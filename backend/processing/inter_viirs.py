"""Inter-VIIRS factor r = S-NPP / NOAA-20 per calendar month (spec §5).

Fit on train years (default 2019-2023) with the shrinkage of pipeline/calibrate.fit, at region level:
    r_global = sum S / sum N              (train months)
    r[m]     = (S_m + pseudo * r_global) / (N_m + pseudo)
S, N = S-NPP and NOAA-20 detections (filtered); pseudo in NOAA-20 detections. Validated on test
years (default 2024-2025) at region-month level with calibrate.validate's measure:
    error % = 100 * mean |pred - observed S-NPP| / max(mean observed, 1), over months present in
    either sensor (outer join, missing = 0).
Any (year, month) containing a known outage day of S-NPP or NOAA-20 is excluded from fit and test:
a dark satellite looks like "no fires".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .common import as_date, jsonable, ym
from .params import DEFAULT_PARAMS

OUTAGES_FILE = Path(__file__).resolve().parent / "data" / "known_outages.json"
AUSTRALIA_REFERENCE_R = 1.02  # NOAA-20 / S-NPP median 0.98 in the Australia exploration (spec §5)


def load_outages(path=OUTAGES_FILE) -> dict:
    doc = json.loads(Path(path).read_text())
    if "outages" not in doc or "source" not in doc:
        raise ValueError(f"{path} needs 'outages' and 'source'")
    return doc


def outage_months(outages, families=("SNPP", "NOAA20")) -> set[tuple[int, int]]:
    """(year, month) pairs containing at least one outage day of the given families."""
    entries = outages["outages"] if isinstance(outages, dict) else outages
    months = set()
    for o in entries:
        if o["family"] not in families:
            continue
        day, last = as_date(o["start"]), as_date(o["end"])
        if last < day:
            raise ValueError(f"outage ends before it starts: {o}")
        while day <= last:
            months.add((day.year, day.month))
            day += timedelta(days=1)
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


def fit_inter_viirs(counts: pd.DataFrame, outages, train_years=DEFAULT_PARAMS.inter_viirs_train_years,
                    test_years=DEFAULT_PARAMS.inter_viirs_test_years,
                    pseudo: float = DEFAULT_PARAMS.inter_viirs_pseudo) -> InterViirsFit:
    """`counts`: filtered counts with year, month, family, n (any spatial detail is summed away)."""
    t0, t1 = train_years
    v0, v1 = test_years
    excluded = outage_months(outages)
    sel = counts[counts.family.isin(["SNPP", "NOAA20"])]
    if sel.empty:
        raise ValueError("no S-NPP or NOAA-20 counts given")
    reg = sel.pivot_table(index=["year", "month"], columns="family", values="n", aggfunc="sum", fill_value=0)
    reg = reg.reindex(columns=["SNPP", "NOAA20"], fill_value=0).astype("int64")
    years = reg.index.get_level_values("year")
    kept = ~np.array([(int(y), int(m)) in excluded for y, m in reg.index], dtype=bool)
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

    months_in_scope = [(y, m) for y in range(min(t0, v0), max(t1, v1) + 1) for m in range(1, 13)
                       if (t0 <= y <= t1 or v0 <= y <= v1)]
    excluded_list = [ym(y, m) for y, m in months_in_scope if (y, m) in excluded]
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
    if len(test):
        obs = test.SNPP.astype(float)
        raw = test.NOAA20.astype(float)
        month_r = pd.Series([r[m] for m in test.index.get_level_values("month")], index=test.index)
        report.update(
            error_pct_uncorrected=_mae_pct(raw, obs),
            error_pct_global_ratio=_mae_pct(raw * r_global, obs),
            error_pct_month_ratio=_mae_pct(raw * month_r, obs))
    else:
        report.update(error_pct_uncorrected=None, error_pct_global_ratio=None, error_pct_month_ratio=None)
    return InterViirsFit(r=r, r_global=r_global, report=report)
