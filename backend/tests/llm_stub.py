"""A scripted stand-in for OpenRouter models: a CrewAI BaseLLM that never touches the network.

Each model id maps to a behaviour. The stub speaks CrewAI's ReAct text protocol: an agent's first
call gets "Action: <its tool>", the next one (after the tool result) a "Final Answer:".
Errors are the OpenAI SDK's own exception types, built offline, so classification is tested
against what CrewAI's OpenRouter client really raises.

Also the fixtures every crewai-importing test module uses (import them into the module):
`no_network` blocks sockets, `crewai_storage` keeps CrewAI's files in a temp dir.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field

import pytest

FAKE_KEY = "sk-or-v1-0000000000000000000000000000000000000000000000000000000000fake"
TOOLS = {"Filter reviewer": "filter_summary", "Harmonization reviewer": "harmonization_summary",
         "Validation reviewer": "validation_summary"}


class NetworkBlocked(OSError):
    """A test tried to open a network connection."""


def _blocked(*args, **kwargs):
    raise NetworkBlocked(f"network access blocked in tests: {args[1:2] or args[:1]}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No connect, name lookup or create_connection: no OpenRouter, telemetry or trace upload."""
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(scope="session", autouse=True)
def crewai_storage(tmp_path_factory):
    """CREWAI_STORAGE_DIR for this session, set before any test imports crewai (session-wide on
    purpose: CrewAI keeps singletons from its first import)."""
    old = os.environ.get("CREWAI_STORAGE_DIR")
    os.environ["CREWAI_STORAGE_DIR"] = str(tmp_path_factory.mktemp("crewai-storage"))
    yield os.environ["CREWAI_STORAGE_DIR"]
    if old is None:
        os.environ.pop("CREWAI_STORAGE_DIR", None)
    else:
        os.environ["CREWAI_STORAGE_DIR"] = old


@dataclass
class Call:
    model: str
    role: str | None
    text: str                     # every message's content, joined
    retry_guard: bool             # CrewAI's own rate-limit retry switched off (crewai_run_scope)
    console_suppressed: bool


@dataclass
class Script:
    """model id -> behaviour; `verdicts` overrides the verdict JSON a model returns."""
    behaviours: dict[str, str]
    verdicts: dict[str, dict] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    built: list[tuple[str, float | None]] = field(default_factory=list)   # (model, timeout_s)
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 11, "completion_tokens": 7,
                                                            "total_tokens": 18})

    def factory(self, model: str, settings, *, timeout_s: float | None = None):
        self.built.append((model, timeout_s))
        return stub_class()(model=model, script=self)


def _sdk_errors():
    import httpx
    import openai

    req = httpx.Request("POST", "https://openrouter.invalid/api/v1/chat/completions")
    return {
        "timeout": lambda: openai.APITimeoutError(request=req),
        "rate_limited": lambda: openai.RateLimitError(
            "Error code: 429 - rate limit exceeded", response=httpx.Response(429, request=req), body=None),
        "server_error": lambda: openai.InternalServerError(
            "Error code: 502", response=httpx.Response(502, request=req), body=None),
        "leaky": lambda: RuntimeError(f"provider rejected key {FAKE_KEY} for this request"),
    }


_CLASS = None


def stub_class():
    global _CLASS
    if _CLASS is not None:
        return _CLASS
    from worker.llm import import_crewai
    import_crewai()
    from crewai import BaseLLM
    from crewai.llms import retry
    from crewai_core.printer import should_suppress_console_output

    class StubLLM(BaseLLM):
        script: Script

        def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
                 from_agent=None, response_model=None, **kwargs):
            msgs = messages if isinstance(messages, list) else [{"role": "user", "content": messages}]
            text = "\n".join(str(m.get("content", "")) for m in msgs)
            role = getattr(from_agent, "role", None)
            s = self.script
            s.calls.append(Call(self.model, role, text, retry._active_llm_rate_limit_retry.get(),
                                should_suppress_console_output()))
            self._track_token_usage_internal(dict(s.usage))
            behaviour = s.behaviours[self.model]
            errors = _sdk_errors()
            if behaviour in errors:
                raise errors[behaviour]()
            if role is None:  # CrewAI's converter retrying a verdict that did not parse
                return "still not a verdict"
            if not any(m.get("role") == "assistant" for m in msgs):  # the tool result comes back as one
                return f"Thought: I need the summary.\nAction: {TOOLS[role]}\nAction Input: {{}}"
            if role != "Validation reviewer":
                return "Thought: I have reviewed it.\nFinal Answer: The summary looks consistent."
            if behaviour == "garbage":
                return "Thought: done.\nFinal Answer: looks fine to me, ship it"
            verdict = s.verdicts.get(self.model, {"decision": "approve", "reasons": ["all hard gates passed"],
                                                  "flagged_keys": []})
            return "Thought: done.\nFinal Answer: " + json.dumps(verdict)

        def supports_function_calling(self) -> bool:
            return False  # ReAct text, so the stub drives tool calls itself

    _CLASS = StubLLM
    return StubLLM
