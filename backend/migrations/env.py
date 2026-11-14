"""Alembic environment for the local SQLite operational database."""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from personal_job_finder_api.core.config import settings  # noqa: E402
from personal_job_finder_api.db.paths import ensure_private_database_file  # noqa: E402
from personal_job_finder_api.db.tables import metadata  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = metadata


def _prepare_sqlite_path() -> None:
    database_url = config.get_main_option("sqlalchemy.url")
    if not database_url:
        return
    parsed = make_url(database_url)
    if parsed.drivername.startswith("sqlite") and parsed.database and parsed.database != ":memory:":
        ensure_private_database_file(Path(parsed.database))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _prepare_sqlite_path()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
