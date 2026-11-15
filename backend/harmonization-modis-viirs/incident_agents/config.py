"""Paths, secrets and spending caps. Secret values are never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
try:
    REPO_ROOT = PROJECT.parents[1]  # backend/harmonization-modis-viirs -> the repo root, where the shared .env lives
except IndexError:  # the package used outside the monorepo
    REPO_ROOT = PROJECT
# Secrets: real environment variables first, then these files in order (the first file that has a name wins):
# the package's own .env, then the repo-root .env that the rest of the backend is configured from.
ENV_FILES = (PROJECT / ".env", REPO_ROOT / ".env")
ENV_FILE = ENV_FILES[0]
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
# The NASA FIRMS key is read by firms_tool, which only looks at the environment and the package's own
# .env: apply_env_files() puts these names from the files into the environment so it finds them.
FIRMS_NAMES = ("FIRMS_MAP_KEY_1", "FIRMS_MAP_KEY")


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
        elif " #" in v:  # an inline comment after an unquoted value, as docker compose and dotenv allow
            v = v.split(" #", 1)[0].rstrip()
        out.setdefault(k.strip(), v)
    return out


def _read_env_files(paths: tuple[Path, ...] | None = None) -> dict[str, str]:
    """Every name from the .env files; the first file that defines a name wins."""
    merged: dict[str, str] = {}
    for path in paths if paths is not None else ENV_FILES:
        for k, v in _read_env_file(path).items():
            merged.setdefault(k, v)
    return merged


_env_cache: dict[str, str] | None = None


def secret(name: str) -> str | None:
    global _env_cache
    if _env_cache is None:
        _env_cache = _read_env_files()
    for key in SECRET_NAMES[name]:
        if _env(key):
            return _env(key)
        if _env_cache.get(key):
            return _env_cache[key]
    return None


def _env(name: str) -> str:
    """An environment variable, trimmed. A .env sourced from a Windows (CRLF) file leaves a hidden \\r on every value."""
    return (os.environ.get(name) or "").strip()


def apply_env_files() -> None:
    """Make the FIRMS key from the .env files visible in the environment (without overriding it)."""
    global _env_cache
    if _env_cache is None:
        _env_cache = _read_env_files()
    for name in FIRMS_NAMES:
        if _env(name):
            os.environ[name] = _env(name)  # firms_tool reads os.environ itself: hand it the trimmed value
        elif _env_cache.get(name):
            os.environ[name] = _env_cache[name]


def key_status() -> dict[str, bool]:
    """Which keys the agents can see, as booleans only (a value is never returned)."""
    apply_env_files()
    return {
        "groq": bool(secret("groq")),
        "x": bool(secret("twitterapi")),
        "bluesky": bool(secret("bsky_handle") and secret("bsky_app_password")),
        "firms": any(_env(n) for n in FIRMS_NAMES),
    }


def env_files_found() -> dict[str, bool]:
    return {"agents_dir": ENV_FILES[0].is_file(), "repo_root": ENV_FILES[1].is_file()}


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

# Where the React dashboard (frontend/) runs in development and preview.
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173",
                        "http://localhost:4173", "http://127.0.0.1:4173")


def cors_origins() -> list[str]:
    """Browser origins allowed to use this API (the dashboard's Social Alerts page and its agent console).

    Comma-separated INCIDENT_CORS_ORIGINS overrides the default; an empty value allows none.
    """
    raw = os.environ.get("INCIDENT_CORS_ORIGINS")
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
