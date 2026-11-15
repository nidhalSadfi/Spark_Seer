"""CrewAI orchestration of one processing run and its AI review (spec §7, Phase 3).

`run_processing()` runs the deterministic stages in `processing.pipeline.STAGE_ORDER`, then
`review`, then `decide`, and returns the result, the decision, every LLM attempt and one record
per step (Phase 4 persists them as job_steps and llm_attempts; nothing here writes to a database).

- With OPENROUTER_MODELS set, the steps are a CrewAI Flow built from STEPS, so CrewAI runs them
  in code-defined order; agents only review summaries and cannot change which stages run
  (`ProcessingRun.run_stage` rejects any other order).
- With OPENROUTER_MODELS empty, the same steps run in a plain loop and crewai is never imported:
  ai_review is "disabled".

The review is three agents (Filter, Harmonization, Validation reviewer). Each one's task prompt
carries its JSON summary, built from the stage summaries, gates and soft flags only (never
detection or staged rows) and capped at REVIEW_SUMMARY_MAX_CHARS. Agents have no tools and answer
once (spec §7 free-tier budget), so one fallback attempt, the whole crew run with one model, sends
at most REQUESTS_PER_ATTEMPT requests; `worker.llm.limit_requests` enforces that. The Validation
reviewer's raw answer must pass `validate_verdict` (parses, flags only affected keys, I8: no number
absent from the summaries in the prompts), and `decide()` lets it only downgrade.

Before any model is tried, the key's remaining free-model requests are read
(OPENROUTER_QUOTA_CHECK). With fewer than REQUESTS_PER_ATTEMPT left, no model is built: the review
is "unavailable" with reason "daily_quota", and the gates decide (D3).
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
from worker.llm import (AttemptContext, InvalidOutput, build_llm, crewai_run_scope, free_model_quota,
                        import_crewai, limit_requests, run_with_fallback, token_usage, utcnow)

STEPS = STAGE_ORDER + ("review", "decide")
AI_REVIEW = ("completed", "unavailable", "disabled")
TRUNCATED = " ...[truncated to REVIEW_SUMMARY_MAX_CHARS]"  # no digits: adds nothing to the I8 set
MAX_ITER = 1  # per agent: the answer, nothing else (spec §7 free-tier budget)
# Hashes (calibration fingerprint, mask digests and versions) are opaque to a reviewer, and their
# digits would only widen the I8 allowed set: dropped, or replaced by "<hash>" inside strings.
_HASH_FIELDS = ("fingerprint", "content_sha256", "file_sha256")
_HEX_RUN = re.compile(r"\b[0-9a-f]{12,}\b")
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

Key = tuple[int, int, int, int]  # (cy, cx, year, month)
LLMFactory = Callable[..., Any]  # (model, settings, *, timeout_s) -> crewai BaseLLM, like llm.build_llm
QuotaCheck = Callable[[Any], "int | None"]  # (settings) -> free-model requests left today, like llm.free_model_quota


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

    `tool_results`: exactly the summaries put into the agents' prompts (spec §7: "tool results"
    in I8 means these). A raw string may be wrapped in a ```json fence, as models often do.
    """
    try:
        if isinstance(verdict_or_raw, Verdict):
            verdict = Verdict.model_validate(verdict_or_raw.model_dump())
        elif isinstance(verdict_or_raw, str):
            raw = verdict_or_raw.strip()
            m = _FENCE.match(raw)
            verdict = _embedded_verdict(raw) or Verdict.model_validate_json(m.group(1) if m else raw)
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


def _embedded_verdict(raw: str) -> Verdict | None:
    """The first JSON object in `raw` that is a valid Verdict, or None.

    Free models often wrap the JSON in prose ("Here is my verdict: {...}"). Without a CrewAI
    conversion call (free-tier budget, spec §7), rejecting that would waste a whole attempt.
    The object must still validate completely, and I8 and the key checks still apply to it.
    """
    decoder = json.JSONDecoder()
    for start in (i for i, ch in enumerate(raw) if ch == "{"):
        try:
            obj, _ = decoder.raw_decode(raw, start)
            return Verdict.model_validate(obj)
        except (ValueError, ValidationError):
            continue
    return None


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
    """The JSON text put into each reviewer's task prompt, from stage summaries, gates and soft flags only."""
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

