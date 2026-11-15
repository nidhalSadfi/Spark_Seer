"""Paths, secrets and spending caps. Secret values are never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT / ".env"
DATA_DIR = Path(os.environ.get("INCIDENT_DATA_DIR", PROJECT / "data" / "incidents"))
DB_PATH = DATA_DIR / "incidents.sqlite"

USER_AGENT = "fire-incident-agents/0.1 (research prototype)"

# Accepted names for each secret, first match wins (environment, then .env).
SECRET_NAMES = {
    "groq": ("GROQ_API_KEY", "test_fire_groq_key"),
    "twitterapi": ("TWITTERAPI_IO_KEY", "twitterapi"),
    "bsky_handle": ("BSKY_HANDLE", "bsky_handle"),
    "bsky_app_password": ("BSKY_APP_PASSWORD", "bsky_app_password", "bsky_password"),
}


def _read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out.setdefault(k.strip(), v)
    return out


_env_cache: dict[str, str] | None = None


def secret(name: str) -> str | None:
    global _env_cache
    if _env_cache is None:
        _env_cache = _read_env_file()
    for key in SECRET_NAMES[name]:
        if os.environ.get(key):
            return os.environ[key]
        if _env_cache.get(key):
            return _env_cache[key]
    return None


def all_secret_values() -> list[str]:
    vals = [secret(n) for n in SECRET_NAMES]
    vals += [v for k, v in (_env_cache or {}).items() if "key" in k.lower() or "pass" in k.lower()]
    return [v for v in vals if v and len(v) >= 6]


def redact(text: str) -> str:
    for v in all_secret_values():
        text = text.replace(v, "<redacted>")
    return text


@dataclass(frozen=True)
class Caps:
    """Hard limits that protect free quotas. Override with env vars of the same name."""

    TWITTER_PAGES_PER_RUN: int = 4        # 20 posts per page
    TWITTER_PAGES_PER_DAY: int = 40
    BLUESKY_LIMIT_PER_QUERY: int = 50
    GDELT_MAX_RECORDS: int = 50
    JUDGE_BATCH: int = 8
    JUDGE_MAX_ITEMS_PER_RUN: int = 120
    FIRMS_CHECKS_PER_RUN: int = 10
    GROQ_CALLS_PER_DAY: int = 900          # free tier: 1,000 requests per day
    SATELLITE_TICKETS_PER_ZONE: int = 20
    REPLAY_MAX_FIRES: int = 6
    MIN_SCHEDULE_MINUTES: int = 15


def caps() -> Caps:
    base = Caps()
    kw = {}
    for f in base.__dataclass_fields__:
        if os.environ.get(f, "").isdigit():
            kw[f] = int(os.environ[f])
    return Caps(**kw) if kw else base


GROQ_MODEL = os.environ.get("INCIDENT_GROQ_MODEL", "openai/gpt-oss-20b")
