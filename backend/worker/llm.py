"""The only place that builds LLM clients (spec §7): OpenRouter through CrewAI, tried model by model.

Everything that imports `crewai` goes through `import_crewai()`, which first switches CrewAI's
telemetry, tracing, version check and .env loading off and keeps its files out of the home
directory. Nothing here imports crewai at module import time, so `import worker.llm` stays cheap
and the API never pulls it in (I1).

`run_with_fallback` tries `OPENROUTER_MODELS` in order until one review succeeds or
`REVIEW_DEADLINE_S` is used up, and records every attempt (model, start, latency, outcome, error
class, redacted message, token counts). The OpenRouter API key never enters an attempt record,
a stored message or a log line: messages are redacted and log records are filtered while a
review runs.

CrewAI hooks used here are private (`crewai.llms.retry._active_llm_rate_limit_retry`,
`crewai_core.settings.DEFAULT_CONFIG_PATH`, `TokenManager._get_secure_storage_path`), hence the
`crewai>=1.15.41,<1.16` pin in requirements-worker.txt; tests/test_worker_llm.py checks each one.
"""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

log = logging.getLogger("worker.llm")

# Set in os.environ before the first `import crewai` (spec §7 privacy). The first two are the spec's;
# the rest stop the other outbound or side-loading behaviour CrewAI 1.15 has: trace upload to
# CrewAI AMP (otherwise enabled by a consent file in its storage dir), the PyPI version check, and
# `load_dotenv()` at `crewai.llm` import (settings come only from backend/settings.py, spec §12).
CREWAI_ENV = {
    "CREWAI_DISABLE_TELEMETRY": "true",
    "OTEL_SDK_DISABLED": "true",
    "CREWAI_TRACING_ENABLED": "false",
    "CREWAI_DISABLE_VERSION_CHECK": "true",
    "PYTHON_DOTENV_DISABLED": "true",
}
# CrewAI's own storage dir (task-output replay database, tracing consent file). Unset, CrewAI uses
# ~/.local/share/<name of the working directory>; an explicit CREWAI_STORAGE_DIR is kept. Per user,
# because the temp dir is shared.
CREWAI_STORAGE_DEFAULT = Path(tempfile.gettempdir()) / f"emberguard-crewai-{getattr(os, 'getuid', lambda: 0)()}"

OUTCOMES = ("ok", "timeout", "rate_limited", "http_error", "invalid_output", "error")
MAX_ERROR_CHARS = 300
# OpenRouter keys look like sk-or-v1-<hex>; redacted even if they are not the configured key.
_KEY_PATTERN = re.compile(r"sk-or-[A-Za-z0-9_-]{8,}")
_TIMEOUT_NAMES = ("APITimeoutError", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
                  "TimeoutException")
_HTTP_NAMES = ("APIStatusError", "APIConnectionError", "APIError", "HTTPStatusError", "TransportError",
               "NetworkError", "ConnectError", "RemoteProtocolError")

T = TypeVar("T")


class InvalidOutput(ValueError):
    """The model answered, but its verdict is unusable (does not parse, foreign key, invented number)."""


def crewai_storage_dir() -> Path:
    """CREWAI_STORAGE_DIR as an absolute path, set to the default first if unset.

    Absolute because CrewAI joins it onto ~/.local/share: a relative name would land in the home.
    """
    path = Path(os.environ.setdefault("CREWAI_STORAGE_DIR", str(CREWAI_STORAGE_DEFAULT))).absolute()
    os.environ["CREWAI_STORAGE_DIR"] = str(path)
    return path


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _keep_crewai_out_of_home(storage: Path) -> None:
    """Point the two files CrewAI 1.15 hard-codes under the home directory into `storage`.

    Importing crewai builds its trace listener, which creates ~/.local/share/crewai/credentials/
    secret.key (an encryption key for CrewAI AMP login tokens) and probes ~/.config/crewai for
    platform settings. Both come from Path.home(), with no environment switch; the worker uses
    neither AMP login nor platform settings. Runs before `import crewai`, from crewai_core only.
    """
    import crewai_core.settings as core_settings
    from crewai_core.token_manager import TokenManager

    core_settings.DEFAULT_CONFIG_PATH = storage / "config" / "settings.json"
    TokenManager._get_secure_storage_path = staticmethod(lambda: _private_dir(storage / "credentials"))


