"""Hard gates G1-G4 (spec §6; any failure blocks publication) and the soft-flag list.

Each gate re-derives what it checks from its inputs instead of trusting the stage that produced
the staged rows: G2 recounts the filtered detections, G3 re-derives the reference and coverage
from the calendar and the fetch coverage, G4 re-looks-up k in the calibration passed in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from .calibration import Calibration
from .common import (FAMILIES, KEY_COLS, MODIS_UNAVAILABLE, REF_MIXED, REF_MODIS, REF_NOAA20, REFERENCE_PENDING,
                     REFERENCE_UNAVAILABLE, REFERENCES, ROW_FLAGS, jsonable, key_list, month_bounds, normalize_keys,
                     reference_for, reference_ranges)
from .coverage import as_coverage
from .drift import DriftSummary
from .filters import FilterSummary
from .grid import with_keys
from .harmonize import RAW_COUNT_COLUMN, STAGED_COLUMNS
from .params import DEFAULT_PARAMS, ProcessingParams

SAMPLE = 20  # keys listed per problem or flag; the counts are always complete


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: dict

    def to_dict(self) -> dict:
        return jsonable(self)


def _result(name: str, problems: dict[str, pd.DataFrame | int], extra: dict | None = None) -> GateResult:
    detail = dict(extra or {})
    failures = {}
    for what, rows in problems.items():
        n = rows if isinstance(rows, int) else len(rows)
        if n:
            failures[what] = {"rows": int(n)}
            if isinstance(rows, pd.DataFrame) and set(KEY_COLS) <= set(rows.columns):
                failures[what]["keys"] = key_list(rows, SAMPLE)
    detail["failures"] = failures
    return GateResult(name=name, passed=not failures, detail=detail)


def gate_schema(staged: pd.DataFrame, affected_keys) -> GateResult:
    """G1: counts are non-negative integers; keys are unique; every affected key appears exactly once."""
    missing_cols = [c for c in STAGED_COLUMNS if c not in staged.columns]
    if missing_cols:
        return GateResult("G1_schema_range", False, {"failures": {"missing_columns": missing_cols}})
    problems: dict[str, pd.DataFrame | int] = {}
    keys_ok = staged[KEY_COLS].notna().all(axis=1)
    problems["null_key"] = staged[~keys_ok]
    s = staged[keys_ok]
    problems["non_integer_key"] = s[~s[KEY_COLS].apply(lambda c: _is_int(c)).all(axis=1)]
    problems["duplicate_key"] = s[s.duplicated(KEY_COLS, keep=False)]
    expected = normalize_keys(affected_keys)
    got = s[KEY_COLS].astype("int64").drop_duplicates()
    both = expected.merge(got, on=KEY_COLS, how="outer", indicator=True)
    problems["affected_key_missing"] = both[both._merge == "left_only"]
    problems["unexpected_key"] = both[both._merge == "right_only"]
    days_in_month = s.apply(lambda r: month_bounds(int(r.year), int(r.month))[1].day, axis=1) if len(s) else \
        pd.Series(dtype=int)
    for col in ("modis", "n_snpp", "n_noaa20", "n_noaa21", "modis_days", "viirs_days"):
        v = s[col]
        bad = v.notna() & ~(_is_int(v) & (pd.to_numeric(v, errors="coerce") >= 0))
        if col.endswith("_days"):
            bad |= v.notna() & (pd.to_numeric(v, errors="coerce") > days_in_month)
        problems[f"{col}_not_count"] = s[bad.fillna(True).astype(bool)]
    for col, nullable in (("viirs_ref", True), ("harmonized", True), ("k", False), ("support", False)):
        v = pd.to_numeric(s[col], errors="coerce").astype(float)
        ok = (np.isfinite(v) & (v >= 0)) | (v.isna() & nullable)
        problems[f"{col}_out_of_range"] = s[~ok]
    problems["reference_unknown"] = s[~s.reference.isin(REFERENCES)]
    problems["is_partial_not_bool"] = s[~s.is_partial.map(lambda x: isinstance(x, (bool, np.bool_)))]
    problems["unknown_flag"] = s[~s.quality_flags.map(lambda fl: isinstance(fl, list) and set(fl) <= set(ROW_FLAGS))]
    return _result("G1_schema_range", problems, {"rows": int(len(staged)), "affected_keys": int(len(expected))})


def _is_int(values: pd.Series) -> pd.Series:
    v = pd.to_numeric(values, errors="coerce").astype(float)
    return (np.isfinite(v) & (v == np.floor(v))) | values.isna()


def gate_conservation(staged: pd.DataFrame, kept: pd.DataFrame, affected_keys,
                      params: ProcessingParams = DEFAULT_PARAMS) -> GateResult:
    """G2: per sensor family, summed staged counts == detections that passed the filter in the
    affected cell-months."""
    keys = normalize_keys(affected_keys)
    if len(kept):
        d = with_keys(kept, params).merge(keys, on=KEY_COLS, how="inner")
        passed = d.family.value_counts()
    else:
        passed = pd.Series(dtype="int64")
    detail, problems = {}, {}
    for fam in FAMILIES:
        col = RAW_COUNT_COLUMN[fam]
        staged_sum = int(pd.to_numeric(staged[col], errors="coerce").fillna(0).sum())
        n = int(passed.get(fam, 0))
        detail[fam] = {"staged": staged_sum, "passed_filter": n}
        problems[f"{fam}_mismatch"] = abs(staged_sum - n)
    return _result("G2_conservation", problems, {"families": detail})


def gate_missing_not_zero(staged: pd.DataFrame, calibration: Calibration, coverage,
                          params: ProcessingParams = DEFAULT_PARAMS) -> GateResult:
    """G3 (I7): no value where the data it needs is unavailable; such rows carry a null and a flag.

    Re-derived per month from the calendar and the coverage: the reference; for each family,
    whether any day of the month is covered (else its count must be null); whether the
    reference's expected days are covered; whether an approved r exists when NOAA-20 is needed.
    """
    coverage = as_coverage(coverage)
    rows = []
    for year, month in sorted(set(zip(staged.year.astype(int), staged.month.astype(int)))):
        start, end = month_bounds(year, month)
        ref = reference_for(year, month, calibration.snpp_end, params.first_viirs_year)
        rng = reference_ranges(year, month, calibration.snpp_end, params.first_viirs_year)
        rows.append({"year": year, "month": month, "_ref": ref,
                     "_ref_ok": all(coverage.covered_days(f, a, b) > 0 for f, a, b in rng),
                     "_r_ok": calibration.r_for(month) is not None or ref not in (REF_NOAA20, REF_MIXED),
                     **{f"_cov_{f}": coverage.covered_days(f, start, end) > 0 for f in FAMILIES}})
    info = pd.DataFrame(rows)
    s = staged.astype({"year": "int64", "month": "int64"}).merge(info, on=["year", "month"], how="left") \
        if len(staged) else staged.assign(_ref=None)
    problems: dict[str, pd.DataFrame | int] = {}
    if len(s):
        flags = s.quality_flags.map(set)
        has = lambda flag: flags.map(lambda fl: flag in fl).astype(bool)  # noqa: E731
        problems["reference_not_calendar"] = s[s.reference != s._ref]
        for fam, col in RAW_COUNT_COLUMN.items():
            problems[f"{col}_not_null_without_coverage"] = s[~s[f"_cov_{fam}"] & s[col].notna()]
        pre = s._ref == REF_MODIS
        modis_missing = pre & ~s._cov_MODIS
        problems["harmonized_without_modis"] = s[modis_missing & s.harmonized.notna()]
        problems["modis_missing_unflagged"] = s[~s._cov_MODIS & ~has(MODIS_UNAVAILABLE)]
        ref_missing = ~pre & ~s._ref_ok
        problems["value_without_reference"] = s[ref_missing & (s.harmonized.notna() | s.viirs_ref.notna())]
        problems["reference_missing_unflagged"] = s[ref_missing & ~has(REFERENCE_UNAVAILABLE)]
        r_missing = ~pre & ~s._r_ok
        problems["value_without_r"] = s[r_missing & (s.harmonized.notna() | s.viirs_ref.notna())]
        problems["r_missing_unflagged"] = s[r_missing & ~has(REFERENCE_PENDING)]
        problems["viirs_ref_before_viirs"] = s[pre & s.viirs_ref.notna()]
    return _result("G3_missing_not_zero", problems, {"r_status": calibration.r_status})


def gate_frozen_calibration(staged: pd.DataFrame, calibration: Calibration) -> GateResult:
    """G4 (I6): every staged row uses the calibration version passed in (version and its k)."""
    problems: dict[str, pd.DataFrame | int] = {
        "other_version": staged[staged.calibration_version != calibration.version]}
    if len(staged):
        lk = calibration.lookup(staged)
        problems["k_not_from_calibration"] = staged[staged.k.to_numpy(dtype=float) != lk.k.to_numpy()]
        problems["support_not_from_calibration"] = staged[staged.support.to_numpy(dtype=float) != lk.support.to_numpy()]
    return _result("G4_frozen_calibration", problems,
                   {"calibration_version": calibration.version, "fingerprint": calibration.fingerprint})


def run_gates(staged: pd.DataFrame, kept: pd.DataFrame, affected_keys, calibration: Calibration, coverage,
              params: ProcessingParams = DEFAULT_PARAMS) -> list[GateResult]:
    return [gate_schema(staged, affected_keys),
            gate_conservation(staged, kept, affected_keys, params),
            gate_missing_not_zero(staged, calibration, coverage, params),
            gate_frozen_calibration(staged, calibration)]


def soft_flags(staged: pd.DataFrame, drift: DriftSummary | None = None, filter_summary: FilterSummary | None = None,
               *, recent_drop_shares: Mapping[str, float] | None = None,
               params: ProcessingParams = DEFAULT_PARAMS) -> list[dict]:
    """Published-but-visible conditions (spec §6): row flags with counts and sample keys, drift
    above DRIFT_WARN_PCT, and a clip or mask drop share far above its recent level (only when the
    recent shares are given; Phase 2b/4 own that history)."""
    out = []
    for flag in ROW_FLAGS:
        hit = staged[staged.quality_flags.map(lambda fl: flag in fl).astype(bool)] if len(staged) else staged
        if len(hit):
            out.append({"flag": flag, "rows": int(len(hit)), "keys": key_list(hit, SAMPLE)})
    if drift is not None and drift.flagged:
        out.append({"flag": "drift_above_warn", "warn_pct": drift.warn_pct, "months": list(drift.flagged)})
    if filter_summary is not None and recent_drop_shares:
        for reason in ("outside_country", "static_mask"):
            share, recent = filter_summary.drop_share.get(reason, 0.0), recent_drop_shares.get(reason)
            if recent is None or not math.isfinite(recent):
                continue
            limit = max(params.drop_share_factor * recent, recent + params.drop_share_min_pp / 100)
            if share > limit:
                out.append({"flag": "drop_share_high", "reason": reason, "share": share, "recent": recent,
                            "limit": round(limit, 6)})
    return out
