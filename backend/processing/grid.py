"""0.5° grid and monthly counts per (cy, cx, year, month, family) (spec §5 Grid, Counts).

cy = floor(lat / 0.5), cx = floor(lon / 0.5), exactly as pipeline/ingest.py (no epsilon: dividing
by 0.5 is exact in binary floating point, and the result must match the existing parquet).
Months are the UTC year and month of `acq_date` (FIRMS acq_date is a UTC date).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .common import FAMILIES, KEY_COLS, jsonable, normalize_keys
from .params import DEFAULT_PARAMS, ProcessingParams

COUNT_COLS = KEY_COLS + ["family", "n", "days", "n_upto", "days_upto"]


def cell_index(deg, cell_deg: float = DEFAULT_PARAMS.cell_deg) -> np.ndarray:
    return np.floor(np.asarray(deg, dtype=float) / cell_deg).astype(np.int64)


def with_keys(detections: pd.DataFrame, params: ProcessingParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Copy of the rows with cy, cx, year, month (int64) and acq_day (datetime64) added."""
    out = detections.copy()
    day = pd.to_datetime(out["acq_date"]).dt.normalize()
    out["cy"] = cell_index(out["latitude"], params.cell_deg)
    out["cx"] = cell_index(out["longitude"], params.cell_deg)
    out["year"] = day.dt.year.astype("int64")
    out["month"] = day.dt.month.astype("int64")
    out["acq_day"] = day
    return out


@dataclass(frozen=True)
class AggregateSummary:
    affected_keys: int | None          # None = every key present in the detections
    groups: int                        # (key, family) rows produced
    detections_by_family: dict[str, int]
    detections_outside_keys: int       # kept detections that fall in no affected key
    split_date: date | None            # n_upto / days_upto count acq_date <= split_date

    def to_dict(self) -> dict:
        return jsonable(self)


def aggregate(kept: pd.DataFrame, affected_keys=None, *, split_date: date | None = None,
              params: ProcessingParams = DEFAULT_PARAMS) -> tuple[pd.DataFrame, AggregateSummary]:
    """Counts per (cy, cx, year, month, family) of filtered detections.

    `n` = detections, `days` = distinct acq_date. `n_upto` / `days_upto` restrict both to
    acq_date <= split_date (all of them when split_date is None); harmonization uses them for the
    month that straddles the S-NPP end. With `affected_keys`, only those cell-months are counted.
    The result's attrs["split_date"] records the split.
    """
    d = with_keys(kept, params) if len(kept) else pd.DataFrame(columns=KEY_COLS + ["family", "acq_day"])
    if affected_keys is not None:
        keys = normalize_keys(affected_keys)
        d = d.astype({c: "int64" for c in KEY_COLS})
        inside = d.merge(keys, on=KEY_COLS, how="left", indicator=True)["_merge"].eq("both").to_numpy()
        outside = int((~inside).sum())
        d = d[inside]
    else:
        keys, outside = None, 0
    if len(d):
        upto = (d["acq_day"] <= pd.Timestamp(split_date)) if split_date is not None else \
            pd.Series(True, index=d.index)
        d = d.assign(_one=1, _upto=upto.astype("int64"), _day_upto=d["acq_day"].where(upto))
        counts = (d.groupby(KEY_COLS + ["family"], sort=True)
                  .agg(n=("_one", "sum"), days=("acq_day", "nunique"),
                       n_upto=("_upto", "sum"), days_upto=("_day_upto", "nunique"))
                  .reset_index())
        counts = counts.astype({c: "int64" for c in KEY_COLS + ["n", "days", "n_upto", "days_upto"]})
    else:
        counts = pd.DataFrame({c: pd.Series(dtype=object if c == "family" else "int64") for c in COUNT_COLS})
    counts = counts[COUNT_COLS].reset_index(drop=True)
    counts.attrs["split_date"] = split_date
    by_family = counts.groupby("family")["n"].sum()
    summary = AggregateSummary(
        affected_keys=None if keys is None else int(len(keys)), groups=int(len(counts)),
        detections_by_family={f: int(by_family.get(f, 0)) for f in FAMILIES},
        detections_outside_keys=outside, split_date=split_date)
    return counts, summary


def to_ingest_cells(counts: pd.DataFrame) -> pd.DataFrame:
    """Counts in pipeline/ingest.py's schema (cy, cx, year, month, sensor, n): MODIS and S-NPP
    (as "VIIRS") only, which is what pipeline/calibrate.py fits on."""
    sel = counts[counts.family.isin(["MODIS", "SNPP"])]
    out = sel.assign(sensor=sel.family.map({"MODIS": "MODIS", "SNPP": "VIIRS"}))
    return out[["cy", "cx", "year", "month", "sensor", "n"]].sort_values(
        ["cy", "cx", "year", "month", "sensor"]).reset_index(drop=True)
