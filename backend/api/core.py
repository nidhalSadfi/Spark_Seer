"""Shared data access and calendar logic. Routers import from here; nothing loads at import time.

Data path: env EMBER_DATA (default data/harmonized.parquet). The validation report sits next to it
as <name>_validation.json, as written by pipeline/calibrate.py.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

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
    """The harmonized cell x month table (loaded once per path, then cached). Do not mutate it."""
    return _read_parquet(data_path())


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