def import_crewai():
    """Import and return `crewai`, with telemetry and the other outbound features switched off first.

    The single entry point for every crewai import in the worker: callers do `import_crewai()` and
    only then `from crewai... import ...`. Sets CREWAI_ENV and CREWAI_STORAGE_DIR in os.environ.
    """
    for name, value in CREWAI_ENV.items():
        os.environ[name] = value
    _keep_crewai_out_of_home(crewai_storage_dir())
    import crewai

    return crewai


@contextmanager
def crewai_run_scope() -> Iterator[None]:
    """Context for one CrewAI flow or crew run.

    - Console output is suppressed (CrewAI prints Rich panels for every flow even with verbose off;
      the worker logs JSON lines to stdout).
    - CrewAI's own rate-limit retry is switched off. CrewAI 1.15 wraps every LLM `call` in up to 3
      tries with sleeps of up to 8 s, honouring an uncapped Retry-After. Retries are then only the
      HTTP client's OPENROUTER_RETRIES, and a rate limit moves on to the next model.

    Both are context variables, which CrewAI copies into the threads it runs flow steps and agents in.
    """
    import_crewai()
    from crewai.llms import retry as crewai_retry
    from crewai_core.printer import _suppress_console_output, set_suppress_console_output

    printer_token = set_suppress_console_output(True)
    retry_token = crewai_retry._active_llm_rate_limit_retry.set(True)
    try:
        yield
    finally:
        crewai_retry._active_llm_rate_limit_retry.reset(retry_token)
        _suppress_console_output.reset(printer_token)


def build_llm(model: str, settings, *, timeout_s: float | None = None):
    """A CrewAI LLM for one OpenRouter model id (e.g. "vendor/model").

    CrewAI 1.15 routes `provider="openrouter"` to its native OpenAI-compatible client (the OpenAI
    SDK pointed at OPENROUTER_BASE_URL) with the model id passed through unchanged; no LiteLLM.
    `timeout_s` caps the per-request timeout (the caller passes what is left of the deadline);
    `max_retries` is OPENROUTER_RETRIES, the only retry layer (see crewai_run_scope). Building it
    makes no request. The returned object shows the API key in its repr: never log it.
    """
    crewai = import_crewai()
    timeout = settings.openrouter_timeout_s
    if timeout_s is not None:
        timeout = max(0.001, min(timeout_s, timeout))
    return crewai.LLM(model=model, provider="openrouter", api_key=settings.openrouter_api_key,
                      base_url=settings.openrouter_base_url, timeout=timeout,
                      max_retries=settings.openrouter_retries, temperature=0, stream=False)


def token_usage(llm: Any) -> dict[str, int] | None:
    """Token counts the LLM instance reported over its lifetime, or None if it reported none."""
    try:
        u = llm.get_token_usage_summary()
    except Exception:  # noqa: BLE001 - usage is optional metadata
        return None
    if not (u.successful_requests or u.total_tokens or u.prompt_tokens or u.completion_tokens):
        return None
    return {"prompt_tokens": int(u.prompt_tokens), "completion_tokens": int(u.completion_tokens),
            "total_tokens": int(u.total_tokens)}


# ----------------------------------------------------------------------------- redaction

def make_redactor(secrets: Iterable[str | None]) -> Callable[[str], str]:
    """Replace every configured secret (and anything shaped like an OpenRouter key) with ***."""
    values = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact(text: str) -> str:
        for s in values:
            text = text.replace(s, "***")
        return _KEY_PATTERN.sub("***", text)

    return redact


class _RedactFilter(logging.Filter):
    def __init__(self, redact: Callable[[str], str]):
        super().__init__()
        self.redact = redact

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string is not ours to fix
            return True
        clean = self.redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


@contextmanager
def redacting_logs(redact: Callable[[str], str]) -> Iterator[None]:
    """Redact secrets from every record reaching the root logger's handlers, or Python's last-resort
    stderr handler when there are none (CrewAI logs provider error messages verbatim, e.g. "Error
    executing listener ...: <exception text>")."""
    flt = _RedactFilter(redact)
    handlers = list(logging.getLogger().handlers) + ([logging.lastResort] if logging.lastResort else [])
    for h in handlers:
        h.addFilter(flt)
    try:
        yield
    finally:
        for h in handlers:
            h.removeFilter(flt)


# ----------------------------------------------------------------------------- attempts

