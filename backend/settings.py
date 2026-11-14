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
        return cls(**kw)


def _get(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def _number(name: str, raw: str, kind: type) -> float | int:
    try:
        return kind(raw)
    except ValueError:
        raise SettingsError(f"{name} must be {'an integer' if kind is int else 'a number'}, got {raw!r}") from None


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
