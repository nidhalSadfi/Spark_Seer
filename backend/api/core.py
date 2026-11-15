"""Shared data access and calendar logic. Routers import from here; nothing loads at import time.

Data source of `get_data()` (spec §10): the approved `cell_month` table when DATABASE_URL is set,
the database is reachable and it has an approved dataset version; otherwise the parquet file.
The database frame is cached per approved dataset_version_id, and that id is re-checked at most
every API_VERSION_CHECK_S seconds (default 30). A failed check is cached for the same time, so an
unreachable database costs one connect timeout per interval, not one per request.

Coverage (spec I7, missing != zero): each /calendar month also says whether the approved dataset
has data for it: "full", "partial" (a near-real-time month that is not complete yet) or "none" (no
row at all for that year-month in the whole approved dataset, or a month after the current UTC
month). The zero-filled counts of a "none" month are unknown, not zero, so the anomaly baseline
leaves "none" and "partial" months out. With the parquet every month is "full", as before.

Data path: env EMBER_DATA (default data/harmonized.parquet). The validation report sits next to it
as <name>_validation.json, as written by pipeline/calibrate.py.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from api import database

AREAS_FILE = Path(__file__).resolve().parent.parent / "data" / "areas.json"  # ships with the code
CELL_DEG = 0.5
MIN_SUPPORT = 30  # MODIS detections behind a ratio before we call it "reliable"
MIN_COUNT = 10    # months below this (and normally below it) are never flagged as anomalies
Z_FLAG = 3.0      # robust z-score threshold; 2 flags too many months on noisy counts
OPTIONAL = ["low", "high", "MODIS_days", "VIIRS_days"]  # passed through only if the parquet has them
DEFAULT_END_YEAR = 2025  # last year of the parquet: /calendar's default `end` without the database


def data_path() -> str:
    """Read at call time, so tests (and scripts) can point EMBER_DATA elsewhere."""
    return os.environ.get("EMBER_DATA", "data/harmonized.parquet")


def validation_path() -> str:
    return data_path().replace(".parquet", "_validation.json")


@lru_cache(maxsize=None)
def _read_parquet(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)


@lru_cache(maxsize=None)
def _read_json(path: str):
    with open(path) as f:
        return json.load(f)


def get_data() -> pd.DataFrame:
    """The harmonized cell x month table (cached; see the module docstring). Do not mutate it."""
    return _data_and_coverage()[0]


def _data_and_coverage() -> tuple[pd.DataFrame, dict | None]:
    """get_data()'s frame and the coverage of the same approved version ({(year, month): "full" |
    "partial"}), or the parquet and None (every month "full")."""
    frame, coverage = _approved()
    if frame is None:
        return _read_parquet(data_path()), None
    return frame, coverage


def default_end_year() -> int:
    """/calendar's default `end` (also /health's data_end_year): the last year with data in the
    approved dataset, never before 2025; 2025 with the parquet, as before."""
    frame = _approved_frame()
    if frame is None or frame.empty:
        return DEFAULT_END_YEAR
    return max(DEFAULT_END_YEAR, int(frame["year"].max()))


def data_source() -> dict:
    """Where get_data() reads from right now: {"data_source": "database"|"parquet", "dataset_version_id"}."""
    frame = _approved_frame()
    if frame is None:
        return {"data_source": "parquet", "dataset_version_id": None}
    return {"data_source": "database", "dataset_version_id": _db_cache["version"]}


# ----------------------------------------------------------------------------- approved cell_month

log = logging.getLogger("uvicorn.error")
PARQUET_DTYPES = {"cy": "int32", "cx": "int32", "year": "int16", "month": "int8"}  # as pipeline/calibrate.py writes
FIRST_VIIRS_YEAR = 2012
_db_lock = threading.Lock()
_db_cache: dict = {"version": None, "frame": None, "coverage": None, "checked": None, "url": None}
_clock = time.monotonic  # tests replace it
_utcnow = lambda: datetime.now(timezone.utc)  # noqa: E731 - tests replace it (the current UTC month)


def reset_cache() -> None:
    with _db_lock:
        _db_cache.update(version=None, frame=None, coverage=None, checked=None, url=None)


def _approved_frame() -> pd.DataFrame | None:
    """The approved cell_month table as the parquet-shaped frame, or None to use the parquet."""
    return _approved()[0]


def _approved() -> tuple[pd.DataFrame | None, dict | None]:
    """(frame, coverage) of the approved dataset version, read from one snapshot, or (None, None)."""
    s = database.settings()
    if not s.database_url:
        return None, None
    with _db_lock:
        now = _clock()
        fresh = (_db_cache["checked"] is not None and _db_cache["url"] == s.database_url
                 and now - _db_cache["checked"] < s.api_version_check_s)
        if fresh:
            return _db_cache["frame"], _db_cache["coverage"]
        coverage = None
        try:
            with database.read(isolation_level="REPEATABLE READ") as c:  # version and rows from one snapshot
                version = database.scalar(c, "SELECT id FROM dataset_versions WHERE status = 'approved'")
                if version is None:
                    frame = None
                elif version == _db_cache["version"] and _db_cache["frame"] is not None:
                    frame, coverage = _db_cache["frame"], _db_cache["coverage"]
                else:
                    frame = frame_from_rows(cell_month_frame(c))
                    coverage = coverage_from_rows(database.rows(c, COVERAGE_SQL))
                    log.info("calendar data: approved dataset version %s from the database (%d rows)",
                             version, len(frame))
        except database.DatabaseUnavailable as e:
            if _db_cache["frame"] is not None or _db_cache["checked"] is None:
                log.warning("calendar data: %s; serving the parquet file", e)
            version, frame = None, None
        if frame is None:
            coverage = None
        _db_cache.update(version=version if frame is not None else None, frame=frame, coverage=coverage,
                         checked=now, url=s.database_url)
        return frame, coverage


CELL_MONTH_SQL = """
    SELECT m.cy, m.cx, m.year, m.month, m.modis, m.viirs_ref, m.harmonized, m.k, m.support,
           m.modis_days, m.viirs_days, m.reference, m.calibration_version_id, c.k_month AS calibration_k_month
    FROM cell_month AS m JOIN calibration_versions AS c ON c.id = m.calibration_version_id
    ORDER BY m.cy, m.cx, m.year, m.month"""
CELL_MONTH_COLUMNS = ["cy", "cx", "year", "month", "modis", "viirs_ref", "harmonized", "k", "support", "modis_days",
                      "viirs_days", "reference", "calibration_version_id", "calibration_k_month"]

# CELL_MONTH_SQL without the per-row JSON, streamed with COPY (a world dataset has millions of
# rows, spec §16); the k_month of each calibration version is read once and shared by its rows.
_CELL_MONTH_COPY_SQL = """
    SELECT cy, cx, year, month, modis, viirs_ref, harmonized, k, support, modis_days, viirs_days, reference,
           calibration_version_id
    FROM cell_month ORDER BY cy, cx, year, month"""


def _cell_month_types():
    import pyarrow as pa

    return {"cy": pa.int32(), "cx": pa.int32(), "year": pa.int16(), "month": pa.int8(), "modis": pa.float64(),
            "viirs_ref": pa.float64(), "harmonized": pa.float64(), "k": pa.float64(), "support": pa.float64(),
            "modis_days": pa.float64(), "viirs_days": pa.float64(), "reference": pa.string(),
            "calibration_version_id": pa.int64()}


def cell_month_frame(conn) -> pd.DataFrame:
    """CELL_MONTH_SQL's result as a frame (same columns, same order), read with COPY. Counts come
    as float64 (NULL = NaN); every row's calibration_k_month is its version's k_month dict."""
    import pgcopy

    df = pgcopy.read_frame(conn, _CELL_MONTH_COPY_SQL, None, _cell_month_types())
    kms = {int(r["id"]): r["k_month"] for r in database.rows(conn, "SELECT id, k_month FROM calibration_versions")}
    df["calibration_k_month"] = df["calibration_version_id"].map(kms)
    return df[CELL_MONTH_COLUMNS]


# One row per year-month the approved dataset has at all; partial if any of its rows is partial.
COVERAGE_SQL = """
    SELECT year, month, bool_or(is_partial) AS partial FROM cell_month GROUP BY year, month"""


def coverage_from_rows(rows: list[dict]) -> dict:
    """COVERAGE_SQL rows -> {(year, month): "partial" | "full"}; a year-month not in it is "none"."""
    return {(int(r["year"]), int(r["month"])): "partial" if r["partial"] else "full" for r in rows}


def month_coverage(years, months, coverage: dict | None, now: datetime | None = None) -> list[str]:
    """Coverage of each (year, month): "full" for every month without coverage information (the
    parquet); otherwise "none" after the current UTC month or without any approved row, else the
    approved dataset's "full" / "partial"."""
    if coverage is None:
        return ["full"] * len(years)
    now = now or _utcnow()
    current = (now.year, now.month)
    return ["none" if (int(y), int(m)) > current else coverage.get((int(y), int(m)), "none")
            for y, m in zip(years, months)]


def frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    """cell_month rows -> the frame pipeline/calibrate.py writes (same columns, dtypes and values).

    - MODIS = modis, VIIRS = viirs_ref, harmonized, k, support; NULL stays NaN (the calendar sums
      skip it, as before).
    - VIIRS (and VIIRS_days) before 2012 is NULL in the database (the sensor did not exist, I7)
      and 0 in the parquet; it is 0 here, so /calendar's viirs_raw keeps its contract (spec §15).
    - k_month: the calibration version's regional k for that calendar month.
    - source: "MODIS*k" for reference MODIS*k, "VIIRS" for any VIIRS reference.
    - MODIS_days / VIIRS_days only when every row has them, i.e. when the bootstrapped parquet had
      them (calibrate.py writes them optionally). A database bootstrapped without them (the real
      one) never gains these /calendar fields, even after runs that store day counts for new rows.
    """
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows, columns=CELL_MONTH_COLUMNS)
    pre = (df["year"].astype(int) < FIRST_VIIRS_YEAR).to_numpy()
    num = lambda col: pd.to_numeric(df[col], errors="coerce").astype("float64")  # noqa: E731 - None -> NaN
    out = pd.DataFrame({c: df[c].astype(t) for c, t in PARQUET_DTYPES.items()})
    out["MODIS"] = num("modis")
    out["VIIRS"] = num("viirs_ref").mask(pre & df["viirs_ref"].isna(), 0.0)
    out["k"] = num("k")
    out["k_month"] = _k_month(df)
    out["support"] = num("support")
    out["harmonized"] = num("harmonized")
    ref = df["reference"]
    out["source"] = np.where(ref.isna(), None, np.where(ref == "MODIS*k", "MODIS*k", "VIIRS")).astype(object)
    days = {"MODIS_days": num("modis_days"), "VIIRS_days": num("viirs_days").mask(pre & df["viirs_days"].isna(), 0.0)}
    if len(df) and not any(col.isna().any() for col in days.values()):
        for name, col in days.items():
            out[name] = col.astype("int64")  # whole counts, like the parquet
    return out


