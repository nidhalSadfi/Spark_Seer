"""Shared data access and calendar logic. Routers import from here; nothing loads at import time.

Data source of `get_data()` (spec §10): the approved `cell_month` table when DATABASE_URL is set,
the database is reachable and it has an approved dataset version; otherwise the parquet file.
The database frame is cached per approved dataset_version_id, and that id is re-checked at most
every API_VERSION_CHECK_S seconds (default 30). A failed check is cached for the same time, so an
unreachable database costs one connect timeout per interval, not one per request.

Data path: env EMBER_DATA (default data/harmonized.parquet). The validation report sits next to it
as <name>_validation.json, as written by pipeline/calibrate.py.
"""
import json
import logging
import os
import threading
import time
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
    frame = _approved_frame()
    return frame if frame is not None else _read_parquet(data_path())


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
_db_cache: dict = {"version": None, "frame": None, "checked": None, "url": None}
_clock = time.monotonic  # tests replace it


def reset_cache() -> None:
    with _db_lock:
        _db_cache.update(version=None, frame=None, checked=None, url=None)


def _approved_frame() -> pd.DataFrame | None:
    """The approved cell_month table as the parquet-shaped frame, or None to use the parquet."""
    s = database.settings()
    if not s.database_url:
        return None
    with _db_lock:
        now = _clock()
        fresh = (_db_cache["checked"] is not None and _db_cache["url"] == s.database_url
                 and now - _db_cache["checked"] < s.api_version_check_s)
        if fresh:
            return _db_cache["frame"]
        try:
            with database.read(isolation_level="REPEATABLE READ") as c:  # version and rows from one snapshot
                version = database.scalar(c, "SELECT id FROM dataset_versions WHERE status = 'approved'")
                if version is None:
                    frame = None
                elif version == _db_cache["version"] and _db_cache["frame"] is not None:
                    frame = _db_cache["frame"]
                else:
                    frame = frame_from_rows(database.rows(c, CELL_MONTH_SQL))
                    log.info("calendar data: approved dataset version %s from the database (%d rows)",
                             version, len(frame))
        except database.DatabaseUnavailable as e:
            if _db_cache["frame"] is not None or _db_cache["checked"] is None:
                log.warning("calendar data: %s; serving the parquet file", e)
            version, frame = None, None
        _db_cache.update(version=version if frame is not None else None, frame=frame, checked=now,
                         url=s.database_url)
        return frame


CELL_MONTH_SQL = """
    SELECT m.cy, m.cx, m.year, m.month, m.modis, m.viirs_ref, m.harmonized, m.k, m.support,
           m.modis_days, m.viirs_days, m.reference, m.calibration_version_id, c.k_month AS calibration_k_month
    FROM cell_month AS m JOIN calibration_versions AS c ON c.id = m.calibration_version_id
    ORDER BY m.cy, m.cx, m.year, m.month"""


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
    df = pd.DataFrame(rows, columns=["cy", "cx", "year", "month", "modis", "viirs_ref", "harmonized", "k", "support",
                                     "modis_days", "viirs_days", "reference", "calibration_version_id",
                                     "calibration_k_month"])
    pre = df["year"].astype(int) < FIRST_VIIRS_YEAR
    num = lambda col: pd.to_numeric(df[col], errors="coerce").astype("float64")  # noqa: E731 - None -> NaN
    out = pd.DataFrame({c: df[c].astype(t) for c, t in PARQUET_DTYPES.items()})
    out["MODIS"] = num("modis")
    out["VIIRS"] = num("viirs_ref").mask(pre & df["viirs_ref"].isna(), 0.0)
    out["k"] = num("k")
    out["k_month"] = [float(km[str(int(m))]) if isinstance(km, dict) and str(int(m)) in km else np.nan
                      for km, m in zip(df["calibration_k_month"], df["month"])]
    out["support"] = num("support")
    out["harmonized"] = num("harmonized")
    out["source"] = [None if r is None else ("MODIS*k" if r == "MODIS*k" else "VIIRS") for r in df["reference"]]
    days = {"MODIS_days": num("modis_days"), "VIIRS_days": num("viirs_days").mask(pre & df["viirs_days"].isna(), 0.0)}
    if len(df) and not any(col.isna().any() for col in days.values()):
        for name, col in days.items():
            out[name] = col.astype("int64")  # whole counts, like the parquet
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


def calendar_for_bbox(bbox: tuple[float, float, float, float], start: int | str, end: int | str) -> dict:
    """Monthly harmonized calendar for a bbox (minLon, minLat, maxLon, maxLat), years start..end.

    start/end are years; a "YYYY-MM" string counts as its whole year.
    """
    H = get_data()
    start, end = _year(start), _year(end)
    x0, y0, x1, y1 = bbox
    sel = H[(H.cx >= int(np.floor(x0 / CELL_DEG))) & (H.cx <= int(np.floor(x1 / CELL_DEG)))
            & (H.cy >= int(np.floor(y0 / CELL_DEG))) & (H.cy <= int(np.floor(y1 / CELL_DEG)))
            & H.year.between(start, end)]
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

    # Robust anomaly score vs. the same calendar month in other years (median / MAD).
    g = m.groupby("month").harmonized
    med, mad = g.transform("median"), g.transform(lambda s: (s - s.median()).abs().median())
    m["z"] = ((m.harmonized - med) / (1.4826 * mad.replace(0, np.nan))).fillna(0).round(2)
    # Ignore "anomalies" in near-empty months: tiny counts swing wildly by chance.
    busy = (m.harmonized >= MIN_COUNT) | (med >= MIN_COUNT)
    m["flag"] = np.select([(m.z >= Z_FLAG) & busy, (m.z <= -Z_FLAG) & busy], ["high", "low"], "normal")
    m.loc[(m.year < 2012) & (m.support < MIN_SUPPORT), "flag"] = "low_confidence"

    clim = m.groupby("month").harmonized.median()
    critical = [int(mo) for mo in clim.index if clim[mo] >= clim.quantile(0.75) and clim[mo] > 0]

    out = m.round(1)
    if opt:
        out[opt] = out[opt].astype(object).where(out[opt].notna(), None)  # NaN is not valid JSON
    return {
        "months": out.to_dict(orient="records"),
        "meta": {"cells": int(sel[["cy", "cx"]].drop_duplicates().shape[0]),
                 "critical_months": critical, "validation": get_validation()},
    }
