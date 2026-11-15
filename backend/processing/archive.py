"""Read FIRMS archive CSVs (e.g. yearly country files) into a raw_detections-shaped DataFrame.

Like pipeline/ingest.py, every `*.csv` under the directory is read and the sensor comes from the
`instrument` and `satellite` columns, not the file name. The S-NPP satellite codes are the ones
ingest.py accepts. `product` is NRT when `version` ends in "NRT" (FIRMS NRT versions look like
"2.0NRT"), SP otherwise.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .common import FAMILY_PREFIX

RAW_COLUMNS = ["source", "product", "satellite", "latitude", "longitude", "acq_date", "acq_time",
               "confidence", "type", "instrument", "version"]
SNPP_SATELLITES = frozenset({"N", "NPP", "SUOMI NPP", "S-NPP"})  # = pipeline/ingest.py
NOAA20_SATELLITES = frozenset({"N20", "NOAA-20", "NOAA20", "J1", "JPSS-1", "JPSS1", "1"})
NOAA21_SATELLITES = frozenset({"N21", "NOAA-21", "NOAA21", "J2", "JPSS-2", "JPSS2", "2"})


def csv_files(raw_dir) -> list[Path]:
    return sorted(p for p in Path(raw_dir).rglob("*.csv") if p.is_file())


def family_from_columns(instrument: pd.Series, satellite: pd.Series) -> pd.Series:
    inst = instrument.astype("string").str.strip().str.upper()
    sat = satellite.astype("string").str.strip().str.upper()
    fam = np.select(
        [inst.eq("MODIS").fillna(False).to_numpy(),
         (inst.eq("VIIRS") & sat.isin(SNPP_SATELLITES)).fillna(False).to_numpy(),
         (inst.eq("VIIRS") & sat.isin(NOAA20_SATELLITES)).fillna(False).to_numpy(),
         (inst.eq("VIIRS") & sat.isin(NOAA21_SATELLITES)).fillna(False).to_numpy()],
        ["MODIS", "SNPP", "NOAA20", "NOAA21"], default="")
    return pd.Series(fam, index=instrument.index).replace("", None).astype(object)


def read_firms_csvs(raw_dir) -> pd.DataFrame:
    """All archive rows under raw_dir. Rows of an unknown sensor get source None (the filter
    drops them as `unknown_source`)."""
    frames = []
    for path in csv_files(raw_dir):
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        df.columns = [c.strip() for c in df.columns]
        frames.append(df)
    if not frames:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in RAW_COLUMNS})
    return raw_from_frame(pd.concat(frames, ignore_index=True, sort=False))


def raw_from_frame(df: pd.DataFrame) -> pd.DataFrame:
    """One table of archive rows (read as strings, stripped column names) in raw_detections shape.

    `read_firms_csvs` is this over the concatenation of every file; processing.world calls it on
    one CSV chunk at a time. Row-wise (FIRMS dates are all ISO), so chunking never changes a row's result.
    """
    missing = [c for c in ("satellite", "instrument", "confidence", "version", "type", "acq_time") if c not in df]
    if missing:  # a new frame, so the caller's table is not changed
        df = df.assign(**{c: None for c in missing})
    fam = family_from_columns(df["instrument"], df["satellite"])
    version = df["version"].astype("string").str.strip().str.upper()
    product = np.where(version.str.endswith("NRT").fillna(False), "NRT", "SP")
    out = pd.DataFrame({
        "source": [f"{FAMILY_PREFIX[f]}_{p}" if f is not None else None for f, p in zip(fam, product)],
        "product": product,
        "satellite": df["satellite"].astype("string").str.strip().astype(object),
        "latitude": pd.to_numeric(df["latitude"]).astype(float),
        "longitude": pd.to_numeric(df["longitude"]).astype(float),
        "acq_date": pd.to_datetime(df["acq_date"].str.strip()).dt.date,
        "acq_time": df["acq_time"].astype("string").str.strip().astype(object),
        "confidence": df["confidence"].astype("string").str.strip().astype(object),
        "type": pd.to_numeric(df["type"], errors="coerce").astype("Int64"),
        "instrument": df["instrument"].astype("string").str.strip().astype(object),
        "version": df["version"].astype("string").str.strip().astype(object),
    })
    return out
