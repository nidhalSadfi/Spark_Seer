"""SQLAlchemy engine helpers shared by the worker and the migrations (no table definitions here).

The schema lives only in the Alembic migrations under migrations/versions/; queries use explicit
SQL because they rely on PostgreSQL features (SKIP LOCKED, advisory locks, partial indexes).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

DRIVER_PREFIX = "postgresql+psycopg://"


def normalize_url(url: str) -> str:
    """Use the psycopg 3 driver for plain postgres:// or postgresql:// URLs (Render-style)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return DRIVER_PREFIX + url[len(prefix):]
    return url


def safe_url(url: str) -> str:
    """The URL with its password masked, for messages and logs."""
    try:
        return make_url(normalize_url(url)).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable database URL>"


def make_engine(url: str, connect_timeout: int = 10, **kwargs) -> Engine:
    connect_args = {"connect_timeout": connect_timeout, **kwargs.pop("connect_args", {})}
    return create_engine(normalize_url(url), pool_pre_ping=True, connect_args=connect_args, **kwargs)
