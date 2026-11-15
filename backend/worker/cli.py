"""`python -m worker [--once] [--worker-id ID]`, run from backend/.

Exit codes: 0 ok, 1 database unreachable or not migrated, 2 configuration error.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from db import make_engine, safe_url
from firms_tool.area_api import Client
from settings import Settings, SettingsError
from worker.handlers import HANDLERS
from worker.log import setup_logging
from worker.scheduler import Scheduler

log = logging.getLogger("worker")

PHASE1_TABLES = ("schedules", "processing_jobs", "job_steps", "firms_fetches", "firms_availability",
                 "raw_detections")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m worker", description="EmberGuard processing worker.")
    p.add_argument("--once", action="store_true", help="run a single scheduler tick, then exit")
    p.add_argument("--worker-id", default=None, help="lease owner name (default: hostname:pid)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def _reason(exc: BaseException) -> str:
    """First line of the driver's message (psycopg errors run over several lines)."""
    msg = str(getattr(exc, "orig", None) or exc).strip()
    return msg.splitlines()[0] if msg else type(exc).__name__


def check_database(engine: Engine) -> str | None:
    """None if the Phase 1 tables exist; otherwise what to do. Raises OperationalError if unreachable."""
    with engine.connect() as c:
        missing = [t for t in PHASE1_TABLES if c.scalar(text("SELECT to_regclass(CAST(:t AS text))"), {"t": t}) is None]
    if missing:
        return (f"database schema is missing ({', '.join(missing)}); "
                "run `alembic upgrade head` from backend/ first")
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"
    try:
        settings = Settings.from_env()
    except SettingsError as e:
        setup_logging(args.log_level, static={"worker_id": worker_id})
        log.error("invalid configuration: %s", e)
        return 2
    redact = Client(settings.firms_map_key, log=lambda m: None).redact if settings.firms_map_key else None
    setup_logging(args.log_level, redact=redact, static={"worker_id": worker_id})
    if not settings.database_url:
        log.error("DATABASE_URL is not set; export it (see .env.example), start PostgreSQL "
                  "(docker compose up -d db) and run `alembic upgrade head` from backend/")
        return 2
    if not settings.firms_map_key:
        log.warning("no FIRMS MAP_KEY set (FIRMS_MAP_KEY or FIRMS_MAP_KEY_1): firms_poll jobs will fail")

    engine = make_engine(settings.database_url)
    try:
        try:
            problem = check_database(engine)
        except OperationalError as e:
            log.error("cannot connect to the database at %s: %s", safe_url(settings.database_url), _reason(e))
            return 1
        if problem:
            log.error(problem)
            return 1

        scheduler = Scheduler(engine, settings, worker_id, HANDLERS)
        stop = threading.Event()
        try:
            scheduler.startup()
            if args.once:
                scheduler.tick()
                return 0
            # SIGTERM (docker stop): finish the current job, then exit before the next tick.
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            log.info("worker started: tick every %ss, lease %ss", settings.worker_tick_seconds,
                     settings.worker_lease_seconds)
            scheduler.run_forever(stop)
            log.info("worker stopped")
            return 0
        except SQLAlchemyError as e:
            log.error("database error: %s", _reason(e))
            return 1
        except KeyboardInterrupt:
            log.info("interrupted")
            return 130
    finally:
        engine.dispose()
