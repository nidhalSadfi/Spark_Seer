"""Chunked batch path for the world archive (spec §16) over the processing functions.

The world SP archive (FIRMS yearly all-countries zips, about 600 million rows) does not fit in memory,
so the history is built one CSV chunk at a time with the SAME functions the live 3-hourly run uses:

  rows -> archive.raw_from_frame -> filters.apply_filters(mask=world mask, boundary=None,
          params=WORLD_PARAMS) -> grid.with_keys -> counts per day -> counts per month

Every filter rule is row-wise, so filtering chunk by chunk keeps exactly the rows a single
`apply_filters` call keeps. The only cross-row quantity is `days` (distinct acq_date); it is
counted from per-day tables, never summed across chunks. The helpers here only reorganise that
work; tests/test_world_build.py checks them against the one-shot functions (oracle):

  mask_tally / mask_from_tally        == mask.build_static_mask(archive.read_firms_csvs(dir))
  daily_counts / monthly_from_daily   == grid.aggregate(kept)
  merge_filter_summaries              == the FilterSummary of one apply_filters call

WORLD_PARAMS is DEFAULT_PARAMS without the 34°N cut (min_lat -90: every valid latitude), which is
what §16 asks for. No country clip: the boundary is None.
"""

from __future__ import annotations

import zipfile
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from .archive import raw_from_frame
from .common import FAMILIES, KEY_COLS, family_series
from .filters import DROP_REASONS, UNKNOWN, FilterSummary, apply_filters
from .grid import COUNT_COLS, with_keys
from .mask import EPS, StaticMask, _content_sha256, _params, square_index
from .params import DEFAULT_PARAMS, ProcessingParams

WORLD_PARAMS = replace(DEFAULT_PARAMS, min_lat=-90.0)
# Columns the filters, the grid, the mask and the natural key need (read_firms_csvs reads all of
# them; the others never reach a count).
READ_COLUMNS = ("latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument", "confidence", "version",
                "type")
# raw_detections' natural key (migration 0001): one stored row per key, so a duplicated key in the
# archive would be one detection for the live system.
NATURAL_KEY = ("satellite", "latitude", "longitude", "acq_date", "acq_time")
DAILY_COLS = KEY_COLS + ["family", "acq_day", "n"]


# -- reading ---------------------------------------------------------------------------------

def archive_files(raw_dir) -> list[Path]:
    """Every *.zip and *.csv under raw_dir (partial downloads, *.part, are not listed)."""
    return sorted(p for p in Path(raw_dir).rglob("*") if p.is_file() and p.suffix.lower() in {".zip", ".csv"})


def _read(source, chunksize: int, columns) -> Iterator[pd.DataFrame]:
    wanted = set(columns)
    reader = pd.read_csv(source, dtype=str, keep_default_na=False, na_values=[""], chunksize=chunksize,
                         usecols=lambda c: c.strip() in wanted)
    for df in reader:
        df.columns = [c.strip() for c in df.columns]
        yield df


def iter_chunks(path, chunksize: int = 500_000, columns=READ_COLUMNS) -> Iterator[tuple[str, pd.DataFrame]]:
    """(member label, chunk of string columns) for a CSV or every CSV inside a zip (streamed), read
    like archive.read_firms_csvs (strings, empty = missing, stripped column names)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        for df in _read(path, chunksize, columns):
            yield path.name, df
        return
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".csv") and not n.endswith("/")):
            with z.open(name) as f:
                for df in _read(f, chunksize, columns):
                    yield name, df


def product_of(chunk: pd.DataFrame) -> np.ndarray:
    """raw_from_frame's `product` (NRT when version ends in NRT, else SP) for string chunks."""
    if "version" not in chunk:
        return np.full(len(chunk), "SP", dtype=object)
    version = chunk["version"].astype("string").str.strip().str.upper()
    return np.where(version.str.endswith("NRT").fillna(False), "NRT", "SP")


def natural_key_hashes(chunk: pd.DataFrame) -> np.ndarray:
    """64-bit hash per row of the natural key (trimmed strings), to count duplicated detections."""
    cols = {c: (chunk[c].astype("string").str.strip() if c in chunk else pd.Series(pd.NA, index=chunk.index,
                                                                                    dtype="string"))
            for c in NATURAL_KEY}
    cols["instrument"] = (chunk["instrument"].astype("string").str.strip().str.upper() if "instrument" in chunk
                          else pd.Series(pd.NA, index=chunk.index, dtype="string"))
    return pd.util.hash_pandas_object(pd.DataFrame(cols), index=False).to_numpy()


