"""PostgreSQL-driven scheduler (spec §9). All schedule and job state lives in the database (I5).

One active job per kind (I4) is enforced three ways:
- claiming a due schedule row with SELECT ... FOR UPDATE SKIP LOCKED, so concurrent workers never
  both see it as due;
- the partial unique index processing_jobs_one_active_per_kind (queued/running);
- a session-level pg_try_advisory_lock(kind) held on the job's dedicated connection while it runs.

A running job holds a lease that a heartbeat thread renews every WORKER_LEASE_SECONDS / 3. If the
process dies, the lease expires and the next tick of any worker marks the job `abandoned` and makes
its kind due again.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from settings import Settings
from worker.jobs import HANDLER_RESULTS, Clock, JobContext, JobError, checked_now, describe, utcnow
from worker.schedules import FIRMS_POLL, MONTHLY_RECONCILE, lock_key, next_monthly, next_run

log = logging.getLogger("worker.scheduler")

Handler = Callable[[JobContext], str]
ACTIVE_JOB_INDEX = "processing_jobs_one_active_per_kind"


def default_client_factory(map_key: str) -> Any:
    from firms_tool.area_api import Client  # the one FIRMS client (spec §4)

    firms_log = logging.getLogger("worker.firms")
    return Client(map_key, log=lambda msg: firms_log.info(msg.strip()))


@dataclass
class Claim:
    conn: Connection  # dedicated connection holding the advisory lock for the job's duration
    kind: str
    job_id: int
    lock_key: int


class Scheduler:
    def __init__(self, engine: Engine, settings: Settings, worker_id: str, handlers: dict[str, Handler],
                 clock: Clock = utcnow, client_factory: Callable[[str], Any] = default_client_factory,
                 llm_factory: Callable[..., Any] | None = None, quota_check: Callable[[Any], Any] | None = None):
        self.engine = engine
        self.llm_factory = llm_factory  # None: worker.review uses the real OpenRouter client
        self.quota_check = quota_check
        self.settings = settings
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.clock = clock
        self.client_factory = client_factory

    def _now(self):
        return checked_now(self.clock)

    def _extra(self, **kw) -> dict:
        return {"worker_id": self.worker_id, **kw}

    # -- startup -------------------------------------------------------------------------------

    def startup(self) -> None:
        self.abandon_expired()
        self.ensure_schedules()

    def ensure_schedules(self) -> None:
        """Write the firms_poll interval from settings; create monthly_reconcile (disabled) if missing."""
        now = self._now()
        interval = max(1, round(self.settings.firms_poll_interval.total_seconds()))
        day = self.settings.reconcile_day_of_month
        with self.engine.begin() as c:
            # A changed interval re-bases the next run on the last claim, so a shorter interval takes
            # effect at once (a run already overdue under it happens on the next tick).
            c.execute(text("""
                INSERT INTO schedules (kind, cadence, interval_seconds, next_run_at, enabled, updated_at)
                VALUES (:kind, 'interval', :interval, :now, true, :now)
                ON CONFLICT (kind) DO UPDATE SET
                    cadence = 'interval',
                    interval_seconds = EXCLUDED.interval_seconds,
                    day_of_month = NULL,
                    next_run_at = CASE
                        WHEN schedules.interval_seconds IS DISTINCT FROM EXCLUDED.interval_seconds
                        THEN COALESCE(schedules.last_claimed_at + EXCLUDED.interval_seconds * INTERVAL '1 second',
                                      EXCLUDED.next_run_at)
                        ELSE schedules.next_run_at END,
                    updated_at = EXCLUDED.updated_at"""),
                {"kind": FIRMS_POLL, "interval": interval, "now": now})
            # Spec §9: the row exists from Phase 1 but stays disabled until its handler lands.
            # `enabled` is never touched on conflict, so an operator's choice survives restarts.
            c.execute(text("""
                INSERT INTO schedules (kind, cadence, day_of_month, next_run_at, enabled, updated_at)
                VALUES (:kind, 'monthly', :day, :next, false, :now)
                ON CONFLICT (kind) DO UPDATE SET
                    cadence = 'monthly',
                    interval_seconds = NULL,
                    day_of_month = EXCLUDED.day_of_month,
                    next_run_at = CASE
                        WHEN schedules.day_of_month IS DISTINCT FROM EXCLUDED.day_of_month
                        THEN EXCLUDED.next_run_at ELSE schedules.next_run_at END,
                    updated_at = EXCLUDED.updated_at"""),
                {"kind": MONTHLY_RECONCILE, "day": day, "next": next_monthly(now, day), "now": now})
        log.info("schedules written: %s every %ss, %s on day %s (row created disabled)", FIRMS_POLL,
                 interval, MONTHLY_RECONCILE, day, extra=self._extra())

    # -- tick ----------------------------------------------------------------------------------

    def tick(self) -> list[int]:
        """Abandon expired leases, then claim and run each due kind that has a handler. Returns job ids run."""
        self.abandon_expired()
        self._log_unhandled_due()
        ran = []
        for kind in self.handlers:
            claim = self.claim(kind)
            if claim is not None:
                self.run(claim)
                ran.append(claim.job_id)
        return ran

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.error("tick failed; retrying on the next tick", exc_info=True, extra=self._extra())
            stop.wait(self.settings.worker_tick_seconds)

    def abandon_expired(self) -> list[int]:
        now = self._now()
        with self.engine.begin() as c:
            rows = c.execute(text("""
                UPDATE processing_jobs
                SET status = 'abandoned', finished_at = :now,
                    error = COALESCE(error, 'lease expired: worker ' || COALESCE(lease_owner, '?')
                                            || ' stopped renewing it (killed or hung)')
                WHERE status = 'running' AND lease_expires_at < :now
                RETURNING id, kind, lease_owner"""), {"now": now}).all()
            kinds = sorted({r.kind for r in rows})
            if kinds:  # spec §9: the abandoned job's kind becomes due again
                c.execute(text("UPDATE schedules SET next_run_at = LEAST(next_run_at, :now), updated_at = :now "
                               "WHERE kind = ANY(CAST(:kinds AS text[]))"), {"now": now, "kinds": kinds})
        for r in rows:
            log.warning("job %s (%s) of worker %s abandoned: lease expired", r.id, r.kind, r.lease_owner,
                        extra=self._extra(job_id=r.id, kind=r.kind))
        return [r.id for r in rows]

    def _log_unhandled_due(self) -> None:
        with self.engine.connect() as c:
            rows = c.execute(text(
                "SELECT kind FROM schedules WHERE enabled AND next_run_at <= :now "
                "AND NOT (kind = ANY(CAST(:handled AS text[])))"),
                {"now": self._now(), "handled": list(self.handlers)}).all()
        for r in rows:
            log.warning("schedule %s is due but has no handler: skipped, no job created", r.kind,
                        extra=self._extra(kind=r.kind))

    # -- claim / run ---------------------------------------------------------------------------

    def claim(self, kind: str) -> Claim | None:
        """Claim `kind` if it is due: insert a running job, advance next_run_at, keep the advisory lock."""
        now = self._now()
        key = lock_key(kind)
        conn = self.engine.connect()
        locked = False
        try:
            with conn.begin():
                row = conn.execute(text("""
                    SELECT kind, cadence, interval_seconds, day_of_month FROM schedules
                    WHERE kind = :kind AND enabled AND next_run_at <= :now
                    FOR UPDATE SKIP LOCKED"""), {"kind": kind, "now": now}).first()
                if row is None:
                    return None  # not due, disabled, or another worker is claiming it right now
                locked = bool(conn.scalar(text("SELECT pg_try_advisory_lock(CAST(:key AS bigint))"), {"key": key}))
                if not locked:
                    log.info("%s is due but already running (advisory lock held elsewhere)", kind,
                             extra=self._extra(kind=kind))
                    return None
                try:
                    with conn.begin_nested():
                        job_id = conn.scalar(text("""
                            INSERT INTO processing_jobs (kind, status, trigger, lease_owner, lease_expires_at,
                                                         heartbeat_at, created_at, started_at)
                            VALUES (:kind, 'running', 'schedule', :owner, :lease, :now, :now, :now)
                            RETURNING id"""),
                            {"kind": kind, "owner": self.worker_id, "now": now,
                             "lease": now + timedelta(seconds=self.settings.worker_lease_seconds)})
                except IntegrityError as e:
                    if getattr(getattr(e.orig, "diag", None), "constraint_name", None) != ACTIVE_JOB_INDEX:
                        raise
                    log.info("%s is due but already has an active job; skipped", kind, extra=self._extra(kind=kind))
                    return None
                # Set at claim time: missed runs collapse into this one run (no backlog).
                conn.execute(text(
                    "UPDATE schedules SET next_run_at = :next, last_claimed_at = :now, last_job_id = :job_id, "
                    "updated_at = :now WHERE kind = :kind"),
                    {"kind": kind, "now": now, "job_id": job_id,
                     "next": next_run(row.cadence, now, row.interval_seconds, row.day_of_month)})
            claim, conn = Claim(conn, kind, job_id, key), None  # the claim now owns the connection
            log.info("claimed %s as job %s", kind, job_id, extra=self._extra(job_id=job_id, kind=kind))
            return claim
        finally:
            if conn is not None:
                if locked:
                    self._unlock(conn, key)
                conn.close()

    def run(self, claim: Claim) -> str:
        """Run a claimed job to a final status, heartbeating meanwhile; always release the lock."""
        ctx = JobContext(engine=self.engine, job_id=claim.job_id, kind=claim.kind, settings=self.settings,
                         clock=self.clock, client_factory=self.client_factory, worker_id=self.worker_id,
                         llm_factory=self.llm_factory, quota_check=self.quota_check)
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(claim.job_id, stop),
                                name=f"heartbeat-{claim.job_id}", daemon=True)
        beat.start()
        status, error = "failed", None
        try:
            result = self.handlers[claim.kind](ctx)
            if result not in HANDLER_RESULTS:
                raise RuntimeError(f"handler returned {result!r}, expected one of {HANDLER_RESULTS}")
            status = result
        except JobError as e:
            error = ctx.redact(describe(e))
            log.error("job %s (%s) failed: %s", claim.job_id, claim.kind, error,
                      extra=self._extra(job_id=claim.job_id, kind=claim.kind))
        except Exception as e:
            error = ctx.redact(describe(e))
            log.error("job %s (%s) failed: %s", claim.job_id, claim.kind, error, exc_info=True,
                      extra=self._extra(job_id=claim.job_id, kind=claim.kind))
        except BaseException as e:  # KeyboardInterrupt/SystemExit: record it, then let it propagate
            error = f"worker interrupted ({type(e).__name__})"
            raise
        finally:
            stop.set()
            beat.join(timeout=max(1.0, self.settings.worker_lease_seconds))
            self._finish(claim, status, error, ctx.counts)
            self._release(claim)
        return status

    def _heartbeat(self, job_id: int, stop: threading.Event) -> None:
        every = self.settings.worker_lease_seconds / 3
        lease = timedelta(seconds=self.settings.worker_lease_seconds)
        while not stop.wait(every):
            try:
                now = self._now()
                with self.engine.begin() as c:
                    renewed = c.execute(text(
                        "UPDATE processing_jobs SET heartbeat_at = :now, lease_expires_at = :lease "
                        "WHERE id = :id AND status = 'running' AND lease_owner = :owner"),
                        {"id": job_id, "now": now, "lease": now + lease, "owner": self.worker_id}).rowcount
            except Exception as e:
                log.warning("heartbeat for job %s failed (%s); retrying", job_id, type(e).__name__,
                            extra=self._extra(job_id=job_id))
                continue
            if not renewed:
                log.warning("job %s is no longer running under this worker; heartbeat stopped", job_id,
                            extra=self._extra(job_id=job_id))
                return

    def _finish(self, claim: Claim, status: str, error: str | None, counts: dict) -> None:
        # A promotion marks its job `succeeded` inside its own transaction (spec §8), so a job may
        # already be succeeded here; it still gets its counts and finished_at.
        try:
            with self.engine.begin() as c:
                done = c.execute(text("""
                    UPDATE processing_jobs
                    SET status = :status, finished_at = :now, heartbeat_at = :now, error = :error,
                        fetched_count = :fetched, new_count = :new, modified_count = :modified,
                        unchanged_count = :unchanged, pending_count = :pending
                    WHERE id = :id AND lease_owner = :owner
                      AND (status = 'running' OR (status = 'succeeded' AND CAST(:status AS text) = 'succeeded'))"""),
                    {"id": claim.job_id, "owner": self.worker_id, "status": status, "error": error,
                     "now": self._now(), **{k: counts.get(k) for k in
                                            ("fetched", "new", "modified", "unchanged", "pending")}}).rowcount
        except Exception:
            log.error("could not record the final status of job %s; its lease will expire and it will be "
                      "marked abandoned", claim.job_id, exc_info=True,
                      extra=self._extra(job_id=claim.job_id, kind=claim.kind))
            return
        if done:
            log.info("job %s (%s) finished: %s", claim.job_id, claim.kind, status,
                     extra=self._extra(job_id=claim.job_id, kind=claim.kind, status=status, counts=counts))
        else:
            log.warning("job %s (%s) was no longer running when it finished (abandoned by another worker?); "
                        "final status %s not recorded", claim.job_id, claim.kind, status,
                        extra=self._extra(job_id=claim.job_id, kind=claim.kind))

    def _release(self, claim: Claim) -> None:
        try:
            self._unlock(claim.conn, claim.lock_key)
        finally:
            claim.conn.close()

    def _unlock(self, conn: Connection, key: int) -> None:
        try:
            if conn.in_transaction():
                conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"), {"key": key})
            conn.commit()
        except Exception:
            # Closing the physical connection ends the session, which releases the lock.
            log.warning("could not release advisory lock %s; discarding the connection", key,
                        extra=self._extra())
            conn.invalidate()
