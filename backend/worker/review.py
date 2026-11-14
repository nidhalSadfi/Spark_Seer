"""CrewAI orchestration of one processing run and its AI review (spec §7, Phase 3).

`run_processing()` runs the deterministic stages in `processing.pipeline.STAGE_ORDER`, then
`review`, then `decide`, and returns the result, the decision, every LLM attempt and one record
per step (Phase 4 persists them as job_steps and llm_attempts; nothing here writes to a database).

- With OPENROUTER_MODELS set, the steps are a CrewAI Flow built from STEPS, so CrewAI runs them
  in code-defined order; agents only review summaries and cannot change which stages run
  (`ProcessingRun.run_stage` rejects any other order).
- With OPENROUTER_MODELS empty, the same steps run in a plain loop and crewai is never imported:
  ai_review is "disabled".

The review is three agents (Filter, Harmonization, Validation reviewer), each with one read-only
tool returning a JSON summary built from the stage summaries, gates and soft flags only (never
detection or staged rows), capped at REVIEW_SUMMARY_MAX_CHARS. One fallback attempt is the whole
crew run with one model. Its verdict must pass `validate_verdict` (parses, flags only affected
keys, I8: no number the tools did not return), and `decide()` lets it only downgrade.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Callable, Literal, Mapping

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.passport import check_numbers
from processing.boundary import Boundary
from processing.calibration import Calibration
from processing.common import normalize_keys
from processing.mask import StaticMask
from processing.params import DEFAULT_PARAMS, ProcessingParams
from processing.pipeline import STAGE_ORDER, ProcessingResult, ProcessingRun
from worker.llm import (AttemptContext, InvalidOutput, build_llm, crewai_run_scope, import_crewai,
                        run_with_fallback, token_usage, utcnow)

STEPS = STAGE_ORDER + ("review", "decide")
AI_REVIEW = ("completed", "unavailable", "disabled")
TRUNCATED = " ...[truncated to REVIEW_SUMMARY_MAX_CHARS]"  # no digits: adds nothing to the I8 set
MAX_ITER = 3  # per agent: one tool call, one answer, one spare
# Hashes (calibration fingerprint, mask digests and versions) are opaque to a reviewer, and their
# digits would only widen the I8 allowed set: dropped, or replaced by "<hash>" inside strings.
_HASH_FIELDS = ("fingerprint", "content_sha256", "file_sha256")
_HEX_RUN = re.compile(r"\b[0-9a-f]{12,}\b")
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

Key = tuple[int, int, int, int]  # (cy, cx, year, month)
LLMFactory = Callable[..., Any]  # (model, settings, *, timeout_s) -> crewai BaseLLM, like llm.build_llm


class Verdict(BaseModel):
    """The Validation reviewer's typed output (spec §7)."""
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "hold", "reject"]
    reasons: list[str]
    flagged_keys: list[Key] = Field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    decision: str     # approve | hold | reject
    ai_review: str    # completed | unavailable | disabled
    basis: str        # which row of the spec §7 table applied


def decide(gates_passed: bool, verdict: Verdict | None, ai_review: str) -> Decision:
    """Spec §7 decision table. Hard gates first; a verdict can only downgrade an approval.

    ai_review "completed" requires a verdict and the others forbid one.
    """
    if ai_review not in AI_REVIEW:
        raise ValueError(f"ai_review must be one of {AI_REVIEW}, got {ai_review!r}")
    if (verdict is not None) != (ai_review == "completed"):
        raise ValueError(f"ai_review {ai_review!r} with verdict {verdict!r}")
    if not gates_passed:
        return Decision("reject", ai_review, "hard_gate_failed")
    if verdict is not None:
        return Decision(verdict.decision, ai_review, "verdict")
    # D3: every model failed, or the review is switched off: the hard gates decide.
    return Decision("approve", ai_review, "gates_only")


