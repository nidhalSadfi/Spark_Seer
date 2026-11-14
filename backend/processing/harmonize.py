"""Harmonized value per affected cell-month (spec §5) with the frozen calibration (I6).

  year < 2012                     harmonized = MODIS x k(cy, cx, calendar month)   reference MODIS*k
  from 2012, S-NPP covers month   harmonized = S-NPP count                         reference SNPP
  month after snpp_end            harmonized = NOAA-20 count x r[month]            reference NOAA20
  month containing snpp_end       S-NPP days <= end + r x NOAA-20 days > end       reference mixed
NOAA-21 never contributes. viirs_ref is the reference value in S-NPP terms (null before 2012).

Missing is not zero (I7): a family with no covered day in the month gets a null count; a value
that needs it is null with a flag (`modis_unavailable`, `reference_unavailable`). Without an
approved r, values that need NOAA-20 are null with `reference_pending`, never 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .calibration import Calibration, keys_with_k
from .common import (FAMILIES, KEY_COLS, MIXED_REFERENCE, MODIS_UNAVAILABLE, REF_MIXED, REF_MODIS, REF_NOAA20,
                     REF_SNPP, REFERENCE_PENDING, REFERENCE_UNAVAILABLE, ROW_FLAGS, counts_by, expected_ranges,
                     jsonable, month_bounds, normalize_keys, reference_for, ym)
from .coverage import Coverage, as_coverage
from .params import DEFAULT_PARAMS, ProcessingParams

STAGED_COLUMNS = ["cy", "cx", "year", "month", "modis", "viirs_ref", "harmonized", "k", "support", "modis_days",
                  "viirs_days", "reference", "n_snpp", "n_noaa20", "n_noaa21", "is_partial", "quality_flags",
                  "calibration_version"]
COUNT_COLUMNS = ["modis", "n_snpp", "n_noaa20", "n_noaa21"]  # raw filtered detections per family
RAW_COUNT_COLUMN = {"MODIS": "modis", "SNPP": "n_snpp", "NOAA20": "n_noaa20", "NOAA21": "n_noaa21"}


@dataclass(frozen=True)
class HarmonizeSummary:
    rows: int
    calibration_version: str
    r_status: str
    snpp_end: date | None
    by_reference: dict[str, int]
    flags: dict[str, int]
    null_harmonized: int
    months: dict[str, dict]  # "YYYY-MM" -> reference, covered days per family

    def to_dict(self) -> dict:
        return jsonable(self)


def month_table(months: pd.DataFrame, coverage: Coverage, calibration: Calibration,
                params: ProcessingParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Per (year, month): reference, whether each family has any covered day in the month
    (`cov_<FAMILY>`), whether the reference's own days are covered (`ref_covered`), and r."""
    rows = []
    for year, month in months[["year", "month"]].drop_duplicates().itertuples(index=False):
        start, end = month_bounds(int(year), int(month))
        ref = reference_for(int(year), int(month), calibration.snpp_end, params.first_viirs_year)
        row = {"year": int(year), "month": int(month), "reference": ref,
               **{f"cov_{f}": coverage.covered_days(f, start, end) > 0 for f in FAMILIES}}
        needed = [r for r in expected_ranges(int(year), int(month), calibration.snpp_end, params.first_viirs_year)
                  if r[0] != "MODIS"]
        row["ref_covered"] = all(coverage.covered_days(f, a, b) > 0 for f, a, b in needed)
        r = calibration.r_for(int(month)) if ref in (REF_NOAA20, REF_MIXED) else None
        row["r"] = np.nan if r is None else r
        rows.append(row)
    cols = ["year", "month", "reference", *[f"cov_{f}" for f in FAMILIES], "ref_covered", "r"]
    return pd.DataFrame(rows, columns=cols).astype({"year": "int64", "month": "int64"})


