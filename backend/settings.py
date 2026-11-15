"""Typed settings for new backend code (worker, migrations, processing), read from environment variables.

All new code reads configuration through `Settings.from_env()` (spec §12). Variable names match
the root `.env.example`. Empty values count as unset, so `VAR=` lines in a .env file fall back to
the default. This module never loads a .env file itself.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from firms_tool.area_api import bbox_arg, resolve_map_key

Bbox = tuple[float, float, float, float]  # (west, south, east, north), degrees

# Spec §1: Tunisia + Algeria north of 34°N padded to 0.5° cells (one 60° FIRMS tile).
DEFAULT_REGION_BBOX: Bbox = (-2.5, 34.0, 11.5, 38.0)
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class SettingsError(ValueError):
    """A configuration value is missing, unparseable or out of range; the message names the variable."""


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(default=None, repr=False)  # repr=False: may hold a password
    firms_map_key: str | None = field(default=None, repr=False)
    firms_poll_interval_hours: float = 3.0
    firms_bbox: Bbox = DEFAULT_REGION_BBOX
    firms_max_backfill_days: int = 30
    firms_raw_dir: Path = Path("data/raw/firms")
    worker_lease_seconds: float = 300.0
    worker_tick_seconds: float = 60.0
    reconcile_day_of_month: int = 5
    # Processing (spec §5): unset files disable that rule, and the run reports it as disabled.
    country_boundary_file: Path | None = None
    static_mask_file: Path | None = None
    drift_warn_pct: float = 40.0
    drift_min_count: float = 50.0
    # Review (spec §7): an empty model list disables the AI review (crewai is then never imported).
    openrouter_api_key: str | None = field(default=None, repr=False)
    openrouter_models: tuple[str, ...] = ()
    openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL
    openrouter_timeout_s: float = 60.0
    openrouter_retries: int = 1
    # Read the key's remaining free-model requests before a review (spec §7 free-tier budget); off for paid keys.
    openrouter_quota_check: bool = True
    review_deadline_s: float = 300.0
    review_summary_max_chars: int = 8000
    # Staging rows of jobs that finished longer ago than this are deleted (spec §8 retention).
    staging_retention_days: int = 30

    def __post_init__(self) -> None:
        _check(self.firms_poll_interval_hours > 0 and math.isfinite(self.firms_poll_interval_hours),
               "FIRMS_POLL_INTERVAL_HOURS", "must be a number > 0", self.firms_poll_interval_hours)
        w, s, e, n = self.firms_bbox
        _check(-180 <= w < e <= 180 and -90 <= s < n <= 90, "FIRMS_BBOX",
               "must be west,south,east,north with west < east and south < north, in range", self.firms_bbox)
        _check(self.firms_max_backfill_days >= 1, "FIRMS_MAX_BACKFILL_DAYS", "must be >= 1",
               self.firms_max_backfill_days)
        _check(self.worker_lease_seconds > 0, "WORKER_LEASE_SECONDS", "must be > 0", self.worker_lease_seconds)
        _check(self.worker_tick_seconds > 0, "WORKER_TICK_SECONDS", "must be > 0", self.worker_tick_seconds)
        # 1-28 so the day exists in every month.
        _check(1 <= self.reconcile_day_of_month <= 28, "RECONCILE_DAY_OF_MONTH", "must be between 1 and 28",
               self.reconcile_day_of_month)
        _check(self.drift_warn_pct > 0 and math.isfinite(self.drift_warn_pct), "DRIFT_WARN_PCT",
               "must be a number > 0", self.drift_warn_pct)
        _check(self.drift_min_count >= 0 and math.isfinite(self.drift_min_count), "DRIFT_MIN_COUNT",
               "must be a number >= 0", self.drift_min_count)
        # A model list without a key would only fail every attempt and publish unreviewed (D3).
        _check(not self.openrouter_models or bool(self.openrouter_api_key), "OPENROUTER_API_KEY",
               "must be set when OPENROUTER_MODELS is not empty", "")
        _check(self.openrouter_base_url.startswith(("https://", "http://")), "OPENROUTER_BASE_URL",
               "must be an http(s) URL", self.openrouter_base_url)
        _check(self.openrouter_timeout_s > 0 and math.isfinite(self.openrouter_timeout_s), "OPENROUTER_TIMEOUT_S",
               "must be a number > 0", self.openrouter_timeout_s)
        _check(self.openrouter_retries >= 0, "OPENROUTER_RETRIES", "must be an integer >= 0", self.openrouter_retries)
        _check(isinstance(self.openrouter_quota_check, bool), "OPENROUTER_QUOTA_CHECK", "must be true or false",
               self.openrouter_quota_check)
        _check(self.review_deadline_s > 0 and math.isfinite(self.review_deadline_s), "REVIEW_DEADLINE_S",
               "must be a number > 0", self.review_deadline_s)
        # Below ~1000 characters a summary cannot even hold the gate results.
        _check(self.staging_retention_days >= 1, "STAGING_RETENTION_DAYS", "must be an integer >= 1",
               self.staging_retention_days)
        _check(self.review_summary_max_chars >= 1000, "REVIEW_SUMMARY_MAX_CHARS", "must be an integer >= 1000",
               self.review_summary_max_chars)

    @property
    def firms_poll_interval(self) -> timedelta:
        return timedelta(hours=self.firms_poll_interval_hours)

    @property
    def firms_bbox_arg(self) -> str:
        """The bbox as the FIRMS area API expects it ("w,s,e,n")."""
        return bbox_arg(self.firms_bbox)

    @classmethod
    def from_env(cls) -> "Settings":
        kw: dict = {"database_url": _get("DATABASE_URL"), "firms_map_key": resolve_map_key()}
        if (v := _get("FIRMS_POLL_INTERVAL_HOURS")) is not None:
            kw["firms_poll_interval_hours"] = _number("FIRMS_POLL_INTERVAL_HOURS", v, float)
        if (v := _get("FIRMS_BBOX")) is not None:
            kw["firms_bbox"] = _parse_bbox(v)
        if (v := _get("FIRMS_MAX_BACKFILL_DAYS")) is not None:
            kw["firms_max_backfill_days"] = _number("FIRMS_MAX_BACKFILL_DAYS", v, int)
        if (v := _get("FIRMS_RAW_DIR")) is not None:
            kw["firms_raw_dir"] = Path(v)
        if (v := _get("WORKER_LEASE_SECONDS")) is not None:
            kw["worker_lease_seconds"] = _number("WORKER_LEASE_SECONDS", v, float)
        if (v := _get("WORKER_TICK_SECONDS")) is not None:
            kw["worker_tick_seconds"] = _number("WORKER_TICK_SECONDS", v, float)
        if (v := _get("RECONCILE_DAY_OF_MONTH")) is not None:
            kw["reconcile_day_of_month"] = _number("RECONCILE_DAY_OF_MONTH", v, int)
        if (v := _get("COUNTRY_BOUNDARY_FILE")) is not None:
            kw["country_boundary_file"] = Path(v)
        if (v := _get("STATIC_MASK_FILE")) is not None:
            kw["static_mask_file"] = Path(v)
        if (v := _get("DRIFT_WARN_PCT")) is not None:
            kw["drift_warn_pct"] = _number("DRIFT_WARN_PCT", v, float)
        if (v := _get("DRIFT_MIN_COUNT")) is not None:
            kw["drift_min_count"] = _number("DRIFT_MIN_COUNT", v, float)
        kw["openrouter_api_key"] = _get("OPENROUTER_API_KEY")
        if (v := _get("OPENROUTER_MODELS")) is not None:
            kw["openrouter_models"] = tuple(m.strip() for m in v.split(",") if m.strip())
        if (v := _get("OPENROUTER_BASE_URL")) is not None:
            kw["openrouter_base_url"] = v
        if (v := _get("OPENROUTER_TIMEOUT_S")) is not None:
            kw["openrouter_timeout_s"] = _number("OPENROUTER_TIMEOUT_S", v, float)
        if (v := _get("OPENROUTER_RETRIES")) is not None:
            kw["openrouter_retries"] = _number("OPENROUTER_RETRIES", v, int)
        if (v := _get("OPENROUTER_QUOTA_CHECK")) is not None:
            kw["openrouter_quota_check"] = _bool("OPENROUTER_QUOTA_CHECK", v)
        if (v := _get("REVIEW_DEADLINE_S")) is not None:
            kw["review_deadline_s"] = _number("REVIEW_DEADLINE_S", v, float)
        if (v := _get("REVIEW_SUMMARY_MAX_CHARS")) is not None:
            kw["review_summary_max_chars"] = _number("REVIEW_SUMMARY_MAX_CHARS", v, int)
        if (v := _get("STAGING_RETENTION_DAYS")) is not None:
            kw["staging_retention_days"] = _number("STAGING_RETENTION_DAYS", v, int)
        return cls(**kw)


def _get(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def _number(name: str, raw: str, kind: type) -> float | int:
    try:
        return kind(raw)
    except ValueError:
        raise SettingsError(f"{name} must be {'an integer' if kind is int else 'a number'}, got {raw!r}") from None


def _bool(name: str, raw: str) -> bool:
    """Strict: only "true" or "false" (any case); anything else is an error, not a guess."""
    value = raw.strip().lower()
    if value not in ("true", "false"):
        raise SettingsError(f"{name} must be true or false, got {raw!r}")
    return value == "true"


def _parse_bbox(raw: str) -> Bbox:
    parts = [p.strip() for p in raw.split(",")]
    try:
        if len(parts) != 4:
            raise ValueError
        w, s, e, n = (float(p) for p in parts)
    except ValueError:
        raise SettingsError(f"FIRMS_BBOX must be four numbers west,south,east,north, got {raw!r}") from None
    return (w, s, e, n)


def _check(ok: bool, name: str, rule: str, value) -> None:
    if not ok:
        raise SettingsError(f"{name} {rule}, got {value!r}")