def validate_verdict(verdict_or_raw: Verdict | Mapping | str, affected_keys, tool_results) -> Verdict:
    """The verdict as a Verdict, or InvalidOutput if it does not parse, flags a key outside
    `affected_keys`, or has a reason with a number not in `tool_results` (I8, check_numbers).

    `tool_results`: exactly what the agents were shown. A raw string may be wrapped in a ```json
    fence, as models often do.
    """
    try:
        if isinstance(verdict_or_raw, Verdict):
            verdict = Verdict.model_validate(verdict_or_raw.model_dump())
        elif isinstance(verdict_or_raw, str):
            raw = verdict_or_raw.strip()
            m = _FENCE.match(raw)
            verdict = Verdict.model_validate_json(m.group(1) if m else raw)
        else:
            verdict = Verdict.model_validate(verdict_or_raw)
    except ValidationError as e:
        raise InvalidOutput(f"verdict does not parse: {e.error_count()} error(s), "
                            f"first: {e.errors()[0]['msg']}") from None
    allowed = _key_set(affected_keys)
    foreign = [k for k in verdict.flagged_keys if k not in allowed]
    if foreign:
        raise InvalidOutput(f"flagged_keys outside the affected set: {foreign[:5]}")
    shown = {"tool_results": list(tool_results)}
    for reason in verdict.reasons:
        ok, bad = check_numbers(reason, shown)
        if not ok:
            raise InvalidOutput(f"reason has numbers not in the tool results (I8): {bad[:5]}")
    return verdict


def _key_set(affected_keys) -> set[Key]:
    return {tuple(int(v) for v in row) for row in normalize_keys(affected_keys).itertuples(index=False)}


# ----------------------------------------------------------------------------- agent inputs

def _cap(obj: Any, max_chars: int) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return text if len(text) <= max_chars else text[:max_chars - len(TRUNCATED)] + TRUNCATED


def _without_hashes(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _without_hashes(v) for k, v in obj.items() if k not in _HASH_FIELDS}
    if isinstance(obj, list):
        return [_without_hashes(v) for v in obj]
    return _HEX_RUN.sub("<hash>", obj) if isinstance(obj, str) else obj


def review_payloads(result: ProcessingResult, affected_keys, max_chars: int) -> dict[str, str]:
    """The JSON text each reviewer's tool returns, from stage summaries, gates and soft flags only."""
    s = result.summary()
    stages, inputs = s["stages"], s["stages"]["inputs"]
    payloads = {
        "filter": {"detections": inputs["detections"], "today_utc": inputs["today_utc"],
                   "filter": stages["filter"], "aggregate": stages["aggregate"]},
        "harmonization": {"calibration": inputs["calibration"], "coverage": inputs["coverage"],
                          "availability": inputs["availability"], "harmonize": stages["harmonize"],
                          "completeness": stages["completeness"], "drift": stages["drift"]},
        "validation": {"gates_passed": s["passed"], "gates": s["gates"], "soft_flags": s["soft_flags"],
                       "affected_keys": sorted(_key_set(affected_keys))},
    }
    return {name: _cap(_without_hashes(obj), max_chars) for name, obj in payloads.items()}


# ----------------------------------------------------------------------------- the crew

_REVIEWERS = {
    # name: (role, goal, tool description, task description)
    "filter": ("Filter reviewer", "Check that the filter and aggregation results of a detection run are plausible.",
               "Filter and aggregation summary of this processing run (JSON).",
               "Call the filter_summary tool, then review it: are the drops per rule and the kept and aggregated "
               "counts per sensor family consistent and plausible? Answer with up to three short notes."),
    "harmonization": ("Harmonization reviewer",
                      "Check that the harmonization, completeness and drift results are plausible.",
                      "Calibration, harmonization, completeness and drift summary of this run (JSON).",
                      "Call the harmonization_summary tool, then review it: reference sensors, quality flags, "
                      "partial months and drift. Answer with up to three short notes."),
    "validation": ("Validation reviewer", "Decide whether this run's results may be published.",
                   "Hard gate results, soft flags and the affected (cy, cx, year, month) keys of this run (JSON).",
                   "Call the validation_summary tool, then decide using it and the other reviewers' notes. "
                   "The hard gates are enforced in code; your decision can only keep or lower an approval. "
                   "Use approve, hold (keep the results unpublished for a human) or reject. Give short reasons, "
                   "and in flagged_keys only keys listed under affected_keys."),
}
_COMMON_RULES = (" Quote numbers only exactly as the tool returned them and never compute new ones. "
                 "A detection is a satellite thermal hotspot, not one wildfire.")


@lru_cache(maxsize=1)
def _summary_tool_class():
    import_crewai()
    from crewai.tools import BaseTool
    from pydantic import PrivateAttr

    class SummaryTool(BaseTool):
        """Read-only: returns a fixed JSON summary and records that it was shown."""
        payload: str
        _shown: list = PrivateAttr(default_factory=list)

        def _run(self, **_: Any) -> str:
            self._shown.append(self.payload)
            return self.payload

    return SummaryTool