# -- static-source mask ----------------------------------------------------------------------

@dataclass
class MaskTally:
    """What mask.build_static_mask needs, summed over chunks."""
    rows: int = 0
    sp_rows: int = 0
    typed_rows: int = 0
    by_type: Counter = field(default_factory=Counter)
    by_family: Counter = field(default_factory=Counter)
    date_min: pd.Timestamp | None = None
    date_max: pd.Timestamp | None = None
    squares: pd.Series = field(default_factory=lambda: pd.Series(dtype="int64"))  # (iy, ix) -> detections

    def add(self, other: "MaskTally") -> "MaskTally":
        self.rows += other.rows
        self.sp_rows += other.sp_rows
        self.typed_rows += other.typed_rows
        self.by_type.update(other.by_type)
        self.by_family.update(other.by_family)
        for name, pick in (("date_min", min), ("date_max", max)):
            mine, theirs = getattr(self, name), getattr(other, name)
            setattr(self, name, theirs if mine is None else mine if theirs is None else pick(mine, theirs))
        if len(other.squares):
            self.squares = other.squares.copy() if not len(self.squares) else \
                self.squares.add(other.squares, fill_value=0).astype("int64")
        return self


def mask_tally(chunk: pd.DataFrame, types=DEFAULT_PARAMS.mask_types,
               res_deg: float = DEFAULT_PARAMS.mask_res_deg) -> MaskTally:
    """build_static_mask's selection (SP rows with type in `types`, nothing else) on one chunk of
    string columns. Only the typed rows go through raw_from_frame."""
    product = product_of(chunk)
    typ_raw = pd.to_numeric(chunk["type"], errors="coerce") if "type" in chunk else pd.Series(np.nan, index=chunk.index)
    pre = typ_raw.isin(list(types)).to_numpy() & (product == "SP")
    t = MaskTally(rows=int(len(chunk)), sp_rows=int((product == "SP").sum()))
    if not pre.any():
        return t
    raw = raw_from_frame(chunk[pre])
    typ = pd.to_numeric(raw["type"], errors="coerce")
    sel = raw[(raw["product"] == "SP").to_numpy() & typ.isin(list(types)).to_numpy()]
    if not len(sel):
        return t
    sel_type = typ[sel.index].astype("int64")
    t.typed_rows = int(len(sel))
    t.by_type = Counter({str(k): int(v) for k, v in sel_type.value_counts().items()})
    t.by_family = Counter({str(k): int(v) for k, v in family_series(sel["source"]).fillna("unknown")
                          .value_counts().items()})
    dates = pd.to_datetime(sel["acq_date"])
    t.date_min, t.date_max = dates.min(), dates.max()
    iy = square_index(pd.to_numeric(sel["latitude"]), res_deg)
    ix = square_index(pd.to_numeric(sel["longitude"]), res_deg)
    t.squares = pd.Series(1, index=pd.MultiIndex.from_arrays([iy, ix])).groupby(level=[0, 1]).sum().astype("int64")
    return t


def mask_from_tally(t: MaskTally, min_count: int = DEFAULT_PARAMS.mask_min_count,
                    res_deg: float = DEFAULT_PARAMS.mask_res_deg, *, types=DEFAULT_PARAMS.mask_types,
                    version: str | None = None) -> StaticMask:
    """The StaticMask build_static_mask returns for the same rows (squares, input summary, version)."""
    if min_count < 1:
        raise ValueError("min_count must be >= 1")
    counts = t.squares
    kept = counts[counts >= min_count] if len(counts) else counts
    squares = tuple((int(a), int(b), int(n)) for (a, b), n in sorted(kept.items()))
    summary = {
        "rows": int(t.rows), "sp_rows": int(t.sp_rows), "typed_rows": int(t.typed_rows),
        "by_type": {str(k): int(t.by_type.get(str(k), 0)) for k in types},
        "by_family": {k: int(v) for k, v in sorted(t.by_family.items())},
        "acq_date_min": t.date_min.date().isoformat() if t.date_min is not None else None,
        "acq_date_max": t.date_max.date().isoformat() if t.date_max is not None else None,
        "candidate_squares": int(len(counts)),
        "detections_in_mask": int(kept.sum()) if len(kept) else 0,
    }
    version = version or f"mask-{_content_sha256(_params(res_deg, min_count, types, EPS), squares)[:12]}"
    return StaticMask(version=version, res_deg=float(res_deg), min_count=int(min_count),
                      types=tuple(int(x) for x in types), squares=squares, input=summary)


