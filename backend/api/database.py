"""Database access for the API process (spec §10). Lazy: nothing connects or imports SQLAlchemy at
import time, and with DATABASE_URL unset SQLAlchemy is never imported at all, so the parquet-only
deployment (Render demo) runs exactly as before.

- `read()`: a READ ONLY transaction. Every GET goes through it, so a GET cannot write (I1).
- `write()`: a read-write transaction, used only by the community-report endpoints, which write
  only to `community_reports` (I1 amendment 2026-11-15).
- Both raise `DatabaseUnavailable` (answered as 503 by api/main.py) when DATABASE_URL is unset,
  the server is unreachable, or the schema lacks a table the endpoint needs.

The API never imports `worker`, `crewai` or the LLM client (I1); queries are explicit SQL.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from typing import Any, Iterator

from settings import ApiSettings

_lock = threading.Lock()
_engine = None
_engine_url: str | None = None


class DatabaseUnavailable(RuntimeError):
    """No usable database for this request; the message is the 503 `detail`."""


def settings() -> ApiSettings:
    """Read at call time, so tests (and a restart with new variables) see the current environment."""
    return ApiSettings.from_env()


def configured() -> bool:
    return settings().database_url is not None


def get_engine():
    """The process-wide engine for DATABASE_URL (created on first use, replaced if the URL changes)."""
    global _engine, _engine_url
    s = settings()
    if not s.database_url:
        raise DatabaseUnavailable("database not configured: DATABASE_URL is not set, so this endpoint is "
                                  "unavailable (the calendar endpoints keep serving the parquet file)")
    with _lock:
        if _engine is None or _engine_url != s.database_url:
            from db import make_engine  # imports SQLAlchemy; only reached with DATABASE_URL set

            if _engine is not None:
                _engine.dispose()
            _engine = make_engine(s.database_url, connect_timeout=s.api_db_connect_timeout_s)
            _engine_url = s.database_url
        return _engine


def reset() -> None:
    """Dispose the engine (tests; a changed DATABASE_URL is picked up without this)."""
    global _engine, _engine_url
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _engine_url = None, None


def _translate(exc: Exception) -> DatabaseUnavailable | None:
    """A SQLAlchemy error as the 503 it stands for, or None for a genuine bug (left as a 500)."""
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    from db import safe_url

    orig = getattr(exc, "orig", None)
    reason = (str(orig or exc).strip().splitlines() or [type(exc).__name__])[0]
    if isinstance(exc, OperationalError) or (isinstance(exc, DBAPIError) and exc.connection_invalidated):
        url = settings().database_url
        return DatabaseUnavailable(f"database unavailable at {safe_url(url) if url else '?'}: {reason}")
    if isinstance(exc, ProgrammingError) and type(orig).__name__ in ("UndefinedTable", "UndefinedColumn"):
        return DatabaseUnavailable(f"database schema is not up to date ({reason}); run `alembic upgrade head` "
                                   "from backend/")
    return None


@contextmanager
def _transaction(read_only: bool, isolation_level: str | None = None) -> Iterator[Any]:
    from sqlalchemy.exc import SQLAlchemyError

    engine = get_engine()
    try:
        conn = engine.connect()
    except SQLAlchemyError as e:
        raise (_translate(e) or e) from None
    try:
        if isolation_level:
            conn = conn.execution_options(isolation_level=isolation_level)
        with conn.begin():
            if read_only:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            yield conn
    except SQLAlchemyError as e:
        translated = _translate(e)
        if translated is None:
            raise
        raise translated from None
    finally:
        conn.close()


def read(isolation_level: str | None = None):
    """A READ ONLY transaction (GET endpoints). REPEATABLE READ gives one consistent snapshot."""
    return _transaction(True, isolation_level)


def write():
    """A read-write transaction; only the community-report endpoints use it."""
    return _transaction(False)


def rows(conn, sql: str, **params) -> list[dict]:
    """Rows of `sql` (named :params) as dicts."""
    from sqlalchemy import text

    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def one(conn, sql: str, **params) -> dict | None:
    found = rows(conn, sql, **params)
    return found[0] if found else None


def scalar(conn, sql: str, **params):
    from sqlalchemy import text

    return conn.execute(text(sql), params).scalar()


def iso(value: datetime | date | None) -> str | None:
    """ISO 8601 in UTC with a `Z` (milliseconds only when present); dates as YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)  # timestamps without a zone are UTC here
        value = value.astimezone(timezone.utc)
        spec = "milliseconds" if value.microsecond else "seconds"
        return value.replace(tzinfo=None).isoformat(timespec=spec) + "Z"
    return value.isoformat()


def utc_datetime(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=timezone.utc)
