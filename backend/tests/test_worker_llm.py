"""worker/llm.py: crewai import hygiene, the CrewAI 1.15 internals it relies on, the OpenRouter
LLM it builds (never called), error classification (including OpenRouter's daily-quota 429), the
fallback loop, key redaction, the per-attempt request guard and the free-model quota check.

No network: sockets are blocked (llm_stub.no_network) and nothing calls OpenRouter. The key is fake.
"""

import json
import logging
import os
import subprocess
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_stub import (DAILY_QUOTA_BODY, FAKE_KEY, UPSTREAM_BODY, NetworkBlocked, crewai_storage,  # noqa: F401
                      no_network, rate_limit_error)
from settings import Settings
from worker import llm
from worker.llm import (CREWAI_ENV, OUTCOMES, AttemptRecord, InvalidOutput, RequestRefused, build_llm,
                        classify_error, crewai_run_scope, free_model_quota, import_crewai, limit_requests,
                        make_redactor, run_with_fallback)

BACKEND = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
# -I ignores PYTHONPATH and the user site, so a subprocess sees only what it imports itself.
PRELUDE = f"import sys; sys.path[:0] = [{str(BACKEND)!r}, {str(BACKEND / 'tests')!r}]\n"


def run_isolated(code: str, env: dict | None = None, cwd: Path = BACKEND) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CREWAI", "XDG_", "OPENROUTER"))}
    return subprocess.run([sys.executable, "-I", "-c", PRELUDE + code], cwd=cwd, env={**base, **(env or {})},
                          capture_output=True, text=True, timeout=300)


def test_sockets_are_blocked():
    import socket
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(NetworkBlocked):
        socket.socket().connect(("127.0.0.1", 9))
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("openrouter.ai", 443)


def test_importing_worker_modules_does_not_import_crewai():
    out = run_isolated("import worker.llm, worker.review\n"
                       "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('crewai', 'crewai_core', 'openai'))\n"
                       "assert not bad, bad")
    assert out.returncode == 0, out.stderr