def harmonize(counts: pd.DataFrame, affected_keys, calibration: Calibration, coverage,
              params: ProcessingParams = DEFAULT_PARAMS) -> tuple[pd.DataFrame, HarmonizeSummary]:
    """One row per affected key (STAGED_COLUMNS; is_partial is left null for completeness).

    `counts` comes from grid.aggregate(..., split_date=calibration.snpp_end).
    """
    coverage = as_coverage(coverage)
    keys = normalize_keys(affected_keys)
    out = keys_with_k(keys, calibration)
    for fam in FAMILIES:
        sub = counts.loc[counts.family == fam, KEY_COLS + ["n", "days", "n_upto", "days_upto"]]
        sub = sub.rename(columns={c: f"{c}_{fam}" for c in ("n", "days", "n_upto", "days_upto")})
        out = out.merge(sub.astype({c: "int64" for c in KEY_COLS}), on=KEY_COLS, how="left")
    raw = [f"{c}_{fam}" for fam in FAMILIES for c in ("n", "days", "n_upto", "days_upto")]
    out[raw] = out[raw].fillna(0).astype("int64")
    info = month_table(out, coverage, calibration, params)
    out = out.merge(info, on=["year", "month"], how="left")
    if (out.reference == REF_MIXED).any() and counts.attrs.get("split_date") != calibration.snpp_end:
        raise ValueError("a month straddles snpp_end: aggregate the counts with split_date=calibration.snpp_end "
                         f"(got split_date={counts.attrs.get('split_date')!r})")

    def known(values: pd.Series, covered: pd.Series) -> pd.Series:
        return values.astype("Int64").where(covered.to_numpy(), pd.NA)

    ref = out.reference
    pre = ref == REF_MODIS
    staged = out[KEY_COLS].copy()
    staged["modis"] = known(out.n_MODIS, out.cov_MODIS)
    # Reference value in S-NPP terms and its days.
    snpp_val = out.n_SNPP.astype(float)
    noaa_val = out.n_NOAA20 * out.r
    mixed_val = out.n_upto_SNPP + out.r * (out.n_NOAA20 - out.n_upto_NOAA20)
    ref_val = np.select([ref == REF_SNPP, ref == REF_NOAA20, ref == REF_MIXED], [snpp_val, noaa_val, mixed_val],
                        default=np.nan)
    ref_days = np.select([ref == REF_SNPP, ref == REF_NOAA20, ref == REF_MIXED],
                         [out.days_SNPP, out.days_NOAA20, out.days_upto_SNPP + out.days_NOAA20 - out.days_upto_NOAA20],
                         default=0)
    needs_r = ref.isin([REF_NOAA20, REF_MIXED])
    r_missing = needs_r & out.r.isna()
    ref_missing = ~pre & ~out.ref_covered
    ref_val = pd.Series(ref_val, index=out.index).where(~(pre | ref_missing | r_missing))
    staged["viirs_ref"] = ref_val.astype(float)
    modis_k = staged["modis"].astype(float) * out.k
    staged["harmonized"] = np.where(pre, modis_k, ref_val).astype(float)
    staged["k"] = out.k.astype(float)
    staged["support"] = out.support.astype(float)
    staged["modis_days"] = known(out.days_MODIS, out.cov_MODIS)
    staged["viirs_days"] = pd.Series(ref_days, index=out.index).astype("Int64").where((~pre & out.ref_covered)
                                                                                      .to_numpy(), pd.NA)
    staged["reference"] = ref.astype(object)
    staged["n_snpp"] = known(out.n_SNPP, out.cov_SNPP)
    staged["n_noaa20"] = known(out.n_NOAA20, out.cov_NOAA20)
    staged["n_noaa21"] = known(out.n_NOAA21, out.cov_NOAA21)
    staged["is_partial"] = pd.array([pd.NA] * len(out), dtype="boolean")
    flags = {
        MODIS_UNAVAILABLE: staged["modis"].isna().to_numpy(),
        REFERENCE_UNAVAILABLE: ref_missing.to_numpy(),
        REFERENCE_PENDING: r_missing.to_numpy(),
        MIXED_REFERENCE: (ref == REF_MIXED).to_numpy(),
    }
    staged["quality_flags"] = row_flags(flags, len(out))
    staged["calibration_version"] = calibration.version
    staged = staged[STAGED_COLUMNS]

    months = {ym(row.year, row.month): {"reference": row.reference, "r": None if np.isnan(row.r) else row.r,
                                        "covered_days": {f: coverage.covered_days(f, *month_bounds(row.year, row.month))
                                                         for f in FAMILIES}}
              for row in info.itertuples(index=False)}
    summary = HarmonizeSummary(
        rows=int(len(staged)), calibration_version=calibration.version, r_status=calibration.r_status,
        snpp_end=calibration.snpp_end, by_reference=counts_by(staged.reference),
        flags={f: int(v.sum()) for f, v in sorted(flags.items())},
        null_harmonized=int(staged.harmonized.isna().sum()), months=dict(sorted(months.items())))
    return staged, summary


def row_flags(flags: dict[str, np.ndarray], n: int) -> list[list[str]]:
    """Sorted flag lists per row from {flag: bool array}."""
    names = [f for f in ROW_FLAGS if f in flags]
    arrays = [np.asarray(flags[f], dtype=bool) for f in names]
    return [[f for f, a in zip(names, arrays) if a[i]] for i in range(n)]


def add_flag(staged: pd.DataFrame, flag: str, where) -> pd.DataFrame:
    """Copy of staged with `flag` added (once, kept sorted in ROW_FLAGS order) where `where` is True."""
    order = {f: i for i, f in enumerate(ROW_FLAGS)}
    where = np.asarray(where, dtype=bool)
    out = staged.copy()
    out["quality_flags"] = [sorted(set(fl) | {flag}, key=order.get) if w else list(fl)
                            for fl, w in zip(staged["quality_flags"], where)]
    return out