def build_crew(llm, payloads: Mapping[str, str], shown: list[str]):
    """The three-agent sequential crew over `payloads`; tool outputs are appended to `shown`.

    Memory, knowledge, planning, embeddings, delegation and caching are off (reasoning and code
    execution stay at their off defaults), and every agent uses `llm` explicitly (no default
    provider). Context-window summarisation is off too: it would make extra LLM calls.
    """
    import_crewai()
    from crewai import Agent, Crew, Process, Task

    tool_cls = _summary_tool_class()
    agents, tasks = [], []
    for name, (role, goal, tool_text, task_text) in _REVIEWERS.items():
        tool = tool_cls(name=f"{name}_summary", description=tool_text, payload=payloads[name])
        tool._shown = shown
        agent = Agent(role=role, goal=goal, backstory="A careful reviewer of EmberGuard's automated FIRMS processing.",
                      llm=llm, tools=[tool], max_iter=MAX_ITER, max_retry_limit=0, allow_delegation=False,
                      verbose=False, cache=False, memory=None, knowledge_sources=None, embedder=None,
                      planning=False, inject_date=False, respect_context_window=False,
                      use_better_compaction=False)
        is_verdict = name == "validation"
        tasks.append(Task(description=task_text + _COMMON_RULES, agent=agent, context=list(tasks) if is_verdict else [],
                          expected_output=("A verdict with decision, reasons and flagged_keys." if is_verdict
                                           else "Up to three short notes."),
                          output_pydantic=Verdict if is_verdict else None))
        agents.append(agent)
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, memory=False, planning=False,
                cache=False, verbose=False, tracing=False, share_crew=False, embedder=None)


def review_attempt(ctx: AttemptContext, *, settings, payloads: Mapping[str, str], affected_keys,
                   llm_factory: LLMFactory) -> Verdict:
    """One model's whole crew run; raises on any failure (run_with_fallback then tries the next)."""
    llm = llm_factory(ctx.model, settings, timeout_s=ctx.budget_s)
    shown: list[str] = []
    try:
        out = build_crew(llm, payloads, shown).kickoff()
        final = out.tasks_output[-1]
        return validate_verdict(final.pydantic if final.pydantic is not None else final.raw, affected_keys, shown)
    finally:
        ctx.usage = token_usage(llm)


# ----------------------------------------------------------------------------- the run

@dataclass(frozen=True, eq=False)
class RunOutcome:
    result: ProcessingResult
    decision: str
    ai_review: str
    verdict: Verdict | None
    attempts: tuple[dict, ...]      # AttemptRecord.to_dict()
    steps: tuple[dict, ...]         # {name, status, started_at, finished_at, summary}, in run order


class ProcessingFailed(RuntimeError):
    """A step raised; `steps` holds the records up to and including the failed one."""

    def __init__(self, step: str, steps: tuple[dict, ...]):
        super().__init__(f"processing step {step!r} failed")
        self.step, self.steps = step, steps