# -- filter and counts -----------------------------------------------------------------------

def filter_chunk(chunk: pd.DataFrame, *, mask: StaticMask | None, params: ProcessingParams = WORLD_PARAMS):
    """apply_filters on one chunk, without a country clip (spec §16)."""
    return apply_filters(raw_from_frame(chunk), mask=mask, boundary=None, params=params)


def daily_counts(kept: pd.DataFrame, params: ProcessingParams = WORLD_PARAMS) -> pd.DataFrame:
    """Kept detections per (cy, cx, year, month, family, acq_day): grid.with_keys, then one row per day."""
    if not len(kept):
        return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "acq_day" else object if c == "family"
                                          else "int64") for c in DAILY_COLS})
    d = with_keys(kept, params)
    out = d.groupby(KEY_COLS + ["family", "acq_day"], sort=False).size().rename("n").reset_index()
    return out.astype({c: "int64" for c in KEY_COLS + ["n"]})


def combine_daily(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """Per-day tables of several chunks as one (a day seen in two chunks is summed into one row)."""
    parts = [p for p in parts if len(p)]
    if not parts:
        return daily_counts(pd.DataFrame())
    d = pd.concat(parts, ignore_index=True)
    out = d.groupby(KEY_COLS + ["family", "acq_day"], sort=False)["n"].sum().reset_index()
    return out.astype({c: "int64" for c in KEY_COLS + ["n"]})


def monthly_from_daily(daily: pd.DataFrame, split_date=None) -> pd.DataFrame:
    """grid.aggregate's counts (COUNT_COLS, int64, sorted) from a per-day table: n = detections,
    days = distinct acq_date, n_upto / days_upto restricted to acq_date <= split_date."""
    if not len(daily):
        out = pd.DataFrame({c: pd.Series(dtype=object if c == "family" else "int64") for c in COUNT_COLS})
        out.attrs["split_date"] = split_date
        return out
    upto = (daily["acq_day"] <= pd.Timestamp(split_date)) if split_date is not None else \
        pd.Series(True, index=daily.index)
    d = daily.assign(_day=1, _n_upto=daily["n"].where(upto, 0), _day_upto=upto.astype("int64"))
    out = (d.groupby(KEY_COLS + ["family"], sort=True)
           .agg(n=("n", "sum"), days=("_day", "sum"), n_upto=("_n_upto", "sum"), days_upto=("_day_upto", "sum"))
           .reset_index())
    out = out.astype({c: "int64" for c in KEY_COLS + ["n", "days", "n_upto", "days_upto"]})[COUNT_COLS]
    out = out.reset_index(drop=True)
    out.attrs["split_date"] = split_date
    return out


def merge_filter_summaries(summaries: list[FilterSummary]) -> FilterSummary:
    """The FilterSummary one apply_filters call over all the chunks' rows would return."""
    if not summaries:
        raise ValueError("no filter summaries to merge")
    rules = summaries[0].rules
    if any(s.rules != rules for s in summaries[1:]):
        raise ValueError("the chunks were filtered with different rules")
    order = {f: i for i, f in enumerate((*FAMILIES, UNKNOWN))}
    n = sum(s.input_rows for s in summaries)
    kept = sum(s.kept_rows for s in summaries)
    kept_by_family: Counter = Counter()
    present: set[str] = set()
    for s in summaries:
        kept_by_family.update(s.kept_by_family)
        present |= set(s.kept_by_family)
    dropped = {}
    for r in DROP_REASONS:
        c: Counter = Counter()
        for s in summaries:
            c.update(s.dropped[r])
        dropped[r] = {f: int(c[f]) for f in sorted(c, key=order.get)}
    return FilterSummary(
        input_rows=int(n), kept_rows=int(kept),
        kept_by_family={f: int(kept_by_family[f]) for f in sorted(present, key=order.get)},
        dropped=dropped, dropped_total=int(n - kept),
        drop_share={r: (round(sum(dropped[r].values()) / n, 6) if n else 0.0) for r in DROP_REASONS},
        rules=rules)