def _k_month(df: pd.DataFrame) -> np.ndarray:
    """Each row's calibration k_month for its calendar month (NaN where it has none), vectorised:
    k_month depends only on the calibration version (it is joined from calibration_versions)."""
    out = np.full(len(df), np.nan)
    if not len(df):
        return out
    month = df["month"].astype("int64").to_numpy()
    first = df.drop_duplicates("calibration_version_id")
    for cal_id, km in zip(first["calibration_version_id"], first["calibration_k_month"]):
        if not isinstance(km, dict):
            continue
        rows = (df["calibration_version_id"] == cal_id).to_numpy()
        table = np.full(13, np.nan)
        for m in range(1, 13):
            if str(m) in km:
                table[m] = float(km[str(m)])
        valid = rows & (month >= 1) & (month <= 12)
        out[valid] = table[month[valid]]
    return out


def get_validation(include_series: bool = False) -> dict:
    """Held-out validation report written by pipeline/calibrate.py.

    A "series" (per-month detail), if the file has one, is left out unless asked for, so that
    /calendar's meta stays small.
    """
    report = dict(_read_json(validation_path()))
    if not include_series:
        report.pop("series", None)
    return report


def get_areas() -> list[dict]:
    """Named demo areas: [{"id", "name", "bbox": [minLon, minLat, maxLon, maxLat]}, ...]."""
    return _read_json(str(AREAS_FILE))


def get_area(area_id: str) -> dict | None:
    return next((a for a in get_areas() if a["id"] == area_id), None)


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """"minLon,minLat,maxLon,maxLat" -> a tuple; ValueError (with the 400 message) when malformed."""
    try:
        x0, y0, x1, y1 = map(float, raw.split(","))
    except ValueError:
        raise ValueError("bbox must be minLon,minLat,maxLon,maxLat") from None
    if not (x0 <= x1 and y0 <= y1):  # also catches NaN; such a box would silently select nothing
        raise ValueError("bbox needs minLon <= maxLon and minLat <= maxLat; "
                         "a box across the antimeridian (180 degrees) must be sent as two boxes")
    return (x0, y0, x1, y1)


def _year(value) -> int:
    """2001, "2001" or "2001-01" -> 2001. api/passport.py passes "YYYY-MM" strings."""
    return int(str(value)[:4])


