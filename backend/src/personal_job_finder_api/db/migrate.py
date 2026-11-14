"""Alembic programmatic migration helpers."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def _backend_dir() -> Path:
    # .../personal_job_finder_api/db/migrate.py -> backend/
    return Path(__file__).resolve().parents[3]


def alembic_config(database_url: str) -> Config:
    backend_dir = _backend_dir()
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def run_migrations(database_url: str) -> None:
    """Apply all pending migrations to the given database URL."""

    command.upgrade(alembic_config(database_url), "head")


def downgrade_database(database_url: str, revision: str = "base") -> None:
    """Roll back to a revision; used by migration tests and local recovery."""

    command.downgrade(alembic_config(database_url), revision)
