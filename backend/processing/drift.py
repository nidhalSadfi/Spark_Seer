"""Drift monitor (spec §5): for complete months from 2012 whose `modis` is fully covered (no
`modis_partial` or `modis_unavailable`) and whose reference exists, compare sum(MODIS x k) with
sum(viirs_ref) over the cells in the run. A % error above DRIFT_WARN_PCT is a soft flag, never a
block. A month whose sum(viirs_ref) is below DRIFT_MIN_COUNT is reported as `insufficient_data`
and never flagged: small counts swing by chance.

% error = 100 x |sum(MODIS x k) - sum(viirs_ref)| / sum(viirs_ref), the per-month form of
pipeline/calibrate.validate's measure. The "region" is the staged rows, i.e. the affected cells of
each month (all cells with data in a monthly_reconcile run, fewer in a 3-hourly poll). Drift is
job-level, not a row flag.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .common import MODIS_PARTIAL, MODIS_UNAVAILABLE, jsonable, ym
from .params import DEFAULT_PARAMS, ProcessingParams

OK, WARN, INSUFFICIENT = "ok", "warn", "insufficient_data"


@dataclass(frozen=True)
class DriftSummary:
    warn_pct: float
    min_count: float
    months: list[dict]   # {month, cells, modis_k, viirs_ref, error_pct, status, flagged}
    flagged: list[str]   # "YYYY-MM"
    scope: str = "staged (affected) cells of each complete month from first_viirs_year with fully covered MODIS"

    def to_dict(self) -> dict:
        return jsonable(self)


def drift_monitor(staged: pd.DataFrame, params: ProcessingParams = DEFAULT_PARAMS) -> DriftSummary:
    modis_full = ~staged.quality_flags.map(lambda fl: MODIS_PARTIAL in fl or MODIS_UNAVAILABLE in fl).astype(bool)
    use = staged[(~staged.is_partial.fillna(True).astype(bool)) & (staged.year >= params.first_viirs_year)
                 & modis_full & staged.modis.notna() & staged.viirs_ref.notna()]
    months = []
    if len(use):
        g = use.assign(modis_k=use.modis.astype(float) * use.k).groupby(["year", "month"], sort=True)
        table = g.agg(cells=("cx", "size"), modis_k=("modis_k", "sum"), viirs_ref=("viirs_ref", "sum"))
        for (year, month), row in table.iterrows():
            enough = row.viirs_ref >= params.drift_min_count and row.viirs_ref > 0
            err = round(100 * abs(row.modis_k - row.viirs_ref) / row.viirs_ref, 1) if enough else None
            status = INSUFFICIENT if not enough else (WARN if err > params.drift_warn_pct else OK)
            months.append({"month": ym(year, month), "cells": int(row.cells), "modis_k": round(float(row.modis_k), 3),
                           "viirs_ref": round(float(row.viirs_ref), 3), "error_pct": err, "status": status,
                           "flagged": status == WARN})
    return DriftSummary(warn_pct=params.drift_warn_pct, min_count=params.drift_min_count, months=months,
                        flagged=[m["month"] for m in months if m["flagged"]])
