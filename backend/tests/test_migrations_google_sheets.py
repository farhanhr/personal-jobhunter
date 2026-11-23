"""Downgrade/re-upgrade round-trips for the Google Sheets settings migrations."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.migrate import downgrade_database, run_migrations
from personal_job_finder_api.db.tables import google_sheets_settings

HEAD_REVISION = "0006_google_sheets_workspace_state"
SETTINGS_REVISION = "0005_google_sheets_settings"


def _head(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar())


def _columns(engine: Engine, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


def _save_state(engine: Engine, *, ready_at: str | None, via: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            google_sheets_settings.insert().values(
                settings_id=1,
                spreadsheet_id="1SyntheticSheetId0123456789abcdefXYZ",
                updated_at_utc="2026-11-23T00:00:00Z",
                workspace_ready_at_utc=ready_at,
                configured_via=via,
            )
        )


def test_upgrade_to_head_and_downgrade_across_both_revisions(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrate-roundtrip.sqlite3'}"
    run_migrations(database_url)

    engine = create_engine_for_url(database_url)
    try:
        assert _head(engine) == HEAD_REVISION
        assert "google_sheets_settings" in sa.inspect(engine).get_table_names()
        assert {"workspace_ready_at_utc", "configured_via"} <= _columns(
            engine, "google_sheets_settings"
        )

        _save_state(engine, ready_at="2026-11-23T00:00:00Z", via="cli")

        downgrade_database(database_url, SETTINGS_REVISION)
        assert _head(engine) == SETTINGS_REVISION
        assert {"workspace_ready_at_utc", "configured_via"}.isdisjoint(
            _columns(engine, "google_sheets_settings")
        )
        with engine.connect() as connection:
            row = connection.execute(
                sa.text("SELECT spreadsheet_id FROM google_sheets_settings WHERE settings_id = 1")
            ).first()
        assert row is not None, "the base settings row must survive the 0006 downgrade"

        downgrade_database(database_url, "base")
        assert "google_sheets_settings" not in sa.inspect(engine).get_table_names()

        run_migrations(database_url)
        assert _head(engine) == HEAD_REVISION
        assert {"workspace_ready_at_utc", "configured_via"} <= _columns(
            engine, "google_sheets_settings"
        )
        with engine.connect() as connection:
            remaining = connection.execute(
                sa.text("SELECT COUNT(*) FROM google_sheets_settings")
            ).scalar()
        assert remaining == 0, "a re-upgrade must start from a clean singleton table"
    finally:
        engine.dispose()


def test_downgrade_of_only_0006_keeps_0005_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrate-0006-only.sqlite3'}"
    run_migrations(database_url)
    downgrade_database(database_url, SETTINGS_REVISION)

    engine = create_engine_for_url(database_url)
    try:
        assert _head(engine) == SETTINGS_REVISION
        columns = _columns(engine, "google_sheets_settings")
        assert "spreadsheet_id" in columns and "updated_at_utc" in columns
        assert {"workspace_ready_at_utc", "configured_via"}.isdisjoint(columns)
        run_migrations(database_url)
        assert _head(engine) == HEAD_REVISION
    finally:
        engine.dispose()
