"""What a job handler gets: its job id, settings, clock, FIRMS client factory and step recording."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from sqlalchemy import text
from sqlalchemy.engine import Engine

from settings import Settings

Clock = Callable[[], datetime]
# Statuses a handler may return; an exception means `failed`.
HANDLER_RESULTS = ("succeeded", "no_change", "held", "rejected")


class JobError(RuntimeError):
    """An expected job failure. Its message is stored on the job as is, so make it clear."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def checked_now(clock: Clock) -> datetime:
    now = clock()
    if now.tzinfo is None:
        raise ValueError("the worker clock must return timezone-aware datetimes")
    return now.astimezone(timezone.utc)


def describe(exc: BaseException) -> str:
    return str(exc) if isinstance(exc, JobError) else f"{type(exc).__name__}: {exc}"


def to_json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


@dataclass
class Step:
    name: str
    summary: dict = field(default_factory=dict)
    status: str = "succeeded"  # a handler may set "skipped"


@dataclass
class JobContext:
    engine: Engine
    job_id: int
    kind: str
    settings: Settings
    clock: Clock
    client_factory: Callable[[str], Any]
    worker_id: str
    # Written to processing_jobs when the job ends, whatever its status (fetched/new/modified/...).
    counts: dict = field(default_factory=dict)
    # Applied to every error text stored in the database; firms_poll sets it to Client.redact.
    redact: Callable[[str], str] = field(default=lambda s: s)

    def now(self) -> datetime:
        return checked_now(self.clock)

    @contextmanager
    def step(self, name: str) -> Iterator[Step]:
        """Record a job_steps row: `running` on entry, then the step's status (or `failed`) on exit."""
        step = Step(name)
        with self.engine.begin() as c:
            step_id = c.scalar(text(
                "INSERT INTO job_steps (job_id, name, status, started_at) "
                "VALUES (:job_id, :name, 'running', :now) RETURNING id"),
                {"job_id": self.job_id, "name": name, "now": self.now()})
        try:
            yield step
        except BaseException as e:
            step.summary["error"] = self.redact(describe(e))
            self._finish_step(step_id, "failed", step.summary)
            raise
        self._finish_step(step_id, step.status, step.summary)

    def _finish_step(self, step_id: int, status: str, summary: dict) -> None:
        with self.engine.begin() as c:
            c.execute(text(
                "UPDATE job_steps SET status = :status, finished_at = :now, summary = CAST(:summary AS jsonb) "
                "WHERE id = :id"),
                {"id": step_id, "status": status, "now": self.now(), "summary": self.redact(to_json(summary))})
