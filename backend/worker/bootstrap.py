"""`python -m worker.bootstrap`, run from backend/ after `alembic upgrade head` (spec §8 Bootstrap).

Loads approved calibration version 1 and approved dataset version 1 from the existing artefacts,
so the database serves the same numbers as the parquet file:

- calibration 1: k per (cy, cx, calendar month), its support and the regional k per month from
  data/harmonized.parquet (written by pipeline/calibrate.py; the validation report of
  data/harmonized_validation.json scores that k, see spec §15), the approved inter-VIIRS factor
  `approved_r` of data/inter_viirs_fit.json (only the global r is applied, spec §5), S-NPP's end
  date, and the static-source mask version of STATIC_MASK_FILE (or --mask).
- dataset 1: every row of the parquet into cell_month. VIIRS is stored as NULL before 2012 (the
  sensor did not exist, I7; the parquet has 0 there, spec §15). The historical VIIRS series is
  S-NPP only (pipeline/ingest.py), so from 2012 `reference` is SNPP and `n_snpp` = VIIRS.
  NOAA-20/21 counts are unknown (NULL). The archive years are closed: is_partial = false.

Idempotent: if a calibration or dataset version exists it does nothing (exit 0). Everything is one
transaction, so a failure leaves the database as it was. The rows go in with COPY (a world dataset
has millions of cell-months, spec §16).

World dataset (spec §16), with explicit paths:

    python -m worker.bootstrap --data data/world/harmonized.parquet \
        --validation data/world/harmonized_validation.json --inter-viirs data/world/inter_viirs_fit.json \
        --mask data/world/static_mask.json [--replace]

`--replace` (the cut-over, spec §16; needs the user's approval): when versions already exist,
create a NEW approved calibration version and a NEW approved dataset version from the files, in
ONE transaction. The previous approved versions become `superseded` (never deleted), every
approved cell_month row is copied to cell_month_history (replaced_by_version = the new dataset
version) before cell_month is reloaded, and the new dataset version's parent is the old one.
Afterwards set STATIC_MASK_FILE to the new mask (firms_poll refuses a mask that is not the
calibration's, I6). Without versions, --replace is a plain bootstrap.

Exit codes: 0 done or already bootstrapped, 1 database unreachable or not migrated, 2 bad input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from processing.calibration import Calibration
from processing.common import REF_MODIS, REF_SNPP
from processing.inter_viirs import approved_r_from_fit
from processing.mask import load_mask
from processing.params import DEFAULT_PARAMS
from worker import store

log = logging.getLogger("worker.bootstrap")

LABEL = "bootstrap"
REPLACE_LABEL = "bootstrap-replace"
PARQUET_COLUMNS = ("cy", "cx", "year", "month", "MODIS", "VIIRS", "k", "k_month", "support", "harmonized", "source")
SOURCE_REFERENCE = {"MODIS*k": REF_MODIS, "VIIRS": REF_SNPP}


class BootstrapError(ValueError):
    """An input file is missing or not what the bootstrap expects."""


@dataclass(frozen=True)
class BootstrapResult:
    created: bool
    calibration_version_id: int | None
    dataset_version_id: int | None
    rows: int
    message: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_parquet(df: pd.DataFrame) -> None:
    """BootstrapError unless the frame is a harmonized.parquet this module can map."""
    missing = [c for c in PARQUET_COLUMNS if c not in df.columns]
    if missing:
        raise BootstrapError(f"harmonized parquet lacks column(s) {', '.join(missing)}")
    if df.duplicated(["cy", "cx", "year", "month"]).any():
        raise BootstrapError("harmonized parquet has more than one row per (cy, cx, year, month)")
    for col in ("MODIS", "VIIRS"):
        v = df[col].to_numpy(dtype=float)
        if not (np.isfinite(v).all() and (v >= 0).all() and (v == np.round(v)).all()):
            raise BootstrapError(f"harmonized parquet column {col} must hold non-negative whole counts")
    unknown = sorted(set(df["source"]) - set(SOURCE_REFERENCE))
    if unknown:
        raise BootstrapError(f"harmonized parquet has unknown source value(s) {unknown}")
    pre = df["year"].to_numpy() < DEFAULT_PARAMS.first_viirs_year
    if (df.loc[pre, "VIIRS"] != 0).any():
        raise BootstrapError("harmonized parquet has VIIRS counts before 2012")


def parquet_rows(df: pd.DataFrame, calibration_version_id: int) -> list[dict]:
    """harmonized.parquet rows as cell_month rows (see the module docstring for the mapping).
    Row by row; `parquet_frame` is the same mapping, vectorised, for the COPY."""
    check_parquet(df)
    has_days = "MODIS_days" in df.columns and "VIIRS_days" in df.columns
    out = []
    for i, r in enumerate(df.itertuples(index=False)):
        year = int(r.year)
        before = year < DEFAULT_PARAMS.first_viirs_year
        viirs = None if before else int(r.VIIRS)
        modis_days = int(r.MODIS_days) if has_days else None
        viirs_days = None if before or not has_days else int(r.VIIRS_days)
        out.append({
            "cy": int(r.cy), "cx": int(r.cx), "year": year, "month": int(r.month),
            "modis": int(r.MODIS), "viirs_ref": None if viirs is None else float(viirs),
            "harmonized": float(r.harmonized), "k": float(r.k), "support": float(r.support),
            "days": modis_days if before else viirs_days, "modis_days": modis_days, "viirs_days": viirs_days,
            "reference": SOURCE_REFERENCE[r.source], "n_snpp": viirs, "n_noaa20": None, "n_noaa21": None,
            "is_partial": False, "quality_flags": [], "calibration_version_id": calibration_version_id})
    return out


def parquet_frame(df: pd.DataFrame, calibration_version_id: int) -> pd.DataFrame:
    """`parquet_rows` as a frame of store.CELL_COLUMNS (nullable Int64 counts, quality_flags as the
    text[] literal "{}"), vectorised for millions of rows. tests/test_world_scale_pg.py checks it
    against parquet_rows value by value."""
    check_parquet(df)
    has_days = "MODIS_days" in df.columns and "VIIRS_days" in df.columns
    year = df["year"].astype("int64").to_numpy()
    before = year < DEFAULT_PARAMS.first_viirs_year
    viirs = pd.array(df["VIIRS"].to_numpy(dtype=float), dtype="Int64")
    viirs[before] = pd.NA
    none = pd.array([pd.NA] * len(df), dtype="Int64")
    modis_days = pd.array(df["MODIS_days"].to_numpy(dtype=float), dtype="Int64") if has_days else none.copy()
    viirs_days = pd.array(df["VIIRS_days"].to_numpy(dtype=float), dtype="Int64") if has_days else none.copy()
    viirs_days[before] = pd.NA
    days = modis_days.copy()
    days[~before] = viirs_days[~before]
    out = pd.DataFrame({
        "cy": df["cy"].astype("int64").to_numpy(), "cx": df["cx"].astype("int64").to_numpy(), "year": year,
        "month": df["month"].astype("int64").to_numpy(),
        "modis": pd.array(df["MODIS"].to_numpy(dtype=float), dtype="Int64"),
        "viirs_ref": viirs.astype("Float64"),
        "harmonized": df["harmonized"].to_numpy(dtype=float), "k": df["k"].to_numpy(dtype=float),
        "support": df["support"].to_numpy(dtype=float), "days": days, "modis_days": modis_days,
        "viirs_days": viirs_days, "reference": df["source"].map(SOURCE_REFERENCE).to_numpy(dtype=object),
        "n_snpp": viirs, "n_noaa20": none, "n_noaa21": none.copy(), "is_partial": False, "quality_flags": "{}",
        "calibration_version_id": int(calibration_version_id)})
    return out[list(store.CELL_COLUMNS)]


def bootstrap(engine: Engine, *, parquet: Path, validation: Path, inter_viirs: Path, mask: Path | None,
              now: datetime | None = None, replace: bool = False) -> BootstrapResult:
    """Create calibration 1 and dataset 1 in one transaction, or do nothing if versions exist.

    replace=True: if versions exist, create a new approved calibration and dataset version from
    the files instead, superseding the old ones (see the module docstring), in one transaction."""
    now = now or datetime.now(timezone.utc)
    parquet, validation, inter_viirs = Path(parquet), Path(validation), Path(inter_viirs)
    for path in (parquet, validation, inter_viirs, *([Path(mask)] if mask else [])):
        if not path.is_file():
            raise BootstrapError(f"{path} does not exist")
    with engine.begin() as c:
        # One bootstrap at a time; a second one waits here, then sees the versions and stops.
        c.execute(text("LOCK TABLE calibration_versions, dataset_versions IN SHARE ROW EXCLUSIVE MODE"))
        existing = c.execute(text("SELECT (SELECT count(*) FROM calibration_versions) AS cal, "
                                  "(SELECT count(*) FROM dataset_versions) AS ds, "
                                  "(SELECT id FROM dataset_versions WHERE status = 'approved') AS approved")).one()
        replacing = bool(existing.cal or existing.ds)
        if replacing and not replace:
            return BootstrapResult(False, None, existing.approved, 0,
                                   f"already bootstrapped ({existing.cal} calibration and {existing.ds} dataset "
                                   f"version(s); approved dataset version {existing.approved}): nothing done")
        old_cal = c.scalar(text("SELECT id FROM calibration_versions WHERE status = 'approved'")) if replacing else None
        old_ds = existing.approved if replacing else None
        if replacing:
            # One approved version of each at a time (partial unique indexes): supersede first.
            c.execute(text("UPDATE calibration_versions SET status = 'superseded' WHERE status = 'approved'"))
            c.execute(text("UPDATE dataset_versions SET status = 'superseded' WHERE status = 'approved'"))

        report = json.loads(validation.read_text())
        fit = json.loads(inter_viirs.read_text())
        try:
            r_global = approved_r_from_fit(fit)
        except ValueError as e:
            raise BootstrapError(f"{inter_viirs}: {e}") from None
        static_mask = load_mask(mask) if mask else None
        cal = Calibration.from_harmonized_parquet(parquet, version="1", snpp_end=DEFAULT_PARAMS.snpp_end)
        cal = cal.with_global_r(r_global, version="1")
        parquet_sha = _sha256(parquet)
        cal_id = store.insert_calibration(
            c, cal, status="approved", label=REPLACE_LABEL if replacing else LABEL, k_table_ref=f"parquet:{parquet.name} sha256:{parquet_sha}",
            mask_version=static_mask.version if static_mask else None,
            mask_sha256=static_mask.content_sha256 if static_mask else None,
            validation_report={
                "harmonized_validation": report,
                "harmonized_validation_file": {"path": validation.name, "sha256": _sha256(validation)},
                "inter_viirs_fit": fit,
                "inter_viirs_fit_file": {"path": inter_viirs.name, "sha256": _sha256(inter_viirs)},
                "mask": static_mask.summary() if static_mask else None,
                "replaces_calibration_version": old_cal,
                "notes": ["k is the one published in the parquet: pipeline/calibrate.harmonize refits it on "
                          "2012-2021, which includes the validation test years, so it differs from the k "
                          "scored in harmonized_validation (spec §15)",
                          "only the global inter-VIIRS r is applied (spec §5)"]},
            now=now)

        df = pd.read_parquet(parquet)
        frame = parquet_frame(df, cal_id)
        dataset_id = c.scalar(text("""
            INSERT INTO dataset_versions (parent_id, job_id, calibration_version_id, status, created_at, approved_at,
                                          summary)
            VALUES (:parent, NULL, :cal, 'approved', :now, :now, CAST(:summary AS jsonb)) RETURNING id"""),
            {"parent": old_ds, "cal": cal_id, "now": now, "summary": json.dumps({
                "source": "bootstrap_replace" if replacing else "bootstrap", "parquet": parquet.name,
                "parquet_sha256": parquet_sha, "rows": int(len(df)),
                "years": [int(df.year.min()), int(df.year.max())] if len(df) else None,
                "viirs_null_before_2012": int((df.year < DEFAULT_PARAMS.first_viirs_year).sum()),
                **({"replaces_dataset_version": old_ds, "replaces_calibration_version": old_cal}
                   if replacing else {})})})
        del df
        history = 0
        if replacing:
            history = c.execute(text(f"""
                INSERT INTO cell_month_history ({", ".join(store.CELL_COLUMNS)}, dataset_version_id,
                                                replaced_by_version, replaced_at)
                SELECT {", ".join(store.CELL_COLUMNS)}, dataset_version_id, :version, :now FROM cell_month"""),
                {"version": dataset_id, "now": now}).rowcount
            c.execute(text("DELETE FROM cell_month"))
        store.copy_cell_month(c, frame, dataset_id)
        rows = len(frame)
    if replacing:
        return BootstrapResult(True, cal_id, dataset_id, rows,
                               f"replaced calibration version {old_cal} and dataset version {old_ds} (now superseded) "
                               f"with calibration version {cal_id} and dataset version {dataset_id} ({rows} "
                               f"cell-months; {history} previous rows kept in cell_month_history)")
    return BootstrapResult(True, cal_id, dataset_id, rows,
                           f"bootstrapped calibration version {cal_id} and dataset version {dataset_id} "
                           f"({rows} cell-months)")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m worker.bootstrap",
                                description="Load approved calibration 1 and dataset 1 (idempotent), or with "
                                            "--replace a new approved calibration and dataset version.")
    p.add_argument("--parquet", "--data", dest="parquet", type=Path, default=Path("data/harmonized.parquet"),
                   help="harmonized parquet (pipeline/calibrate.py output), e.g. data/world/harmonized.parquet")
    p.add_argument("--validation", type=Path, default=None,
                   help="default: <parquet name>_validation.json next to the parquet")
    p.add_argument("--inter-viirs", type=Path, default=Path("data/inter_viirs_fit.json"))
    p.add_argument("--mask", type=Path, default=None, help="static-source mask JSON (default: STATIC_MASK_FILE)")
    p.add_argument("--replace", action="store_true",
                   help="if versions exist, supersede them with a new approved calibration and dataset version "
                        "built from these files, in one transaction (the world cut-over, spec §16)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from db import make_engine, safe_url
    from settings import Settings, SettingsError

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    try:
        settings = Settings.from_env()
    except SettingsError as e:
        log.error("invalid configuration: %s", e)
        return 2
    if not settings.database_url:
        log.error("DATABASE_URL is not set; export it (see .env.example) and run `alembic upgrade head` first")
        return 2
    mask = args.mask or settings.static_mask_file
    if mask is None:
        log.warning("no static-source mask (STATIC_MASK_FILE or --mask): calibration 1 records none, and "
                    "firms_poll will then refuse to run with a mask file set")
    validation = args.validation or args.parquet.with_name(args.parquet.stem + "_validation.json")
    engine = make_engine(settings.database_url)
    try:
        result = bootstrap(engine, parquet=args.parquet, validation=validation, inter_viirs=args.inter_viirs,
                           mask=mask, replace=args.replace)
    except BootstrapError as e:
        log.error("%s", e)
        return 2
    except OperationalError as e:
        log.error("cannot connect to the database at %s: %s", safe_url(settings.database_url),
                  str(getattr(e, "orig", e)).strip().splitlines()[0])
        return 1
    except ProgrammingError as e:
        log.error("database schema is missing or old (%s); run `alembic upgrade head` from backend/ first",
                  str(getattr(e, "orig", e)).strip().splitlines()[0])
        return 1
    finally:
        engine.dispose()
    log.info(result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
