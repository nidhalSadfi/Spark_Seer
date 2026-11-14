"""Drift monitor (spec §5): for complete months from 2012 where MODIS and the VIIRS reference both
exist, compare sum(MODIS x k) with sum(viirs_ref) at region-month level. A % error above
DRIFT_WARN_PCT is a soft flag, never a block.

% error = 100 x |sum(MODIS x k) - sum(viirs_ref)| / max(sum(viirs_ref), 1), the per-month form of
pipeline/calibrate.validate's measure. The "region" is the staged rows, i.e. the affected cells of
each month (all cells with data in a monthly_reconcile run, fewer in a 3-hourly poll).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .common import jsonable, ym
from .params import DEFAULT_PARAMS, ProcessingParams


@dataclass(frozen=True)
class DriftSummary:
    warn_pct: float
    months: list[dict]   # {month, cells, modis_k, viirs_ref, error_pct, flagged}
    flagged: list[str]   # "YYYY-MM"
    scope: str = "staged (affected) cells of each complete month from first_viirs_year"

    def to_dict(self) -> dict:
        return jsonable(self)


def drift_monitor(staged: pd.DataFrame, params: ProcessingParams = DEFAULT_PARAMS) -> DriftSummary:
    use = staged[(~staged.is_partial.fillna(True).astype(bool)) & (staged.year >= params.first_viirs_year)
                 & staged.modis.notna() & staged.viirs_ref.notna()]
    months = []
    if len(use):
        g = use.assign(modis_k=use.modis.astype(float) * use.k).groupby(["year", "month"], sort=True)
        table = g.agg(cells=("cx", "size"), modis_k=("modis_k", "sum"), viirs_ref=("viirs_ref", "sum"))
        for (year, month), row in table.iterrows():
            err = round(100 * abs(row.modis_k - row.viirs_ref) / max(row.viirs_ref, 1.0), 1)
            months.append({"month": ym(year, month), "cells": int(row.cells), "modis_k": round(float(row.modis_k), 3),
                           "viirs_ref": round(float(row.viirs_ref), 3), "error_pct": err,
                           "flagged": bool(err > params.drift_warn_pct)})
    return DriftSummary(warn_pct=params.drift_warn_pct, months=months,
                        flagged=[m["month"] for m in months if m["flagged"]])
