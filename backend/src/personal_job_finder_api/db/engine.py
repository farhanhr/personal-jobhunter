"""Database engine and transaction helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.db.paths import ensure_private_database_file


def create_engine_for_url(
    database_url: str,
    *,
    busy_timeout_ms: int = 5_000,
) -> Engine:
    """Create a SQLite engine with foreign keys and a bounded busy timeout."""

    engine = sa.create_engine(
        database_url,
        connect_args={
            "timeout": busy_timeout_ms / 1_000,
            "check_same_thread": False,
        },
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        finally:
            cursor.close()

    return engine


def create_database_engine(settings: Settings) -> Engine:
    """Create the operational database engine and its private parent directory."""

    ensure_private_database_file(settings.database_path)
    return create_engine_for_url(
        settings.database_url,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )


@contextmanager
def immediate_transaction(engine: Engine) -> Iterator[Connection]:
    """Open an explicit SQLite ``BEGIN IMMEDIATE`` transaction.

    The connection is in AUTOCOMMIT mode so SQLAlchemy does not issue its own
    deferred ``BEGIN`` first. Competing writers wait up to the configured busy
    timeout and then see the committed state.
    """

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
