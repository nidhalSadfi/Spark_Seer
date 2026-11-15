"""Inter-VIIRS factor r = S-NPP / NOAA-20 per calendar month (spec §5).

Fit on train years (default 2019-2023) with the shrinkage of pipeline/calibrate.fit, at region level:
    r_global = sum S / sum N              (train months)
    r[m]     = (S_m + pseudo * r_global) / (N_m + pseudo)
S, N = S-NPP and NOAA-20 detections (filtered); pseudo in NOAA-20 detections. Validated on test
years (default 2024-2025) at region-month level with calibrate.validate's measure:
    error % = 100 * mean |pred - observed S-NPP| / max(mean observed, 1), over months present in
    either sensor (outer join, missing = 0).
Any (year, month) containing a known outage day of S-NPP or NOAA-20, or listed as a whole outage
month, is excluded from fit and test: a dark satellite looks like "no fires".

Fit pool (spec §5, decision 2026-11-15): r may be fitted on a wider pool of countries and validated
out of region on Tunisia + Algeria (`validate_inter_viirs`, same measure and exclusions). The
acceptance rule (`accept_r`) approves the global r only if its validation error is below r = 1's;
only the global r is ever applied (`approved_r_from_fit`, `Calibration.with_global_r`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .common import as_date, jsonable, ym
from .params import DEFAULT_PARAMS

OUTAGES_FILE = Path(__file__).resolve().parent / "data" / "known_outages.json"
AUSTRALIA_REFERENCE_R = 1.02  # NOAA-20 / S-NPP median 0.98 in the Australia exploration (spec §5)
FIT_KIND = "emberguard.inter_viirs_fit"
ACCEPTANCE_RULE = ("approved_r = r_global if error_pct_global_ratio < error_pct_uncorrected (r = 1) on the "
                   "validation set over the test years, else 1.0; errors compared as reported (one decimal), so a "
                   "tie keeps r = 1; the per-month r is reported, never applied (spec §5)")


def load_outages(path=OUTAGES_FILE) -> dict:
    """The outage file: {"source", "outages": [...]}. An entry is either a day range
    {"family", "start", "end"} (inclusive) or a whole month {"family", "month": "YYYY-MM", "note"}."""
    doc = json.loads(Path(path).read_text())
    if "outages" not in doc or "source" not in doc:
        raise ValueError(f"{path} needs 'outages' and 'source'")
    for entry in doc["outages"]:
        _entry_months(entry)  # validates
    return doc


def _entry_months(entry: dict) -> set[tuple[int, int]]:
    if "family" not in entry:
        raise ValueError(f"outage entry without family: {entry}")
    if "month" in entry:
        if "start" in entry or "end" in entry:
            raise ValueError(f"outage entry has both month and start/end: {entry}")
        year, month = (int(p) for p in str(entry["month"]).split("-"))
        if not 1 <= month <= 12:
            raise ValueError(f"outage month must be YYYY-MM: {entry}")
        return {(year, month)}
    if "start" not in entry or "end" not in entry:
        raise ValueError(f"outage entry needs start and end, or month: {entry}")
    day, last = as_date(entry["start"]), as_date(entry["end"])
    if last < day:
        raise ValueError(f"outage ends before it starts: {entry}")
    months = set()
    while day <= last:
        months.add((day.year, day.month))
        day += timedelta(days=1)
    return months


def outage_months(outages, families=("SNPP", "NOAA20")) -> set[tuple[int, int]]:
    """(year, month) pairs containing at least one outage day (or a whole-month entry) of the given
    families."""
    entries = outages["outages"] if isinstance(outages, dict) else outages
    months: set[tuple[int, int]] = set()
    for entry in entries:
        if entry["family"] in families:
            months |= _entry_months(entry)
    return months


@dataclass(frozen=True)
class InterViirsFit:
    r: dict[int, float]   # calendar month -> factor (all 12 months)
    r_global: float
    report: dict

    def to_dict(self) -> dict:
        return jsonable({"r": {str(m): v for m, v in self.r.items()}, "r_global": self.r_global,
                         "report": self.report})


def _mae_pct(pred: pd.Series, obs: pd.Series) -> float:
    return round(100 * float((pred - obs).abs().mean() / max(obs.mean(), 1)), 1)


def _region_table(counts: pd.DataFrame) -> pd.DataFrame:
    """S-NPP and NOAA-20 detections per (year, month), summed over space."""
    sel = counts[counts.family.isin(["SNPP", "NOAA20"])]
    if sel.empty:
        raise ValueError("no S-NPP or NOAA-20 counts given")
    reg = sel.pivot_table(index=["year", "month"], columns="family", values="n", aggfunc="sum", fill_value=0)
    return reg.reindex(columns=["SNPP", "NOAA20"], fill_value=0).astype("int64")


def _not_excluded(reg: pd.DataFrame, excluded: set[tuple[int, int]]) -> np.ndarray:
    return ~np.array([(int(y), int(m)) in excluded for y, m in reg.index], dtype=bool)


def _excluded_list(excluded: set[tuple[int, int]], *year_ranges: tuple[int, int]) -> list[str]:
    lo, hi = min(a for a, _ in year_ranges), max(b for _, b in year_ranges)
    return [ym(y, m) for y in range(lo, hi + 1) for m in range(1, 13)
            if any(a <= y <= b for a, b in year_ranges) and (y, m) in excluded]


def _errors(test: pd.DataFrame, r: Mapping[int, float], r_global: float) -> dict:
    if not len(test):
        return {"error_pct_uncorrected": None, "error_pct_global_ratio": None, "error_pct_month_ratio": None}
    obs = test.SNPP.astype(float)
    raw = test.NOAA20.astype(float)
    month_r = pd.Series([r[m] for m in test.index.get_level_values("month")], index=test.index)
    return {"error_pct_uncorrected": _mae_pct(raw, obs), "error_pct_global_ratio": _mae_pct(raw * r_global, obs),
            "error_pct_month_ratio": _mae_pct(raw * month_r, obs)}


def fit_inter_viirs(counts: pd.DataFrame, outages, train_years=DEFAULT_PARAMS.inter_viirs_train_years,
                    test_years=DEFAULT_PARAMS.inter_viirs_test_years,
                    pseudo: float = DEFAULT_PARAMS.inter_viirs_pseudo) -> InterViirsFit:
    """`counts`: filtered counts with year, month, family, n (any spatial detail is summed away)."""
    t0, t1 = train_years
    v0, v1 = test_years
    excluded = outage_months(outages)
    reg = _region_table(counts)
    years = reg.index.get_level_values("year")
    kept = _not_excluded(reg, excluded)
    train = reg[(years >= t0) & (years <= t1) & kept]
    test = reg[(years >= v0) & (years <= v1) & kept]
    s_tot, n_tot = int(train.SNPP.sum()), int(train.NOAA20.sum())
    if s_tot == 0 or n_tot == 0:
        raise ValueError(f"no S-NPP / NOAA-20 overlap in train years {t0}-{t1} (S-NPP {s_tot}, NOAA-20 {n_tot})")
    r_global = s_tot / n_tot
    by_month = train.groupby(level="month").sum()
    r = {}
    for m in range(1, 13):
        s_m, n_m = float(by_month.SNPP.get(m, 0)), float(by_month.NOAA20.get(m, 0))
        r[m] = (s_m + pseudo * r_global) / (n_m + pseudo)

    excluded_list = _excluded_list(excluded, (t0, t1), (v0, v1))
    report = {
        "train_years": [t0, t1], "test_years": [v0, v1], "pseudo": pseudo,
        "r_global": round(r_global, 4),
        "r": {str(m): round(v, 4) for m, v in r.items()},
        "train_months": int(len(train)), "train_snpp": s_tot, "train_noaa20": n_tot,
        "test_months": int(len(test)),
        "excluded_months": len(excluded_list), "excluded_month_list": excluded_list,
        "australia_reference_r": AUSTRALIA_REFERENCE_R,
        "deviation_from_reference_pct": round(100 * (r_global - AUSTRALIA_REFERENCE_R) / AUSTRALIA_REFERENCE_R, 1),
    }
    report.update(_errors(test, r, r_global))
    return InterViirsFit(r=r, r_global=r_global, report=report)


def validate_inter_viirs(counts: pd.DataFrame, outages, fit: InterViirsFit,
                         test_years=DEFAULT_PARAMS.inter_viirs_test_years) -> dict:
    """Errors of r = 1, the fit's global r and its per-month r on other `counts` (e.g. Tunisia +
    Algeria for a pool fit) over `test_years`, with the measure and outage exclusion of the fit."""
    v0, v1 = test_years
    excluded = outage_months(outages)
    reg = _region_table(counts)
    years = reg.index.get_level_values("year")
    test = reg[(years >= v0) & (years <= v1) & _not_excluded(reg, excluded)]
    excluded_list = _excluded_list(excluded, (v0, v1))
    return {"test_years": [v0, v1], "test_months": int(len(test)), "test_snpp": int(test.SNPP.sum()),
            "test_noaa20": int(test.NOAA20.sum()), "excluded_months": len(excluded_list),
            "excluded_month_list": excluded_list, **_errors(test, fit.r, fit.r_global)}


def accept_r(fit: InterViirsFit, validation: dict) -> dict:
    """The spec §5 acceptance rule on a `validate_inter_viirs` result: the global r is approved only
    if its validation error is below r = 1's, else r = 1 is."""
    e1, eg = validation["error_pct_uncorrected"], validation["error_pct_global_ratio"]
    if e1 is None or eg is None:
        raise ValueError("no validation months in the test years: the acceptance rule cannot be applied")
    wins = eg < e1
    return {"rule": ACCEPTANCE_RULE, "approved_r": fit.r_global if wins else 1.0,
            "approved": "r_global" if wins else "r_1", "r_global": fit.r_global, "error_pct_r1": e1,
            "error_pct_r_global": eg, "error_pct_month_ratio": validation["error_pct_month_ratio"]}