def test_crewai_env_is_set_before_crewai_is_imported(tmp_path):
    # A meta-path hook snapshots the environment at the moment `import crewai` starts.
    code = ("import os\n"
            "seen = {}\n"
            "class Spy:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'crewai' and not seen:\n"
            "            seen.update(os.environ)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Spy())\n"
            "from worker.llm import CREWAI_ENV, import_crewai\n"
            "assert 'crewai' not in sys.modules\n"
            "import_crewai()\n"
            "assert 'crewai' in sys.modules and seen, 'crewai was not imported through the hook'\n"
            "assert all(seen.get(k) == v for k, v in CREWAI_ENV.items()), seen\n"
            "assert os.path.isabs(seen['CREWAI_STORAGE_DIR']), seen['CREWAI_STORAGE_DIR']\n"
            "print(seen['CREWAI_STORAGE_DIR'])")
    out = run_isolated(code, {"HOME": str(tmp_path / "home"), "CREWAI_STORAGE_DIR": "rel"}, cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    # A relative CREWAI_STORAGE_DIR is made absolute (CrewAI would join it onto ~/.local/share).
    assert out.stdout.strip() == str(tmp_path / "rel")
    assert set(CREWAI_ENV) >= {"CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED", "CREWAI_TRACING_ENABLED"}


def test_crewai_writes_nothing_under_home(tmp_path):
    """Import plus a full stub-LLM review in a fresh process with an empty HOME and no CrewAI or
    XDG variables: the home stays empty, CrewAI's files land in the default storage dir."""
    home, tmp = tmp_path / "home", tmp_path / "tmp"
    home.mkdir()
    tmp.mkdir()
    code = ("import socket\n"
            "def blocked(*a, **k): raise OSError('network blocked')\n"
            "socket.socket.connect = socket.create_connection = socket.getaddrinfo = blocked\n"
            "import test_processing_pipeline as t\n"
            "from llm_stub import FAKE_KEY, Script\n"
            "from settings import Settings\n"
            "from worker.review import run_processing\n"
            "s = Settings(openrouter_api_key=FAKE_KEY, openrouter_models=('m',))\n"
            "out = run_processing(t.detections(), t.KEYS, t.CAL, mask=t.mask(), boundary=None, coverage=t.COVERAGE,\n"
            "                     availability=t.AVAIL, now=t.NOW, settings=s, llm_factory=Script({'m': 'ok'}).factory)\n"
            "assert (out.decision, out.ai_review) == ('approve', 'completed'), out\n"
            "import os; print(os.environ['CREWAI_STORAGE_DIR'])")
    out = run_isolated(code, {"HOME": str(home), "TMPDIR": str(tmp)})
    assert out.returncode == 0, out.stderr
    assert sorted(p.relative_to(home).as_posix() for p in home.rglob("*")) == []
    storage = Path(out.stdout.strip())
    assert storage == tmp / f"emberguard-crewai-{os.getuid()}"
    assert (storage / "credentials" / "secret.key").is_file()   # what CrewAI would put in ~/.local/share


def test_private_crewai_hooks_still_exist():
    # requirements-worker.txt pins crewai <1.16 because of these; a new version must keep them.
    crewai = import_crewai()
    from crewai.llms import retry
    from crewai_core import printer, settings as core_settings
    from crewai_core.token_manager import TokenManager

    major, minor, patch = (int(p) for p in crewai.__version__.split(".")[:3])
    assert (1, 15, 41) <= (major, minor, patch) < (1, 16, 0)
    assert isinstance(retry._active_llm_rate_limit_retry, ContextVar)
    assert isinstance(printer._suppress_console_output, ContextVar)
    storage = Path(os.environ["CREWAI_STORAGE_DIR"])
    assert core_settings.DEFAULT_CONFIG_PATH == storage / "config" / "settings.json"
    assert TokenManager._get_secure_storage_path() == storage / "credentials"


def test_run_scope_sets_and_resets_the_crewai_hooks():
    import_crewai()
    from crewai.llms import retry
    from crewai_core.printer import should_suppress_console_output

    assert not retry._active_llm_rate_limit_retry.get() and not should_suppress_console_output()
    with crewai_run_scope():
        assert retry._active_llm_rate_limit_retry.get() and should_suppress_console_output()
    assert not retry._active_llm_rate_limit_retry.get() and not should_suppress_console_output()
    with pytest.raises(RuntimeError), crewai_run_scope():
        raise RuntimeError("boom")
    assert not retry._active_llm_rate_limit_retry.get() and not should_suppress_console_output()


def test_build_llm_targets_openrouter_without_a_request():
    s = Settings(openrouter_api_key=FAKE_KEY, openrouter_models=("vendor/model",), openrouter_timeout_s=60,
                 openrouter_retries=2, openrouter_base_url="https://openrouter.example/api/v1")
    model = build_llm("vendor/model", s, timeout_s=12.5)
    assert type(model).__name__ == "OpenAICompatibleCompletion"
    assert (model.model, model.provider, model.base_url) == ("vendor/model", "openrouter",
                                                             "https://openrouter.example/api/v1")
    assert (model.timeout, model.max_retries, model.temperature, model.stream) == (12.5, 2, 0, False)
    assert build_llm("vendor/model", s).timeout == 60                   # no budget given: the setting
    assert build_llm("vendor/model", s, timeout_s=500).timeout == 60   # the budget only lowers it
    # CrewAI keeps the key in the model's repr, so the guard is redaction of everything that leaves
    # an attempt (messages, logs), never the object itself.
    assert FAKE_KEY not in make_redactor([FAKE_KEY])(repr(model))


def _sdk(name):
    import httpx
    import openai
    req = httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions")
    return {"timeout": openai.APITimeoutError(request=req),
            "rate": openai.RateLimitError("429", response=httpx.Response(429, request=req), body=None),
            "server": openai.InternalServerError("502", response=httpx.Response(502, request=req), body=None),
            "connection": openai.APIConnectionError(request=req)}[name]


def _wrapped(outer, inner):
    try:
        try:
            raise inner
        except Exception as e:
            raise outer from e
    except Exception as e:
        return e


@pytest.mark.parametrize("make,outcome", [
    (lambda: _sdk("timeout"), "timeout"),
    (lambda: _wrapped(ConnectionError("LLM call failed"), _sdk("timeout")), "timeout"),   # CrewAI's re-raise
    (lambda: TimeoutError("slow"), "timeout"),
    (lambda: _sdk("rate"), "rate_limited"),
    (lambda: _wrapped(RuntimeError("wrapped"), _sdk("rate")), "rate_limited"),
    (lambda: rate_limit_error(UPSTREAM_BODY), "rate_limited"),                 # a model's upstream limit
    (lambda: rate_limit_error(DAILY_QUOTA_BODY), "quota_exhausted"),           # the key's daily free limit
    (lambda: _wrapped(RuntimeError("LLM call failed"), rate_limit_error(DAILY_QUOTA_BODY)), "quota_exhausted"),
    (lambda: RuntimeError("free-models-per-day"), "error"),                    # the marker alone is not a 429
    (lambda: RequestRefused("second request"), "invalid_output"),
    (lambda: _sdk("server"), "http_error"),
    (lambda: _sdk("connection"), "http_error"),
    (lambda: InvalidOutput("bad verdict"), "invalid_output"),
    (lambda: _wrapped(RuntimeError("crew failed"), InvalidOutput("bad")), "invalid_output"),
    (lambda: RuntimeError("anything else"), "error"),
])
def test_classify_error(make, outcome):
    assert classify_error(make()) == outcome and outcome in OUTCOMES


def test_redactor_removes_the_key_and_any_openrouter_key():
    redact = make_redactor([FAKE_KEY, None, ""])
    assert redact(f"bad key {FAKE_KEY}!") == "bad key ***!"
    assert redact("other sk-or-v1-abcdef0123456789 here") == "other *** here"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_fallback_stops_at_the_first_success_and_records_every_attempt():
    clock, tried = FakeClock(), []

    def run_one(ctx):
        tried.append((ctx.model, ctx.budget_s))
        clock.t += 2.0
        ctx.usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        if ctx.model == "a":
            raise _sdk("rate")
        return f"verdict from {ctx.model}"

    result, attempts = run_with_fallback(["a", "b", "c"], run_one, deadline_s=10, clock=clock, now=lambda: T0)
    assert result == "verdict from b" and tried == [("a", 10.0), ("b", 8.0)]   # c never started
    assert [a.outcome for a in attempts] == ["rate_limited", "ok"]
    assert attempts[0].error_class == "RateLimitError" and attempts[0].latency_ms == 2000
    assert attempts[0].total_tokens == 8 and attempts[1].prompt_tokens == 5   # usage kept on failure too
    d = attempts[0].to_dict()
    assert json.loads(json.dumps(d)) == d and d["started_at"] == T0.isoformat()
    assert set(d) == {"model", "started_at", "latency_ms", "outcome", "error_class", "error", "prompt_tokens",
                      "completion_tokens", "total_tokens"}


def test_no_model_is_started_after_the_deadline(caplog):
    clock, tried = FakeClock(), []

    def run_one(ctx):
        tried.append(ctx.model)
        clock.t += 7.0
        raise TimeoutError("slow model")

    with caplog.at_level(logging.WARNING, logger="worker.llm"):
        result, attempts = run_with_fallback(["a", "b", "c", "d"], run_one, deadline_s=10, clock=clock)
    assert result is None and tried == ["a", "b"] and [a.outcome for a in attempts] == ["timeout", "timeout"]
    assert any(r.getMessage().startswith("review deadline used up") and r.skipped == 2 for r in caplog.records)


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan")])
def test_deadline_must_be_finite_and_positive(bad):
    with pytest.raises(ValueError):
        run_with_fallback(["a"], lambda ctx: None, deadline_s=bad)


def test_the_key_never_reaches_attempts_or_logs(caplog, capfd):
    provider_log = logging.getLogger("crewai.fake_provider")

    def run_one(ctx):
        provider_log.error("Error executing listener call_llm_and_parse: bad key %s", FAKE_KEY)
        try:
            raise RuntimeError(f"401 for key {FAKE_KEY}")
        except RuntimeError:
            provider_log.exception("provider failed")
            raise

    with caplog.at_level(logging.DEBUG):
        _, attempts = run_with_fallback(["a"], run_one, deadline_s=10, secrets=[FAKE_KEY])
    rec = attempts[0]
    assert rec.outcome == "error" and rec.error == "401 for key ***"
    assert FAKE_KEY not in json.dumps([a.to_dict() for a in attempts])
    assert "***" in caplog.text and FAKE_KEY not in caplog.text
    out, err = capfd.readouterr()
    assert FAKE_KEY not in out + err
    # The filter is removed afterwards: it only covers the review.
    assert all(not isinstance(f, llm._RedactFilter) for h in logging.getLogger().handlers for f in h.filters)


# ----------------------------------------------------------------------------- daily quota 429

def test_fallback_stops_after_a_daily_quota_429(caplog):
    tried = []

    def run_one(ctx):
        tried.append(ctx.model)
        raise rate_limit_error(DAILY_QUOTA_BODY if ctx.model == "b" else UPSTREAM_BODY)

    with caplog.at_level(logging.WARNING, logger="worker.llm"):
        result, attempts = run_with_fallback(["a", "b", "c", "d"], run_one, deadline_s=10)
    assert result is None and tried == ["a", "b"]   # an upstream 429 moves on; the daily quota stops the chain
    assert [a.outcome for a in attempts] == ["rate_limited", "quota_exhausted"]
    assert attempts[1].error_class == "RateLimitError" and "free-models-per-day" in attempts[1].error
    assert any(r.getMessage().startswith("daily free-model quota exhausted") and r.skipped == 2
               for r in caplog.records)


# ----------------------------------------------------------------------------- request guard

class FakeAgent:
    def __init__(self, role):
        self.role = role


class FakeLLM:
    def __init__(self):
        self.sent = []

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None, from_agent=None,
             response_model=None, **kwargs):
        self.sent.append(getattr(from_agent, "role", None))
        return "Final Answer: ok"


