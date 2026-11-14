"""Complete vs partial months (spec §5).

A cell-month is complete iff (a) the month has ended (UTC) and (b) every family the harmonized
value depends on (common.expected_ranges: MODIS before 2012, the reference family or families from
2012) has a stored fetch window on every expected day, and that family's availability max_date is
on or after its last expected day. Expected families are decided by calendar, not by detections,
so a cell with zero detections is still partial without coverage. Coverage is bbox-wide, so the
answer is per month.

In the month that contains snpp_end, S-NPP is expected only up to snpp_end (and its max_date only
needs to reach snpp_end), NOAA-20 from the day after. From 2012 MODIS does not decide completeness
(the end of Terra and Aqua must not leave every month partial); harmonize flags `modis` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

import pandas as pd

from .common import PARTIAL, as_date, expected_ranges, jsonable, month_bounds, utc_date, ym
from .coverage import Coverage, as_coverage
from .harmonize import add_flag
from .params import DEFAULT_PARAMS, ProcessingParams


@dataclass(frozen=True)
class CompletenessSummary:
    today_utc: date
    months: dict[str, dict]  # "YYYY-MM" -> {complete, reasons}
    complete_months: int
    partial_months: int
    partial_rows: int

    def to_dict(self) -> dict:
        return jsonable(self)


def month_status(year: int, month: int, coverage: Coverage, availability: Mapping[str, object], now,
                 snpp_end: date | None, params: ProcessingParams = DEFAULT_PARAMS) -> tuple[bool, list[str]]:
    """(complete, reasons it is partial) for one month."""
    reasons = []
    _, month_end = month_bounds(year, month)
    if utc_date(now) <= month_end:
        reasons.append("month_not_ended")
    for family, first, last in expected_ranges(year, month, snpp_end, params.first_viirs_year):
        need = (last - first).days + 1
        got = coverage.covered_days(family, first, last)
        if got < need:
            reasons.append(f"{family}: {got}/{need} days covered from {first} to {last}")
        max_date = availability.get(family)
        if max_date is None:
            reasons.append(f"{family}: no availability max_date")
        elif as_date(max_date) < last:
            reasons.append(f"{family}: availability max_date {as_date(max_date)} is before {last}")
    return not reasons, reasons


def completeness(staged: pd.DataFrame, coverage, availability: Mapping[str, object], now,
                 snpp_end: date | None, params: ProcessingParams = DEFAULT_PARAMS
                 ) -> tuple[pd.DataFrame, CompletenessSummary]:
    """Copy of staged with is_partial set and flag `partial` added where the month is not complete."""
    coverage = as_coverage(coverage)
    months = {}
    for year, month in sorted(set(zip(staged.year.astype(int), staged.month.astype(int)))):
        complete, reasons = month_status(year, month, coverage, availability, now, snpp_end, params)
        months[(year, month)] = (complete, reasons)
    partial = [not months[(int(y), int(m))][0] for y, m in zip(staged.year, staged.month)]
    out = add_flag(staged, PARTIAL, partial)
    out["is_partial"] = pd.array(partial, dtype="boolean")
    summary = CompletenessSummary(
        today_utc=utc_date(now),
        months={ym(y, m): {"complete": c, "reasons": r} for (y, m), (c, r) in sorted(months.items())},
        complete_months=sum(c for c, _ in months.values()),
        partial_months=sum(not c for c, _ in months.values()),
        partial_rows=int(sum(partial)))
    return out, summary