@dataclass(frozen=True)
class AttemptRecord:
    """One model's try at the review (spec §7 `llm_attempts`). Never holds the exception itself."""
    model: str
    started_at: datetime            # UTC
    latency_ms: int
    outcome: str                    # one of OUTCOMES
    error_class: str | None = None
    error: str | None = None        # short and redacted
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def to_dict(self) -> dict:
        return {"model": self.model, "started_at": self.started_at.isoformat(), "latency_ms": self.latency_ms,
                "outcome": self.outcome, "error_class": self.error_class, "error": self.error,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens}


@dataclass
class AttemptContext:
    """What `run_one` gets for one model: the model id, the seconds left before REVIEW_DEADLINE_S,
    and a slot for the token usage it observed (read even when the attempt fails)."""
    model: str
    budget_s: float
    usage: dict[str, int] | None = field(default=None)


def _chain(exc: BaseException) -> list[BaseException]:
    out, seen, cur = [], set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def _status(e: BaseException) -> int | None:
    code = getattr(e, "status_code", None)
    if code is None:
        code = getattr(getattr(e, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def classify_error(exc: BaseException) -> str:
    """Attempt outcome for an exception, looking through its cause chain (CrewAI re-raises the
    OpenAI SDK's APITimeoutError as ConnectionError, for example)."""
    chain = _chain(exc)
    names = [[c.__name__ for c in type(e).__mro__] for e in chain]
    if any(isinstance(e, InvalidOutput) for e in chain):
        return "invalid_output"
    if any(isinstance(e, TimeoutError) or any(n in _TIMEOUT_NAMES for n in ns) for e, ns in zip(chain, names)):
        return "timeout"
    if any(_status(e) == 429 or "RateLimitError" in ns for e, ns in zip(chain, names)):
        return "rate_limited"
    if any(_status(e) is not None or isinstance(e, ConnectionError) or any(n in _HTTP_NAMES for n in ns)
           for e, ns in zip(chain, names)):
        return "http_error"
    return "error"


def _short(text: str, redact: Callable[[str], str]) -> str:
    text = " ".join(redact(text).split())
    return text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS - 3] + "..."


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_with_fallback(models: Sequence[str], run_one: Callable[[AttemptContext], T], *, deadline_s: float,
                      secrets: Iterable[str | None] = (), clock: Callable[[], float] = time.monotonic,
                      now: Callable[[], datetime] = utcnow) -> tuple[T | None, tuple[AttemptRecord, ...]]:
    """Try `models` in order; return (first successful result, attempts) or (None, attempts).

    A model fails on any exception from `run_one` (timeout, rate limit, HTTP error, InvalidOutput,
    anything else) and the next one is tried. No model is started once `deadline_s` seconds have
    passed since the first; each attempt gets the time that is left as `ctx.budget_s`.
    """
    if not math.isfinite(deadline_s) or deadline_s <= 0:
        raise ValueError(f"deadline_s must be a finite number > 0, got {deadline_s!r}")
    redact = make_redactor(secrets)
    start = clock()
    attempts: list[AttemptRecord] = []
    for model in models:
        left = deadline_s - (clock() - start)
        if left <= 0:
            log.warning("review deadline used up; models not tried", extra={"deadline_s": deadline_s,
                        "skipped": len(models) - len(attempts)})
            break
        ctx = AttemptContext(model=model, budget_s=left)
        started, t0 = now(), clock()
        result, failure = None, None
        with redacting_logs(redact):
            try:
                result = run_one(ctx)
            except Exception as e:  # noqa: BLE001 - every failure moves on to the next model
                failure = e
        latency = max(0, int(round((clock() - t0) * 1000)))
        usage = ctx.usage or {}
        common = dict(model=model, started_at=started, latency_ms=latency, prompt_tokens=usage.get("prompt_tokens"),
                      completion_tokens=usage.get("completion_tokens"), total_tokens=usage.get("total_tokens"))
        if failure is None:
            attempts.append(AttemptRecord(outcome="ok", **common))
            log.info("review model succeeded", extra={"model": model, "latency_ms": latency})
            return result, tuple(attempts)
        rec = AttemptRecord(outcome=classify_error(failure), error_class=type(failure).__name__,
                            error=_short(str(failure) or type(failure).__name__, redact), **common)
        attempts.append(rec)
        log.warning("review model failed", extra={"model": model, "outcome": rec.outcome,
                                                   "error_class": rec.error_class, "latency_ms": latency})
    return None, tuple(attempts)