# The wording contains no digits, so the only numbers a prompt adds are the payload's (I8).
_REVIEWERS = {
    # name: (role, goal, task description; the payload follows it)
    "filter": ("Filter reviewer", "Check that the filter and aggregation results of a detection run are plausible.",
               "Review the filter and aggregation summary of this processing run below: are the drops per rule "
               "and the kept and aggregated counts per sensor family consistent and plausible? Answer with up to "
               "three short notes."),
    "harmonization": ("Harmonization reviewer",
                      "Check that the harmonization, completeness and drift results are plausible.",
                      "Review the calibration, harmonization, completeness and drift summary of this run below: "
                      "reference sensors, quality flags, partial months and drift. Answer with up to three short "
                      "notes."),
    "validation": ("Validation reviewer", "Decide whether this run's results may be published.",
                   "Decide, using the hard gate results, soft flags and affected (cy, cx, year, month) keys below "
                   "and the other reviewers' notes. The hard gates are enforced in code; your decision can only "
                   "keep or lower an approval. Use approve, hold (keep the results unpublished for a human) or "
                   "reject. " + "{expected}" + " Give short reasons, and in flagged_keys only keys listed under "
                   "affected_keys."),
}
# Spec §6: these soft flags are published with the row, so on their own they are no reason to hold.
_EXPECTED_FLAGS = ("The soft flags partial, modis_partial and mixed_reference are expected in near-real-time "
                   "runs: the current month is not over yet, the first month polled starts mid-month, and the "
                   "month in which S-NPP ended mixes S-NPP with its NOAA successor as reference, by design. These rows are "
                   "published with their flags, so these flags alone are not a reason to hold or reject. Hold or "
                   "reject only for a real problem: implausible counts or drops, drift above its threshold, a "
                   "drop share far above its recent level, or another reviewer's concrete concern.")
_REVIEWERS["validation"] = (*_REVIEWERS["validation"][:2],
                            _REVIEWERS["validation"][2].replace("{expected}", _EXPECTED_FLAGS))
_REVIEWERS["harmonization"] = (*_REVIEWERS["harmonization"][:2], _REVIEWERS["harmonization"][2]
                               + " Partial months and the mixed reference of the month S-NPP ended are expected; "
                               "note them only if something about them looks wrong.")
_COMMON_RULES = (" Quote numbers only exactly as the summary gives them and never compute new ones. "
                 "A detection is a satellite thermal hotspot, not one wildfire. Answer at once, in one reply.")
_PAYLOAD_HEADER = "\n\nSummary (JSON):\n"
_VERDICT_FORMAT = ('Only a JSON object, no other text: {"decision": "approve" | "hold" | "reject", '
                   '"reasons": [short strings], "flagged_keys": [[cy, cx, year, month], ...]}')
REVIEWER_ROLES = tuple(role for role, _, _ in _REVIEWERS.values())
REQUESTS_PER_ATTEMPT = len(_REVIEWERS)  # one answer per agent


def task_descriptions(payloads: Mapping[str, str]) -> dict[str, str]:
    """Each reviewer's whole task prompt text: its wording, the common rules, then its payload."""
    return {name: text + _COMMON_RULES + _PAYLOAD_HEADER + payloads[name]
            for name, (_, _, text) in _REVIEWERS.items()}


def build_crew(llm, payloads: Mapping[str, str]):
    """The three-agent sequential crew over `payloads`, each put directly into its task prompt.

    Agents have no tools and `max_iter = 1`; tasks have no `output_pydantic`, so CrewAI makes no
    conversion request. Memory, knowledge, planning, embeddings, delegation and caching are off
    (reasoning and code execution stay at their off defaults), and every agent uses `llm`
    explicitly (no default provider). Context-window summarisation is off too: it would make
    extra LLM calls. Payloads are never interpolated: kickoff() gets no inputs.
    """
    import_crewai()
    from crewai import Agent, Crew, Process, Task

    descriptions = task_descriptions(payloads)
    agents, tasks = [], []
    for name, (role, goal, _) in _REVIEWERS.items():
        agent = Agent(role=role, goal=goal, backstory="A careful reviewer of EmberGuard's automated FIRMS processing.",
                      llm=llm, tools=[], max_iter=MAX_ITER, max_retry_limit=0, allow_delegation=False,
                      verbose=False, cache=False, memory=None, knowledge_sources=None, embedder=None,
                      planning=False, inject_date=False, respect_context_window=False,
                      use_better_compaction=False)
        is_verdict = name == "validation"
        tasks.append(Task(description=descriptions[name], agent=agent, context=list(tasks) if is_verdict else [],
                          expected_output=_VERDICT_FORMAT if is_verdict else "Up to three short notes."))
        agents.append(agent)
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, memory=False, planning=False,
                cache=False, verbose=False, tracing=False, share_crew=False, embedder=None)