def test_limit_requests_allows_one_request_per_agent_and_nothing_else():
    fake = FakeLLM()
    sent = limit_requests(fake, ("A", "B"))
    assert fake.call([], from_agent=FakeAgent("A")) == "Final Answer: ok"
    with pytest.raises(RequestRefused, match="second request"):
        fake.call([], from_agent=FakeAgent("A"))
    with pytest.raises(RequestRefused, match="outside an agent"):
        fake.call([], callbacks=[])                       # how CrewAI forces a final answer
    with pytest.raises(RequestRefused, match="outside an agent"):
        fake.call([], from_agent=FakeAgent("Someone else"))
    fake.call([], from_agent=FakeAgent("B"))
    assert sent == ["A", "B"] and fake.sent == ["A", "B"]   # refused requests never reached the client
    assert classify_error(RequestRefused("x")) == "invalid_output"


def test_limit_requests_wraps_the_real_openrouter_llm_without_a_request():
    import asyncio

    s = Settings(openrouter_api_key=FAKE_KEY, openrouter_models=("vendor/model",))
    model = build_llm("vendor/model", s)
    limit_requests(model, ("A",))
    with pytest.raises(RequestRefused):
        model.call([{"role": "user", "content": "hi"}], callbacks=[])
    with pytest.raises(RequestRefused):
        asyncio.run(model.acall([{"role": "user", "content": "hi"}]))
    assert model.model == "vendor/model"   # still the same pydantic model, fields untouched


