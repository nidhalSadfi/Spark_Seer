"""Alembic environment. The URL comes from DATABASE_URL via settings.py (never from alembic.ini).

Tests pass an open connection in `config.attributes["connection"]` instead. Offline mode
(`alembic upgrade head --sql`) renders PostgreSQL DDL and needs no database or URL.
"""

from logging.config import fileConfig

from alembic import context

import db
from settings import Settings

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = None  # migrations are written by hand; there is no autogenerate metadata


def database_url(required: bool) -> str | None:
    url = config.get_main_option("sqlalchemy.url") or Settings.from_env().database_url
    if not url and required:
        raise SystemExit("DATABASE_URL is not set; export it (see .env.example) before running migrations.")
    return db.normalize_url(url) if url else None


def run_migrations_offline() -> None:
    context.configure(url=database_url(required=False), dialect_name="postgresql",
                      target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = db.make_engine(database_url(required=True))
    try:
        with engine.connect() as conn:
            context.configure(connection=conn, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