def review_attempt(ctx: AttemptContext, *, settings, payloads: Mapping[str, str], affected_keys,
                   llm_factory: LLMFactory) -> Verdict:
    """One model's whole crew run; raises on any failure (run_with_fallback then tries the next).

    At most one request per agent (limit_requests). The verdict is the Validation reviewer's raw
    final answer; its numbers are checked against exactly the payloads put into the prompts.
    """
    llm = llm_factory(ctx.model, settings, timeout_s=ctx.budget_s)
    limit_requests(llm, REVIEWER_ROLES)
    try:
        out = build_crew(llm, payloads).kickoff()
        return validate_verdict(out.tasks_output[-1].raw, affected_keys, list(payloads.values()))
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
    quota_check: QuotaCheck = free_model_quota
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
        remaining = self.quota_check(self.settings) if self.settings.openrouter_quota_check else None
        if remaining is not None and remaining < REQUESTS_PER_ATTEMPT:
            # Not enough left for one attempt: no model is built, the gates decide (D3).
            self.ai_review = "unavailable"
            return {"ai_review": "unavailable", "models": len(models), "attempts": 0, "model": None,
                    "verdict": None, "reason": "daily_quota", "quota_remaining": remaining,
                    "requests_per_attempt": REQUESTS_PER_ATTEMPT}
        payloads = review_payloads(self.result, self.affected_keys, self.settings.review_summary_max_chars)
        verdict, attempts = run_with_fallback(
            models, lambda ctx: review_attempt(ctx, settings=self.settings, payloads=payloads,
                                               affected_keys=self.affected_keys, llm_factory=self.llm_factory),
            deadline_s=self.settings.review_deadline_s, secrets=(self.settings.openrouter_api_key,),
            clock=self.clock, now=self.now)
        self.verdict, self.attempts = verdict, tuple(a.to_dict() for a in attempts)
        self.ai_review = "completed" if verdict is not None else "unavailable"
        summary = {"ai_review": self.ai_review, "models": len(models), "attempts": len(attempts),
                   "model": attempts[-1].model if verdict is not None else None,
                   "verdict": verdict.model_dump(mode="json") if verdict is not None else None,
                   "payload_chars": {k: len(v) for k, v in payloads.items()}, "quota_remaining": remaining}
        if verdict is None and attempts and attempts[-1].outcome == "quota_exhausted":
            summary["reason"] = "daily_quota"
        return summary

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
                   llm_factory: LLMFactory | None = None, quota_check: QuotaCheck | None = None,
                   clock: Callable[[], float] = time.monotonic,
                   utcnow: Callable[[], datetime] = utcnow) -> RunOutcome:
    """Process, review and decide one run. Arguments up to `recent_drop_shares` are `process()`'s.

    settings: settings.Settings (OPENROUTER_*, REVIEW_*). llm_factory: builds each attempt's LLM,
    default worker.llm.build_llm (tests inject a stub). quota_check: the free-model requests left
    today, default worker.llm.free_model_quota (only called with OPENROUTER_QUOTA_CHECK on and a
    model list). clock / utcnow: the deadline clock and the timestamps of steps and attempts.
    Raises ProcessingFailed if a step raises.
    """
    run = ProcessingRun.start(detections, affected_keys, calibration, mask=mask, boundary=boundary,
                              coverage=coverage, availability=availability, now=now, params=params,
                              recent_drop_shares=recent_drop_shares)
    job = _Job(run=run, affected_keys=affected_keys, settings=settings, llm_factory=llm_factory or build_llm,
               quota_check=quota_check or free_model_quota, clock=clock, now=utcnow)
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
