"""worker/review.py on SYNTHETIC data with stub models (llm_stub): the flow's fixed step order and
its equality with processing.process(), the fallback chain, the deadline, decide() (downgrade
only), validate_verdict (I8), the agents' inputs, the disabled path (no crewai) and key hygiene.

No network: sockets are blocked (llm_stub.no_network); no test calls OpenRouter; the key is fake.
"""

import json
import pickle
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

import test_processing_pipeline as synth
from api.passport import allowed_numbers
from llm_stub import FAKE_KEY, NetworkBlocked, Script, crewai_storage, no_network  # noqa: F401 - fixtures
from processing import pipeline
from processing.gates import GateResult
from processing.pipeline import STAGE_ORDER, ProcessingRun, process
from settings import Settings
from worker import review
from worker.llm import InvalidOutput
from worker.review import (STEPS, TRUNCATED, Verdict, decide, review_payloads, run_processing, validate_verdict)

BACKEND = Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND / "requirements.txt"
WORKER_REQUIREMENTS = BACKEND / "requirements-worker.txt"
KEYS = synth.KEYS
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def settings(*models, **kw) -> Settings:
    return Settings(openrouter_api_key=FAKE_KEY if models else None, openrouter_models=models, **kw)


def inputs(**kw) -> dict:
    args = dict(mask=synth.mask(), boundary=None, coverage=synth.COVERAGE, availability=synth.AVAIL, now=synth.NOW)
    args.update(kw)
    return args


def run(script: Script | None, models=(), **kw):
    s = kw.pop("settings", None) or settings(*models)
    return run_processing(synth.detections(), KEYS, synth.CAL, **inputs(), settings=s,
                          llm_factory=script.factory if script else None, **kw)


def test_sockets_are_blocked():
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("openrouter.ai", 443))


# ----------------------------------------------------------------------------- flow order

def test_steps_are_the_pipeline_stages_then_review_and_decide():
    assert STEPS == STAGE_ORDER + ("review", "decide")
    assert STAGE_ORDER == ("filter", "aggregate", "harmonize", "completeness", "gates", "drift")


def test_flow_runs_every_step_in_order_and_matches_process(monkeypatch):
    threads = []
    real_step = review._Job.step

    def spy(self, name):
        threads.append(threading.current_thread() is threading.main_thread())
        return real_step(self, name)

    monkeypatch.setattr(review._Job, "step", spy)
    script = Script({"m": "ok"})
    out = run(script, ("m",))
    assert [s["name"] for s in out.steps] == list(STEPS) and all(s["status"] == "ok" for s in out.steps)
    assert threads == [False] * len(STEPS)  # each step ran as a CrewAI flow method, not in this thread
    expected = process(synth.detections(), KEYS, synth.CAL, **inputs())
    assert json.dumps(out.result.summary()) == json.dumps(expected.summary())
    assert pickle.dumps(out.result.staged) == pickle.dumps(expected.staged)
    by_name = {s["name"]: s for s in out.steps}
    for name in STAGE_ORDER:
        if name != "gates":
            assert by_name[name]["summary"] == expected.stage_summaries[name]
    assert by_name["gates"]["summary"]["passed"] is True and len(by_name["gates"]["summary"]["gates"]) == 4
    assert (out.decision, out.ai_review) == ("approve", "completed")
    assert by_name["decide"]["summary"] == {"decision": "approve", "ai_review": "completed", "basis": "verdict",
                                            "gates_passed": True}
    assert json.loads(json.dumps(list(out.steps))) == list(out.steps)


def test_stages_cannot_be_skipped_reordered_or_repeated():
    r = ProcessingRun.start(synth.detections(), KEYS, synth.CAL, **inputs())
    with pytest.raises(RuntimeError, match="out of order"):
        r.run_stage("aggregate")
    r.run_stage("filter")
    with pytest.raises(RuntimeError, match="out of order"):
        r.run_stage("filter")
    with pytest.raises(RuntimeError, match="incomplete"):
        r.result()
    job = review._Job(run=r, affected_keys=KEYS, settings=settings(), llm_factory=None, clock=lambda: 0.0,
                      now=lambda: synth.NOW)
    with pytest.raises(RuntimeError, match="out of order"):
        job.step("review")


