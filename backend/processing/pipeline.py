"""The deterministic processing run (spec §6 step 6): filter -> aggregate affected cell-months ->
harmonize with the frozen calibration -> completeness -> hard gates -> drift monitor.

Pure: no database, network or clock. Same inputs give identical output. Each stage is also
callable on its own (filters.apply_filters, grid.aggregate, harmonize.harmonize,
completeness.completeness, gates.run_gates, drift.drift_monitor) and returns a typed,
JSON-serialisable summary next to its data.

`STAGE_ORDER` is the one definition of the stage order. `process()` runs it in one go; the
worker's CrewAI flow (worker/review.py) runs the same `ProcessingRun` one stage per flow step,
so both produce the same `ProcessingResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

from .boundary import Boundary
from .calibration import Calibration
from .common import jsonable, utc_date
from .completeness import completeness
from .coverage import Coverage, as_coverage
from .drift import DriftSummary, drift_monitor
from .filters import FilterSummary, apply_filters
from .gates import GateResult, run_gates, soft_flags
from .grid import aggregate
from .harmonize import harmonize
from .mask import StaticMask
from .params import DEFAULT_PARAMS, ProcessingParams

STAGE_ORDER = ("filter", "aggregate", "harmonize", "completeness", "gates", "drift")


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


@dataclass(eq=False)
class ProcessingRun:
    """One run's inputs and intermediate results, advanced one stage at a time.

    `run_stage(name)` only accepts the next name in STAGE_ORDER, so no caller (the flow included)
    can skip, repeat or reorder a stage; `result()` needs every stage done.
    """
    detections: pd.DataFrame
    affected_keys: Any
    calibration: Calibration
    mask: StaticMask | None
    boundary: Boundary | None
    coverage: Coverage
    availability: Mapping[str, object]
    now: Any
    params: ProcessingParams = DEFAULT_PARAMS
    recent_drop_shares: Mapping[str, float] | None = None
    done: list[str] = field(default_factory=list)
    kept: pd.DataFrame | None = None
    counts: pd.DataFrame | None = None
    staged: pd.DataFrame | None = None
    filter_summary: FilterSummary | None = None
    gates: list[GateResult] | None = None
    drift: DriftSummary | None = None
    summaries: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, detections: pd.DataFrame, affected_keys, calibration: Calibration, *,
              mask: StaticMask | None, boundary: Boundary | None, coverage, availability: Mapping[str, object],
              now, params: ProcessingParams = DEFAULT_PARAMS,
              recent_drop_shares: Mapping[str, float] | None = None) -> "ProcessingRun":
        """Same arguments as `process()`."""
        return cls(detections=detections, affected_keys=affected_keys, calibration=calibration, mask=mask,
                   boundary=boundary, coverage=as_coverage(coverage), availability=availability, now=now,
                   params=params, recent_drop_shares=recent_drop_shares)

    def run_stage(self, name: str) -> dict:
        """Run stage `name` (must be the next one in STAGE_ORDER); return its JSON-ready summary."""
        if len(self.done) >= len(STAGE_ORDER) or name != STAGE_ORDER[len(self.done)]:
            nxt = STAGE_ORDER[len(self.done)] if len(self.done) < len(STAGE_ORDER) else None
            raise RuntimeError(f"stage {name!r} out of order: done {self.done}, next is {nxt!r}")
        summary = getattr(self, f"_{name}")()
        self.done.append(name)
        return jsonable(summary)

    def _filter(self) -> dict:
        self.kept, self.filter_summary = apply_filters(self.detections, mask=self.mask, boundary=self.boundary,
                                                       params=self.params)
        self.summaries["filter"] = self.filter_summary.to_dict()
        return self.summaries["filter"]

    def _aggregate(self) -> dict:
        self.counts, a_sum = aggregate(self.kept, self.affected_keys, split_date=self.calibration.snpp_end,
                                       params=self.params)
        self.summaries["aggregate"] = a_sum.to_dict()
        return self.summaries["aggregate"]

    def _harmonize(self) -> dict:
        self.staged, h_sum = harmonize(self.counts, self.affected_keys, self.calibration, self.coverage, self.params)
        self.summaries["harmonize"] = h_sum.to_dict()
        return self.summaries["harmonize"]

    def _completeness(self) -> dict:
        self.staged, c_sum = completeness(self.staged, self.coverage, self.availability, self.now,
                                          self.calibration.snpp_end, self.params)
        self.summaries["completeness"] = c_sum.to_dict()
        return self.summaries["completeness"]

    def _gates(self) -> dict:
        self.gates = run_gates(self.staged, self.kept, self.affected_keys, self.calibration, self.coverage,
                               self.params)
        return {"passed": all(g.passed for g in self.gates), "gates": [g.to_dict() for g in self.gates]}

    def _drift(self) -> dict:
        self.drift = drift_monitor(self.staged, self.params)
        self.summaries["drift"] = self.drift.to_dict()
        return self.summaries["drift"]

    def result(self) -> ProcessingResult:
        if tuple(self.done) != STAGE_ORDER:
            raise RuntimeError(f"run incomplete: done {self.done}, expected {list(STAGE_ORDER)}")
        flags = soft_flags(self.staged, self.drift, self.filter_summary, recent_drop_shares=self.recent_drop_shares,
                           params=self.params)
        summaries = {
            "inputs": {"detections": int(len(self.detections)), "today_utc": utc_date(self.now),
                       "calibration": self.calibration.summary(), "coverage": self.coverage.summary(),
                       "availability": dict(sorted(self.availability.items())), "params": self.params,
                       "recent_drop_shares": dict(self.recent_drop_shares) if self.recent_drop_shares else None},
            **{name: self.summaries[name] for name in STAGE_ORDER if name in self.summaries},
        }
        return ProcessingResult(staged=self.staged.reset_index(drop=True), gates=tuple(self.gates),
                                soft_flags=tuple(flags), stage_summaries=jsonable(summaries))


def process(detections: pd.DataFrame, affected_keys, calibration: Calibration, *,
            mask: StaticMask | None, boundary: Boundary | None, coverage,
            availability: Mapping[str, object], now, params: ProcessingParams = DEFAULT_PARAMS,
            recent_drop_shares: Mapping[str, float] | None = None) -> ProcessingResult:
    """Run every stage over `detections` (all stored detections of the affected cell-months).

    mask / boundary: None disables that rule (reported "disabled" in the filter summary).
    coverage: processing.coverage.Coverage or (family, window_start, window_days) triples.
    availability: family -> availability max_date. now: the run's clock reading (UTC).
    """
    run = ProcessingRun.start(detections, affected_keys, calibration, mask=mask, boundary=boundary,
                              coverage=coverage, availability=availability, now=now, params=params,
                              recent_drop_shares=recent_drop_shares)
    for name in STAGE_ORDER:
        run.run_stage(name)
    return run.result()