def approved_r_from_fit(fit) -> float:
    """The `approved_r` of a fit written by `fit_inter_viirs --validate-dir` (a path or the parsed
    JSON). Raises ValueError when the file is not such a fit or carries no valid approved r."""
    doc = json.loads(Path(fit).read_text()) if isinstance(fit, (str, Path)) else fit
    if not isinstance(doc, dict) or doc.get("kind") != FIT_KIND:
        raise ValueError(f"not an inter-VIIRS fit ({FIT_KIND})")
    value = doc.get("approved_r")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (np.isfinite(value) and value > 0):
        raise ValueError("the fit has no approved_r (run it with --validate-dir)")
    return float(value)


_SENSOR_TOKEN = re.compile(r"(viirs|modis|snpp|noaa|jpss|firms|^fire$|^archive$|^all$|^nrt$|^sp$|\d)",
                           re.IGNORECASE)


def countries_from_names(files: Iterable) -> list[str]:
    """Country names in FIRMS file names such as viirs-snpp_2020_Portugal.csv or
    firms_modis_Tunisia_2001-2025.csv: the "_"-separated parts that name no sensor, product or year."""
    names = set()
    for f in files:
        parts = [p for p in Path(f).stem.split("_") if p and not _SENSOR_TOKEN.search(p)]
        if parts:
            names.add(" ".join(parts))
    return sorted(names)