def test_a_failing_stage_stops_the_flow_with_its_step_records(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("harmonize broke")

    monkeypatch.setattr(pipeline, "harmonize", broken)
    with pytest.raises(review.ProcessingFailed) as info:
        run(Script({"m": "ok"}), ("m",))
    assert info.value.step == "harmonize"
    assert [(s["name"], s["status"]) for s in info.value.steps] == [("filter", "ok"), ("aggregate", "ok"),
                                                                     ("harmonize", "failed")]


# ----------------------------------------------------------------------------- fallback chain

def test_fallback_chain_with_stub_models():
    invented = {"decision": "approve", "reasons": ["98765 detections were dropped"], "flagged_keys": []}
    foreign = {"decision": "hold", "reasons": ["a cell looks odd"], "flagged_keys": [[1, 2, 2026, 10]]}
    good = {"decision": "hold", "reasons": ["G2 conservation holds with 17 MODIS detections",
                                            "2026-11 is partial and its reference is pending"],
            "flagged_keys": [[73, 18, 2026, 11]]}
    script = Script({"timeout": "timeout", "rate": "rate_limited", "garbage": "garbage", "invented": "ok",
                     "foreign": "ok", "good": "ok", "never": "ok"},
                    verdicts={"invented": invented, "foreign": foreign, "good": good})
    out = run(script, ("timeout", "rate", "garbage", "invented", "foreign", "good", "never"))
    got = [(a["model"], a["outcome"], a["error_class"]) for a in out.attempts]
    assert got == [("timeout", "timeout", "APITimeoutError"), ("rate", "rate_limited", "RateLimitError"),
                   ("garbage", "invalid_output", "InvalidOutput"), ("invented", "invalid_output", "InvalidOutput"),
                   ("foreign", "invalid_output", "InvalidOutput"), ("good", "ok", None)]
    errors = {a["model"]: a["error"] for a in out.attempts}
    assert "does not parse" in errors["garbage"] and "I8" in errors["invented"] and "98765" in errors["invented"]
    assert "outside the affected set" in errors["foreign"]
    # The first success stops the chain: "never" is not built, let alone called.
    assert [m for m, _ in script.built] == ["timeout", "rate", "garbage", "invented", "foreign", "good"]
    assert "never" not in {c.model for c in script.calls}
    assert (out.decision, out.ai_review) == ("hold", "completed")
    assert out.verdict == Verdict.model_validate(good)
    assert all(a["total_tokens"] and a["prompt_tokens"] for a in out.attempts)   # stub usage, per attempt
    rev = next(s for s in out.steps if s["name"] == "review")["summary"]
    assert (rev["ai_review"], rev["model"], rev["attempts"], rev["models"]) == ("completed", "good", 6, 7)


def test_every_model_failing_publishes_on_the_gates_alone():
    out = run(Script({"a": "timeout", "b": "server_error"}), ("a", "b"))
    assert [a["outcome"] for a in out.attempts] == ["timeout", "http_error"]
    assert (out.decision, out.ai_review, out.verdict) == ("approve", "unavailable", None)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_no_model_is_started_after_the_review_deadline():
    clock = Clock()
    script = Script({"a": "timeout", "b": "timeout", "c": "ok"})

    def slow_factory(model, s, *, timeout_s=None):
        clock.t += 200.0   # each attempt "takes" 200 s of the injected clock; nothing sleeps
        return script.factory(model, s, timeout_s=timeout_s)

    out = run_processing(synth.detections(), KEYS, synth.CAL, **inputs(),
                         settings=settings("a", "b", "c", review_deadline_s=300, openrouter_timeout_s=60),
                         llm_factory=slow_factory, clock=clock)
    assert [a["model"] for a in out.attempts] == ["a", "b"]   # c would start at t=400 > 300
    # Each attempt is handed what is left of the deadline as its LLM timeout budget.
    assert script.built == [("a", 300.0), ("b", 100.0)]
    assert (out.decision, out.ai_review) == ("approve", "unavailable")


def test_review_runs_and_rejects_when_a_hard_gate_fails(monkeypatch):
    real = pipeline.run_gates

    def failing(*args, **kwargs):
        gates = real(*args, **kwargs)
        return [GateResult("G2_conservation", False, {"failures": {"count_mismatch": {"rows": 1}}}), *gates[1:]]

    monkeypatch.setattr(pipeline, "run_gates", failing)
    script = Script({"m": "ok"})   # the stub approves
    out = run(script, ("m",))
    assert not out.result.passed and script.calls   # the review still ran ...
    assert (out.decision, out.ai_review) == ("reject", "completed")   # ... and cannot upgrade the gate
    assert out.verdict.decision == "approve"
    out = run(Script({"m": "timeout"}), ("m",))
    assert (out.decision, out.ai_review) == ("reject", "unavailable")


def test_crewai_hooks_are_active_for_every_llm_call():
    script = Script({"m": "ok"})
    run(script, ("m",))
    assert script.calls and all(c.retry_guard and c.console_suppressed for c in script.calls)
    from crewai.llms import retry
    from crewai_core.printer import should_suppress_console_output
    assert not retry._active_llm_rate_limit_retry.get() and not should_suppress_console_output()


# ----------------------------------------------------------------------------- decide()

V = {d: Verdict(decision=d, reasons=["x"]) for d in ("approve", "hold", "reject")}


@pytest.mark.parametrize("gates_passed,verdict,ai_review,decision", [
    (False, V["approve"], "completed", "reject"),     # downgrade only: an approval cannot lift a failed gate
    (False, V["hold"], "completed", "reject"),
    (False, V["reject"], "completed", "reject"),
    (False, None, "unavailable", "reject"),
    (False, None, "disabled", "reject"),
    (True, V["approve"], "completed", "approve"),
    (True, V["hold"], "completed", "hold"),
    (True, V["reject"], "completed", "reject"),
    (True, None, "unavailable", "approve"),           # D3: every model failed
    (True, None, "disabled", "approve"),              # OPENROUTER_MODELS empty
])
def test_decide_table(gates_passed, verdict, ai_review, decision):
    d = decide(gates_passed, verdict, ai_review)
    assert (d.decision, d.ai_review) == (decision, ai_review)   # ai_review is "as it happened"


@pytest.mark.parametrize("verdict,ai_review", [(None, "completed"), (V["approve"], "unavailable"),
                                               (V["hold"], "disabled"), (None, "skipped")])
def test_decide_rejects_inconsistent_inputs(verdict, ai_review):
    with pytest.raises(ValueError):
        decide(True, verdict, ai_review)


# ----------------------------------------------------------------------------- validate_verdict

SHOWN = ['{"kept_by_family":{"MODIS":17},"months":{"2026-11":{"complete":false}},"error_pct":12.34}']


@pytest.mark.parametrize("raw", [
    {"decision": "hold", "reasons": ["17 MODIS detections", "2026-11 partial", "drift 12.3%"],
     "flagged_keys": [[73, 18, 2026, 11]]},
    '{"decision": "approve", "reasons": ["drift 12.34 is fine"]}',
    '```json\n{"decision": "reject", "reasons": ["no numbers here"], "flagged_keys": []}\n```',
    Verdict(decision="approve", reasons=["17 kept"]),
])
def test_valid_verdicts(raw):
    v = validate_verdict(raw, KEYS, SHOWN)
    assert isinstance(v, Verdict) and all(isinstance(k, tuple) for k in v.flagged_keys)


@pytest.mark.parametrize("raw,match", [
    ("not json", "does not parse"),
    ('{"decision": "publish", "reasons": []}', "does not parse"),
    ('{"decision": "approve"}', "does not parse"),
    ('{"decision": "approve", "reasons": [], "confidence": 0.9}', "does not parse"),   # extra fields refused
    ('{"decision": "approve", "reasons": [], "flagged_keys": [[73, 18, 2026]]}', "does not parse"),
    ({"decision": "hold", "reasons": [], "flagged_keys": [[73, 18, 2026, 12]]}, "outside the affected set"),
    ({"decision": "approve", "reasons": ["18 MODIS detections"]}, "I8"),
    ({"decision": "approve", "reasons": ["drift 12.4%"]}, "I8"),
])
def test_invalid_verdicts(raw, match):
    with pytest.raises(InvalidOutput, match=match):
        validate_verdict(raw, KEYS, SHOWN)


# ----------------------------------------------------------------------------- agent inputs

RAW_ROW_MARKERS = ("acq_time", "acq_date", "latitude", "longitude", "36.61", "36.62", "9.12", "9.13", "1200",
                   "instrument")


def test_agents_see_summaries_only_within_the_cap():
    cap = 1000
    script = Script({"m": "ok"})
    out = run(script, settings=settings("m", review_summary_max_chars=cap))
    assert out.ai_review == "completed"
    expected = process(synth.detections(), KEYS, synth.CAL, **inputs())
    payloads = review_payloads(expected, KEYS, cap)
    assert set(payloads) == {"filter", "harmonization", "validation"}
    for text in payloads.values():
        assert len(text) <= cap
        assert not any(m in text for m in RAW_ROW_MARKERS)
    # Every tool result an agent got is one of those capped payloads; prompts carry no raw rows.
    for call in script.calls:
        assert not any(m in call.text for m in RAW_ROW_MARKERS), call.role
    fingerprint = synth.CAL.fingerprint
    assert all(fingerprint not in c.text and fingerprint[:12] not in c.text for c in script.calls)
    assert any(payloads["validation"] in c.text for c in script.calls)


def test_truncation_is_marked_and_limits_the_i8_numbers():
    res = process(synth.detections(), KEYS, synth.CAL, **inputs())
    full = review_payloads(res, KEYS, 10**6)
    small = review_payloads(res, KEYS, 1000)
    assert not any(TRUNCATED in t for t in full.values())
    assert small["harmonization"].endswith(TRUNCATED) and len(small["harmonization"]) == 1000
    assert json.loads(full["validation"])["affected_keys"] == [list(k) for k in sorted(KEYS)]
    # A number the agents could only have seen past the cut is not allowed.
    seen = allowed_numbers({"shown": list(small.values())})
    unseen = [x for x in map(float, NUMBER.findall(full["harmonization"]))
              if not any(abs(abs(x) - a) < 1e-9 for a in seen)]
    assert unseen, "the cut should hide at least one number"
    verdict = {"decision": "hold", "reasons": [f"a value of {abs(unseen[0]):g}"]}
    assert validate_verdict(verdict, KEYS, [full["harmonization"]]).decision == "hold"
    with pytest.raises(InvalidOutput, match="I8"):
        validate_verdict(verdict, KEYS, list(small.values()))


# ----------------------------------------------------------------------------- disabled, isolation, keys

PRELUDE = f"import sys; sys.path[:0] = [{str(BACKEND)!r}, {str(BACKEND / 'tests')!r}]\n"


def isolated(code: str) -> subprocess.CompletedProcess:
    # -I: no PYTHONPATH, user site or cwd on sys.path, so earlier imports cannot mask anything.
    return subprocess.run([sys.executable, "-I", "-c", PRELUDE + code], cwd=BACKEND, capture_output=True,
                          text=True, timeout=300)


def test_empty_model_list_approves_without_importing_crewai():
    code = ("import json\n"
            "import test_processing_pipeline as t\n"
            "from settings import Settings\n"
            "from worker.review import run_processing\n"
            "out = run_processing(t.detections(), t.KEYS, t.CAL, mask=t.mask(), boundary=None, coverage=t.COVERAGE,\n"
            "                     availability=t.AVAIL, now=t.NOW, settings=Settings(openrouter_models=()))\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('crewai', 'crewai_core', 'openai'))\n"
            "print(json.dumps({'decision': out.decision, 'ai_review': out.ai_review, 'attempts': list(out.attempts),\n"
            "                  'steps': [s['name'] for s in out.steps], 'review': out.steps[-2]['summary'],\n"
            "                  'bad': bad}))")
    out = isolated(code)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert (got["decision"], got["ai_review"], got["attempts"], got["bad"]) == ("approve", "disabled", [], [])
    assert got["steps"] == list(STEPS) and got["review"] == {"ai_review": "disabled", "models": 0}


def test_disabled_run_matches_process_in_process():
    out = run(None)
    assert (out.decision, out.ai_review, out.verdict, out.attempts) == ("approve", "disabled", None, ())
    expected = process(synth.detections(), KEYS, synth.CAL, **inputs())
    assert json.dumps(out.result.summary()) == json.dumps(expected.summary())
    pd.testing.assert_frame_equal(out.result.staged, expected.staged)


def test_api_imports_neither_crewai_nor_the_worker():
    out = isolated("import api.main\n"
                   "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('crewai', 'crewai_core', 'worker'))\n"
                   "assert not bad, bad")
    assert out.returncode == 0, out.stderr


def test_crewai_is_a_worker_only_requirement():
    def names(path):
        return [line.split("#")[0].strip() for line in path.read_text().splitlines() if line.split("#")[0].strip()]

    assert not any(n.lower().startswith("crewai") for n in names(REQUIREMENTS))
    worker = names(WORKER_REQUIREMENTS)
    assert worker[0] == "-r requirements.txt" and "crewai>=1.15.41,<1.16" in worker


def test_the_key_never_appears_in_attempts_steps_or_logs(caplog, capfd):
    script = Script({"leaky": "leaky", "m": "ok"})
    with caplog.at_level("DEBUG"):
        out = run(script, ("leaky", "m"))
    assert [a["outcome"] for a in out.attempts] == ["error", "ok"]
    assert "***" in out.attempts[0]["error"]
    dumped = json.dumps([list(out.attempts), list(out.steps)])
    assert FAKE_KEY not in dumped and "sk-or-" not in dumped
    assert FAKE_KEY not in caplog.text
    stdout, stderr = capfd.readouterr()
    assert FAKE_KEY not in stdout + stderr
