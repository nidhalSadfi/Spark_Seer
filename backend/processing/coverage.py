"""Fetch coverage per sensor family: which UTC days have a stored fetch window (NRT or SP).

Phase 2b builds it from `firms_fetches` (source -> family, window_start, window_days). Coverage is
for the whole bbox, so it is per family and day, not per cell.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .common import FAMILIES, as_date

ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Coverage:
    # family -> merged, sorted, disjoint inclusive (first day, last day) intervals
    intervals: tuple[tuple[str, tuple[tuple[date, date], ...]], ...] = ()

    @classmethod
    def from_windows(cls, windows: Iterable[tuple[str, object, int]]) -> "Coverage":
        """From (family, window_start, window_days) triples; window_days >= 1."""
        by_family: dict[str, list[tuple[date, date]]] = defaultdict(list)
        for family, start, days in windows:
            if family not in FAMILIES:
                raise ValueError(f"unknown sensor family {family!r}; expected one of {FAMILIES}")
            days = int(days)
            if days < 1:
                raise ValueError(f"window_days must be >= 1, got {days}")
            first = as_date(start)
            by_family[family].append((first, first + timedelta(days=days - 1)))
        return cls(tuple((fam, _merge(by_family[fam])) for fam in sorted(by_family)))

    @classmethod
    def full(cls, families: Iterable[str], start, end) -> "Coverage":
        """Every day from start to end (inclusive) covered for each family (e.g. archived SP years)."""
        first, last = as_date(start), as_date(end)
        return cls.from_windows((f, first, (last - first).days + 1) for f in families)

    def of(self, family: str) -> tuple[tuple[date, date], ...]:
        return dict(self.intervals).get(family, ())

    def covered_days(self, family: str, start: date, end: date) -> int:
        """Number of days in [start, end] covered for this family."""
        total = 0
        for lo, hi in self.of(family):
            a, b = max(lo, start), min(hi, end)
            if a <= b:
                total += (b - a).days + 1
        return total

    def summary(self) -> dict:
        return {fam: {"days": sum((b - a).days + 1 for a, b in iv),
                      "intervals": [[a.isoformat(), b.isoformat()] for a, b in iv]}
                for fam, iv in self.intervals}


def as_coverage(value) -> Coverage:
    return value if isinstance(value, Coverage) else Coverage.from_windows(value)


def _merge(spans: list[tuple[date, date]]) -> tuple[tuple[date, date], ...]:
    merged: list[tuple[date, date]] = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + ONE_DAY:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return tuple(merged)