def select_bbox(H: pd.DataFrame, bbox: tuple[float, float, float, float], start: int, end: int) -> pd.DataFrame:
    """Rows of H whose cell is in the bbox (cell rule floor(deg / CELL_DEG)) and year in start..end,
    in H's order. When H is sorted by cy (the database frame is), only the cy range is scanned: a
    world-wide frame has millions of rows (spec §16), a small bbox a few hundred."""
    x0, y0, x1, y1 = bbox
    cx0, cx1 = int(np.floor(x0 / CELL_DEG)), int(np.floor(x1 / CELL_DEG))
    cy0, cy1 = int(np.floor(y0 / CELL_DEG)), int(np.floor(y1 / CELL_DEG))
    part = H
    cy = H["cy"]
    if len(H) and cy.is_monotonic_increasing:
        values = cy.to_numpy()
        part = H.iloc[np.searchsorted(values, cy0, "left"):np.searchsorted(values, cy1, "right")]
    return part[(part.cx >= cx0) & (part.cx <= cx1) & (part.cy >= cy0) & (part.cy <= cy1)
                & part.year.between(start, end)]


def calendar_for_bbox(bbox: tuple[float, float, float, float], start: int | str, end: int | str) -> dict:
    """Monthly harmonized calendar for a bbox (minLon, minLat, maxLon, maxLat), years start..end.

    start/end are years; a "YYYY-MM" string counts as its whole year. Each month carries
    `coverage` (see the module docstring). A "none" month keeps its zero-filled counts, but the
    anomaly baseline (z, flag, critical_months) is computed from "full" months only, so unknown
    months never enter a median as zeros (I7). With the parquet every month is "full": unchanged.
    """
    H, coverage = _data_and_coverage()
    start, end = _year(start), _year(end)
    sel = select_bbox(H, bbox, start, end)
    if sel.empty:
        return {"months": [], "meta": {"cells": 0, "note": "no detections in this area"}}

    opt = [c for c in OPTIONAL if c in sel.columns]
    m = sel.groupby(["year", "month"]).agg(
        modis_raw=("MODIS", "sum"), viirs_raw=("VIIRS", "sum"),
        harmonized=("harmonized", "sum"),
        # Optional columns are summed over cells too, but stay null if any cell lacks the value.
        **{c: (c, lambda s: s.sum(skipna=False)) for c in opt}).reset_index()
    # Support = training MODIS detections behind this area's ratio, per calendar month.
    support = sel.drop_duplicates(["cy", "cx", "month"]).groupby("month").support.sum()

    # Fill missing months with zeros so the calendar grid is complete.
    full = pd.MultiIndex.from_product([range(start, end + 1), range(1, 13)], names=["year", "month"])
    m = m.set_index(["year", "month"]).reindex(full, fill_value=0).reset_index()
    m["support"] = m.month.map(support).fillna(0)

    # The baseline uses "full" months only (I7): the zero-filled counts of a "none" month are
    # unknown, and a partial month is incomplete. With the parquet every month is "full".
    coverage_col = month_coverage(m.year, m.month, coverage)
    baseline = m.harmonized.where(pd.Series(coverage_col, index=m.index) == "full")

    # Robust anomaly score vs. the same calendar month in other years (median / MAD).
    g = baseline.groupby(m.month)
    med, mad = g.transform("median"), g.transform(lambda s: (s - s.median()).abs().median())
    m["z"] = ((m.harmonized - med) / (1.4826 * mad.replace(0, np.nan))).fillna(0).round(2)
    # Ignore "anomalies" in near-empty months: tiny counts swing wildly by chance.
    busy = (m.harmonized >= MIN_COUNT) | (med >= MIN_COUNT)
    m["flag"] = np.select([(m.z >= Z_FLAG) & busy, (m.z <= -Z_FLAG) & busy], ["high", "low"], "normal")
    m.loc[(m.year < 2012) & (m.support < MIN_SUPPORT), "flag"] = "low_confidence"

    clim = baseline.groupby(m.month).median()
    critical = [int(mo) for mo in clim.index if clim[mo] >= clim.quantile(0.75) and clim[mo] > 0]

    m["coverage"] = coverage_col

    out = m.round(1)
    if opt:
        out[opt] = out[opt].astype(object).where(out[opt].notna(), None)  # NaN is not valid JSON
    return {
        "months": out.to_dict(orient="records"),
        "meta": {"cells": int(sel[["cy", "cx"]].drop_duplicates().shape[0]),
                 "critical_months": critical, "validation": get_validation()},
    }
