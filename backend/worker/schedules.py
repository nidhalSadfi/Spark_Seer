"""Job kinds and pure schedule arithmetic (no database access)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

FIRMS_POLL = "firms_poll"
MONTHLY_RECONCILE = "monthly_reconcile"
RECONCILE_HOUR_UTC = 3  # spec §9: monthly_reconcile runs on RECONCILE_DAY_OF_MONTH at 03:00 UTC


def next_monthly(after: datetime, day: int, hour: int = RECONCILE_HOUR_UTC) -> datetime:
    """First `day`-of-month at `hour`:00 UTC strictly after `after` (day must be 1-28)."""
    after = after.astimezone(timezone.utc)
    candidate = after.replace(day=day, hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= after:
        year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
        candidate = candidate.replace(year=year, month=month)
    return candidate


def next_run(cadence: str, now: datetime, interval_seconds: int | None = None,
             day_of_month: int | None = None) -> datetime:
    """When a schedule is due next after being claimed at `now`.

    Missed runs collapse (spec §9): the next run is computed from the claim time, never from the
    old next_run_at, so a schedule overdue by many intervals runs once and is then `now + interval`.
    """
    if cadence == "interval":
        return now + timedelta(seconds=interval_seconds)
    if cadence == "monthly":
        return next_monthly(now, day_of_month)
    raise ValueError(f"unknown cadence {cadence!r}")


def lock_key(kind: str) -> int:
    """Stable signed 64-bit key for pg_try_advisory_lock (Python's hash() differs between processes)."""
    digest = hashlib.sha256(f"emberguard-job:{kind}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)
