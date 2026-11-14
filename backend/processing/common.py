"""Shared vocabulary: sensor families, cell-month keys, reference rules, JSON conversion."""

from __future__ import annotations

import calendar
import dataclasses
import math
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np
import pandas as pd

KEY_COLS = ["cy", "cx", "year", "month"]
FAMILIES = ("MODIS", "SNPP", "NOAA20", "NOAA21")
VIIRS_FAMILIES = ("SNPP", "NOAA20", "NOAA21")
# FIRMS source id prefix -> sensor family (spec: MODIS_* / VIIRS_SNPP_* / VIIRS_NOAA20_* / VIIRS_NOAA21_*).
SOURCE_PREFIXES = (("MODIS_", "MODIS"), ("VIIRS_SNPP_", "SNPP"), ("VIIRS_NOAA20_", "NOAA20"),
                   ("VIIRS_NOAA21_", "NOAA21"))
FAMILY_PREFIX = {fam: prefix.rstrip("_") for prefix, fam in SOURCE_PREFIXES}

# Value of the `reference` column: where the harmonized value comes from.
REF_MODIS = "MODIS*k"
REF_SNPP = "SNPP"
REF_NOAA20 = "NOAA20"
REF_MIXED = "mixed"
REFERENCES = (REF_MODIS, REF_SNPP, REF_NOAA20, REF_MIXED)

# Row quality flags.
PARTIAL = "partial"                              # month not complete (spec §5)
REFERENCE_PENDING = "reference_pending"          # NOAA-20 needed but no approved r (spec §5, I7)
MIXED_REFERENCE = "mixed_reference"              # S-NPP days up to its end + scaled NOAA-20 after
REFERENCE_UNAVAILABLE = "reference_unavailable"  # the reference sensor has no fetched day this month (I7)
MODIS_UNAVAILABLE = "modis_unavailable"          # MODIS has no fetched day this month (I7)
DRIFT_WARN = "drift_warn"                        # region-month drift above DRIFT_WARN_PCT
ROW_FLAGS = (DRIFT_WARN, MIXED_REFERENCE, MODIS_UNAVAILABLE, PARTIAL, REFERENCE_PENDING, REFERENCE_UNAVAILABLE)


def family_of(source) -> str | None:
    """Sensor family of a FIRMS source id (e.g. VIIRS_NOAA20_NRT -> NOAA20); None if unknown."""
    if not isinstance(source, str):
        return None
    s = source.strip().upper()
    for prefix, fam in SOURCE_PREFIXES:
        if s.startswith(prefix):
            return fam
    return None


def family_series(sources: pd.Series) -> pd.Series:
    uniq = {s: family_of(s) for s in pd.unique(sources)}
    return sources.map(uniq).astype(object)


def as_date(value) -> date:
    """A date from a date, datetime, pandas Timestamp or ISO string."""
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip()[:10])
    raise TypeError(f"cannot read a date from {value!r}")


def utc_date(now) -> date:
    """Today's UTC date for `now` (aware datetimes are converted; naive ones are taken as UTC)."""
    if isinstance(now, pd.Timestamp):
        now = now.to_pydatetime()
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc)
        return now.date()
    return as_date(now)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def normalize_keys(keys) -> pd.DataFrame:
    """Affected keys as a sorted, unique DataFrame [cy, cx, year, month] of int64.

    Accepts a DataFrame with those columns or an iterable of (cy, cx, year, month) tuples.
    """
    if isinstance(keys, pd.DataFrame):
        df = keys[KEY_COLS].copy()
    else:
        df = pd.DataFrame(list(keys), columns=KEY_COLS)
    if df.isna().any().any():
        raise ValueError("affected keys contain nulls")
    df = df.astype("int64")
    if not df.month.between(1, 12).all():
        raise ValueError("affected keys contain a month outside 1..12")
    return df.drop_duplicates().sort_values(KEY_COLS).reset_index(drop=True)


def reference_for(year: int, month: int, snpp_end: date | None, first_viirs_year: int) -> str:
    """Spec §5 harmonized value: MODIS*k before first_viirs_year, then S-NPP while it covers the
    whole month, NOAA-20 for months after snpp_end, `mixed` for the month that contains snpp_end
    (unless snpp_end is that month's last day)."""
    if year < first_viirs_year:
        return REF_MODIS
    start, end = month_bounds(year, month)
    if snpp_end is None or end <= snpp_end:
        return REF_SNPP
    if start > snpp_end:
        return REF_NOAA20
    return REF_MIXED


def expected_ranges(year: int, month: int, snpp_end: date | None,
                    first_viirs_year: int) -> list[tuple[str, date, date]]:
    """(family, first day, last day) each expected family must cover for this month, by calendar.

    MODIS always; the reference family or families by `reference_for`. In the month that contains
    snpp_end, S-NPP is expected up to snpp_end and NOAA-20 from the day after.
    """
    start, end = month_bounds(year, month)
    out = [("MODIS", start, end)]
    ref = reference_for(year, month, snpp_end, first_viirs_year)
    if ref == REF_SNPP:
        out.append(("SNPP", start, end))
    elif ref == REF_NOAA20:
        out.append(("NOAA20", start, end))
    elif ref == REF_MIXED:
        out.append(("SNPP", start, snpp_end))
        out.append(("NOAA20", snpp_end + timedelta(days=1), end))
    return out


def ym(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def key_list(df: pd.DataFrame, limit: int | None = None) -> list[list[int]]:
    """Keys of the rows as [[cy, cx, year, month], ...] (sorted), optionally capped."""
    rows = sorted(map(tuple, df[KEY_COLS].astype("int64").to_numpy().tolist()))
    if limit is not None:
        rows = rows[:limit]
    return [list(r) for r in rows]


def jsonable(obj):
    """Recursively convert to JSON-serialisable Python types (dates as ISO, NaN/NA as None)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable({f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)})
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = sorted(obj) if isinstance(obj, (set, frozenset)) else obj
        return [jsonable(v) for v in items]
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if obj is pd.NA or obj is None:
        return None
    return obj


def counts_by(values: Iterable) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))