@dataclass(eq=False)
class _Job:
    """The steps of one run, executed strictly in STEPS order by whoever drives them."""
    run: ProcessingRun
    affected_keys: Any
    settings: Any
    llm_factory: LLMFactory
    clock: Callable[[], float]
    now: Callable[[], datetime]
    steps: list[dict] = field(default_factory=list)
    result: ProcessingResult | None = None
    verdict: Verdict | None = None
    ai_review: str | None = None
    attempts: tuple[dict, ...] = ()
    decision: Decision | None = None

    def step(self, name: str) -> str:
        if len(self.steps) >= len(STEPS) or name != STEPS[len(self.steps)]:
            raise RuntimeError(f"step {name!r} out of order after {[s['name'] for s in self.steps]}")
        record = {"name": name, "status": "running", "started_at": self.now().isoformat(), "finished_at": None,
                  "summary": None}
        self.steps.append(record)
        work = {"review": self._review, "decide": self._decide}.get(name, lambda: self.run.run_stage(name))
        try:
            record["summary"] = work()
        except Exception as e:
            record.update(status="failed", finished_at=self.now().isoformat(), summary={"error": type(e).__name__})
            raise ProcessingFailed(name, tuple(self.steps)) from e
        record.update(status="ok", finished_at=self.now().isoformat())
        return name

    def _review(self) -> dict:
        self.result = self.run.result()
        models = self.settings.openrouter_models
        if not models:
            self.ai_review = "disabled"
            return {"ai_review": "disabled", "models": 0}
        payloads = review_payloads(self.result, self.affected_keys, self.settings.review_summary_max_chars)
        verdict, attempts = run_with_fallback(
            models, lambda ctx: review_attempt(ctx, settings=self.settings, payloads=payloads,
                                               affected_keys=self.affected_keys, llm_factory=self.llm_factory),
            deadline_s=self.settings.review_deadline_s, secrets=(self.settings.openrouter_api_key,),
            clock=self.clock, now=self.now)
        self.verdict, self.attempts = verdict, tuple(a.to_dict() for a in attempts)
        self.ai_review = "completed" if verdict is not None else "unavailable"
        return {"ai_review": self.ai_review, "models": len(models), "attempts": len(attempts),
                "model": attempts[-1].model if verdict is not None else None,
                "verdict": verdict.model_dump(mode="json") if verdict is not None else None,
                "payload_chars": {k: len(v) for k, v in payloads.items()}}

    def _decide(self) -> dict:
        self.decision = decide(self.result.passed, self.verdict, self.ai_review)
        return {"decision": self.decision.decision, "ai_review": self.decision.ai_review,
                "basis": self.decision.basis, "gates_passed": self.result.passed}

    def outcome(self) -> RunOutcome:
        return RunOutcome(result=self.result, decision=self.decision.decision, ai_review=self.decision.ai_review,
                          verdict=self.verdict, attempts=self.attempts, steps=tuple(self.steps))


@lru_cache(maxsize=1)
def _flow_class():
    """A CrewAI Flow with one method per entry of STEPS, each listening to the one before."""
    import_crewai()
    from crewai.flow.flow import Flow, listen, start
    from pydantic import PrivateAttr

    def make(name: str):
        def step(self) -> str:
            return self._job.step(name)
        step.__name__ = step.__qualname__ = name
        return step

    # _skip_auto_memory: without it a Flow creates a Memory (and its storage) on construction.
    ns: dict[str, Any] = {"__annotations__": {"_skip_auto_memory": bool}, "_skip_auto_memory": True,
                          "_job": PrivateAttr(default=None)}
    previous = None
    for name in STEPS:
        ns[name] = start()(make(name)) if previous is None else listen(previous)(make(name))
        previous = name
    return type("ProcessingFlow", (Flow,), ns)


def run_processing(detections: pd.DataFrame, affected_keys, calibration: Calibration, *,
                   mask: StaticMask | None, boundary: Boundary | None, coverage,
                   availability: Mapping[str, object], now, params: ProcessingParams = DEFAULT_PARAMS,
                   recent_drop_shares: Mapping[str, float] | None = None, settings,
                   llm_factory: LLMFactory | None = None, clock: Callable[[], float] = time.monotonic,
                   utcnow: Callable[[], datetime] = utcnow) -> RunOutcome:
    """Process, review and decide one run. Arguments up to `recent_drop_shares` are `process()`'s.

    settings: settings.Settings (OPENROUTER_*, REVIEW_*). llm_factory: builds each attempt's LLM,
    default worker.llm.build_llm (tests inject a stub). clock / utcnow: the deadline clock and the
    timestamps of steps and attempts. Raises ProcessingFailed if a step raises.
    """
    run = ProcessingRun.start(detections, affected_keys, calibration, mask=mask, boundary=boundary,
                              coverage=coverage, availability=availability, now=now, params=params,
                              recent_drop_shares=recent_drop_shares)
    job = _Job(run=run, affected_keys=affected_keys, settings=settings, llm_factory=llm_factory or build_llm,
               clock=clock, now=utcnow)
    if not settings.openrouter_models:
        for name in STEPS:  # review disabled: crewai is not imported (spec §7)
            job.step(name)
        return job.outcome()
    with crewai_run_scope():
        flow = _flow_class()(tracing=False, suppress_flow_events=True)
        flow._job = job
        flow.kickoff()
    if tuple(s["name"] for s in job.steps) != STEPS:  # the flow must have run every step, in order
        raise RuntimeError(f"flow ran {[s['name'] for s in job.steps]}, expected {list(STEPS)}")
    return job.outcome()
