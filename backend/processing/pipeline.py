"""The deterministic processing run (spec §6 step 6): filter -> aggregate affected cell-months ->
harmonize with the frozen calibration -> completeness -> hard gates -> drift monitor.

Pure: no database, network or clock. Same inputs give identical output. Each stage is also
callable on its own (filters.apply_filters, grid.aggregate, harmonize.harmonize,
completeness.completeness, gates.run_gates, drift.drift_monitor) and returns a typed,
JSON-serialisable summary next to its data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .boundary import Boundary
from .calibration import Calibration
from .common import jsonable, utc_date
from .completeness import completeness
from .coverage import as_coverage
from .drift import drift_monitor
from .filters import apply_filters
from .gates import GateResult, run_gates, soft_flags
from .grid import aggregate
from .harmonize import harmonize
from .mask import StaticMask
from .params import DEFAULT_PARAMS, ProcessingParams


@dataclass(frozen=True, eq=False)
class ProcessingResult:
    staged: pd.DataFrame                 # one row per affected key, harmonize.STAGED_COLUMNS
    gates: tuple[GateResult, ...]
    soft_flags: tuple[dict, ...]
    stage_summaries: dict[str, dict]     # filter, aggregate, harmonize, completeness, drift, inputs

    @property
    def passed(self) -> bool:
        """All hard gates passed (publication still needs the review verdict, spec §7)."""
        return all(g.passed for g in self.gates)

    def summary(self) -> dict:
        return jsonable({"passed": self.passed, "gates": [g.to_dict() for g in self.gates],
                         "soft_flags": list(self.soft_flags), "stages": self.stage_summaries})

    def staged_records(self) -> list[dict]:
        """The staged rows as JSON-ready dicts (null counts and values as None)."""
        return jsonable(self.staged.to_dict(orient="records"))


def process(detections: pd.DataFrame, affected_keys, calibration: Calibration, *,
            mask: StaticMask | None, boundary: Boundary | None, coverage,
            availability: Mapping[str, object], now, params: ProcessingParams = DEFAULT_PARAMS,
            recent_drop_shares: Mapping[str, float] | None = None) -> ProcessingResult:
    """Run every stage over `detections` (all stored detections of the affected cell-months).

    mask / boundary: None disables that rule (reported "disabled" in the filter summary).
    coverage: processing.coverage.Coverage or (family, window_start, window_days) triples.
    availability: family -> availability max_date. now: the run's clock reading (UTC).
    """
    coverage = as_coverage(coverage)
    kept, f_sum = apply_filters(detections, mask=mask, boundary=boundary, params=params)
    counts, a_sum = aggregate(kept, affected_keys, split_date=calibration.snpp_end, params=params)
    staged, h_sum = harmonize(counts, affected_keys, calibration, coverage, params)
    staged, c_sum = completeness(staged, coverage, availability, now, calibration.snpp_end, params)
    gates = run_gates(staged, kept, affected_keys, calibration, coverage, params)
    drift = drift_monitor(staged, params)
    flags = soft_flags(staged, drift, f_sum, recent_drop_shares=recent_drop_shares, params=params)
    summaries = {
        "inputs": {"detections": int(len(detections)), "today_utc": utc_date(now),
                   "calibration": calibration.summary(), "coverage": coverage.summary(),
                   "availability": dict(sorted(availability.items())),
                   "params": params, "recent_drop_shares": dict(recent_drop_shares) if recent_drop_shares else None},
        "filter": f_sum.to_dict(),
        "aggregate": a_sum.to_dict(),
        "harmonize": h_sum.to_dict(),
        "completeness": c_sum.to_dict(),
        "drift": drift.to_dict(),
    }
    return ProcessingResult(staged=staged.reset_index(drop=True), gates=tuple(gates), soft_flags=tuple(flags),
                            stage_summaries=jsonable(summaries))