# ----------------------------------------------------------------------------- quota check

def _resp(status=200, payload=None, content=None):
    import httpx
    req = httpx.Request("GET", "https://openrouter.example/api/v1/key")
    if content is not None:
        return httpx.Response(status, content=content, request=req)
    return httpx.Response(status, json=payload, request=req)


QUOTA_SETTINGS = Settings(openrouter_api_key=FAKE_KEY, openrouter_models=("vendor/model",),
                          openrouter_base_url="https://openrouter.example/api/v1/")


def test_free_model_quota_reads_the_key_endpoint():
    seen = []

    def get(url, headers, timeout):
        seen.append((url, headers, timeout))
        return _resp(payload={"data": {"label": "x", "free_model_daily_requests": {"limit": 50, "remaining": 47}}})

    assert free_model_quota(QUOTA_SETTINGS, http_get=get) == 47
    assert seen == [("https://openrouter.example/api/v1/key", {"Authorization": f"Bearer {FAKE_KEY}"}, 10.0)]


def _raises(exc):
    def get(url, headers, timeout):
        raise exc
    return get


def _timeout():
    import httpx
    return httpx.ReadTimeout("timed out", request=httpx.Request("GET", "https://openrouter.example/api/v1/key"))


@pytest.mark.parametrize("get", [
    lambda url, headers, timeout: _resp(401, {"error": {"message": f"bad key {FAKE_KEY}"}}),
    lambda url, headers, timeout: _resp(500, {"error": "down"}),
    lambda url, headers, timeout: _resp(content=b"<html>not json</html>"),
    lambda url, headers, timeout: _resp(payload={"data": {}}),
    lambda url, headers, timeout: _resp(payload={"data": {"free_model_daily_requests": {"remaining": "12"}}}),
    lambda url, headers, timeout: _resp(payload={"data": {"free_model_daily_requests": {"remaining": True}}}),
    lambda url, headers, timeout: _resp(payload={"data": {"free_model_daily_requests": {"remaining": None}}}),
    lambda url, headers, timeout: _resp(payload=[1, 2]),
    _raises(_timeout()),
    _raises(RuntimeError(f"connection reset for {FAKE_KEY}")),
])
def test_free_model_quota_is_none_on_any_failure_and_never_logs_the_key(get, caplog, capfd):
    with caplog.at_level(logging.DEBUG):
        assert free_model_quota(QUOTA_SETTINGS, http_get=get) is None
    assert any(r.getMessage() == "free-model quota check failed" for r in caplog.records)
    assert FAKE_KEY not in caplog.text and "sk-or-" not in caplog.text
    out, err = capfd.readouterr()
    assert FAKE_KEY not in out + err


def test_free_model_quota_without_a_key_makes_no_request():
    def get(*a, **k):
        raise AssertionError("requested")

    assert free_model_quota(Settings(), http_get=get) is None


def test_free_model_quota_default_client_meets_the_blocked_network():
    assert free_model_quota(QUOTA_SETTINGS) is None   # httpx.get, sockets blocked: a failure, so None
