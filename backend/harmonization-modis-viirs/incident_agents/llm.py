"""Groq chat client: strict JSON-schema output, paced on the rate-limit headers.

The free tier measured on 14 Nov 2026 for openai/gpt-oss-20b: 8,000 tokens per
minute, and 1,000 requests per day (one request comes back every 86.4 s). The client
waits when the remaining token budget is lower than the next call is expected to
use, and backs off on HTTP 429. The daily request cap is enforced by the pipeline.
"""

from __future__ import annotations

import json
import re
import threading
import time

import requests

from . import config

URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(RuntimeError):
    pass


def _seconds(value: str | None) -> float:
    """Parse Groq reset strings such as '2.265s', '1m26.4s', '450ms'."""
    if not value:
        return 0.0
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|s|m|h)", value):
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


class Groq:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 session: requests.Session | None = None):
        self.key = api_key or config.secret("groq")
        if not self.key:
            raise LLMError("no Groq key: set test_fire_groq_key (or GROQ_API_KEY) in .env")
        self.model = model or config.GROQ_MODEL
        self.s = session or requests.Session()
        self._lock = threading.Lock()
        self._tokens_left: float | None = None
        self._tokens_reset_at = 0.0
        self.total_tokens = 0
        self.calls = 0
        self.on_call = None  # optional hook, e.g. to count calls against the daily limit
        self._reasoning_param = True

    def _wait_budget(self, need: int) -> None:
        if self._tokens_left is None:
            return
        if self._tokens_left < need and time.monotonic() < self._tokens_reset_at:
            time.sleep(max(0.0, self._tokens_reset_at - time.monotonic()) + 0.2)

    def json(self, system: str, user: str, schema: dict, name: str = "result",
             max_tokens: int = 1500, expect_tokens: int = 1500, attempts: int = 4) -> dict:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": schema}},
        }
        last = ""
        for attempt in range(attempts):
            if self._reasoning_param and "gpt-oss" in self.model:
                body["reasoning_effort"] = "low"
            else:
                body.pop("reasoning_effort", None)
            with self._lock:
                self._wait_budget(expect_tokens)
                try:
                    r = self.s.post(URL, json=body, timeout=90,
                                    headers={"Authorization": f"Bearer {self.key}"})
                except requests.RequestException as e:
                    last = type(e).__name__
                    time.sleep(2 * (attempt + 1))
                    continue
                h = r.headers
                if h.get("x-ratelimit-remaining-tokens"):
                    self._tokens_left = float(h["x-ratelimit-remaining-tokens"])
                    self._tokens_reset_at = time.monotonic() + _seconds(h.get("x-ratelimit-reset-tokens"))
            if r.status_code == 429:
                wait = float(h.get("retry-after") or 0) or _seconds(h.get("x-ratelimit-reset-tokens")) or 5
                time.sleep(min(wait, 60) + 0.5)
                last = "HTTP 429"
                continue
            if r.status_code == 400 and "reasoning_effort" in r.text and self._reasoning_param:
                self._reasoning_param = False
                continue
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}: {config.redact(r.text[:200])}"
                if r.status_code >= 500:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise LLMError(last)
            data = r.json()
            self.total_tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
            self.calls += 1
            if self.on_call:
                self.on_call()
            content = data["choices"][0]["message"].get("content") or ""
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                last = "model returned invalid JSON"
                continue
        raise LLMError(f"Groq call failed after {attempts} attempts ({last})")
