"""Alembic schema and SQLite pragma tests."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.migrate import alembic_config, downgrade_database, run_migrations
from personal_job_finder_api.db.tables import companies, metadata, search_runs


def _run_values(*, run_id: str, key: str, status: str = "accepted") -> dict[str, object]:
    accepted_at = format_utc(datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
    return {
        "run_id": run_id,
        "idempotency_key": key,
        "request_fingerprint": "a" * 64,
        "operational_day": "2026-11-12",
        "status": status,
        "accepted_at_utc": accepted_at,
        "config_snapshot_json": "{}",
    }


def test_upgrade_creates_required_tables_and_columns(engine: Engine) -> None:
    inspector = sa.inspect(engine)
    assert {"search_runs", "target_roles", "companies", "alembic_version"} <= set(
        inspector.get_table_names()
    )

    search_run_columns = {column["name"] for column in inspector.get_columns("search_runs")}
    assert {
        "run_id",
        "idempotency_key",
        "request_fingerprint",
        "operational_day",
        "status",
        "accepted_at_utc",
        "started_at_utc",
        "finished_at_utc",
        "config_snapshot_json",
        "error_summary",
        "progress_json",
        "source_summary_json",
    } <= search_run_columns

    company_columns = {column["name"] for column in inspector.get_columns("companies")}
    assert {
        "company_id",
        "company_name",
        "company_website",
        "career_url",
        "ats_type",
        "ats_board_token",
        "enabled",
        "policy_status",
        "access_notes",
        "created_at_utc",
        "updated_at_utc",
    } <= company_columns


def test_metadata_and_migrated_columns_match(engine: Engine) -> None:
    inspector = sa.inspect(engine)
    for table_name, table in metadata.tables.items():
        migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert migrated_columns == set(table.c.keys()), table_name


def test_company_checks_and_indexes_survive_0002(engine: Engine) -> None:
    inspector = sa.inspect(engine)

    check_names = {
        constraint["name"] for constraint in inspector.get_check_constraints("companies")
    }
    assert {
        "ck_companies_ats_type",
        "ck_companies_enabled",
        "ck_companies_policy_status",
    } <= check_names

    index_names = {index["name"] for index in inspector.get_indexes("companies")}
    assert {"ix_companies_enabled", "ix_companies_ats_type"} <= index_names


def test_sqlite_pragmas_are_applied_to_engine_connections(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5_000


def test_only_one_active_run_can_be_persisted(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                _run_values(run_id="run_1", key="11111111-1111-4111-8111-111111111111")
            )
        )

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                _run_values(
                    run_id="run_2",
                    key="22222222-2222-4222-8222-222222222222",
                    status="running",
                )
            )
        )

    with engine.begin() as connection:
        connection.execute(
            search_runs.update().where(search_runs.c.run_id == "run_1").values(status="completed")
        )
        connection.execute(
            search_runs.insert().values(
                _run_values(
                    run_id="run_2",
                    key="22222222-2222-4222-8222-222222222222",
                    status="running",
                )
            )
        )


def test_invalid_status_is_rejected_by_database(engine: Engine) -> None:
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                _run_values(
                    run_id="run_bad",
                    key="33333333-3333-4333-8333-333333333333",
                    status="not_a_status",
                )
            )
        )


def test_duplicate_idempotency_key_is_rejected_by_database(engine: Engine) -> None:
    same_key = "44444444-4444-4444-8444-444444444444"
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(_run_values(run_id="run_first", key=same_key))
        )

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                _run_values(run_id="run_second", key=same_key, status="completed")
            )
        )


def test_invalid_company_policy_status_is_rejected_by_database(engine: Engine) -> None:
    timestamp = format_utc(datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            companies.insert().values(
                company_id="company_bad",
                company_name="Example Company",
                ats_type="none",
                enabled=True,
                policy_status="not_a_policy_status",
                created_at_utc=timestamp,
                updated_at_utc=timestamp,
            )
        )


@pytest.mark.parametrize(
    ("ats_type", "enabled"),
    [
        ("not_an_ats_type", 1),
        ("none", 2),
    ],
)
def test_company_legacy_constraints_are_still_enforced(
    engine: Engine,
    ats_type: str,
    enabled: int,
) -> None:
    timestamp = format_utc(datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        # Raw SQL bypasses SQLAlchemy Boolean coercion so SQLite enforces the CHECK.
        connection.exec_driver_sql(
            "INSERT INTO companies "
            "(company_id, company_name, ats_type, enabled, policy_status, "
            "created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "company_invalid",
                "Example Company",
                ats_type,
                enabled,
                "permitted",
                timestamp,
                timestamp,
            ),
        )


def test_new_company_defaults_to_unreviewed(engine: Engine) -> None:
    timestamp = format_utc(datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
    with engine.begin() as connection:
        connection.execute(
            companies.insert().values(
                company_id="company_new",
                company_name="Example Company",
                ats_type="none",
                enabled=True,
                created_at_utc=timestamp,
                updated_at_utc=timestamp,
            )
        )
        status = connection.execute(
            sa.select(companies.c.policy_status).where(companies.c.company_id == "company_new")
        ).scalar_one()

    assert status == "unreviewed"


def test_migration_downgrade_and_upgrade_round_trip(
    engine: Engine,
    database_url: str,
) -> None:
    engine.dispose()
    downgrade_database(database_url)

    downgraded_engine = create_engine_for_url(database_url)
    try:
        assert "search_runs" not in sa.inspect(downgraded_engine).get_table_names()
        assert "target_roles" not in sa.inspect(downgraded_engine).get_table_names()
        assert "companies" not in sa.inspect(downgraded_engine).get_table_names()
    finally:
        downgraded_engine.dispose()

    run_migrations(database_url)
    upgraded_engine = create_engine_for_url(database_url)
    try:
        assert "search_runs" in sa.inspect(upgraded_engine).get_table_names()
    finally:
        upgraded_engine.dispose()


def test_upgrade_to_head_is_idempotent(database_url: str) -> None:
    run_migrations(database_url)
    run_migrations(database_url)

    verification_engine = create_engine_for_url(database_url)
    try:
        assert "search_runs" in sa.inspect(verification_engine).get_table_names()
    finally:
        verification_engine.dispose()


def test_migration_creates_missing_nested_data_directory(tmp_path) -> None:
    data_dir = tmp_path / "missing" / "private-data"
    database_path = data_dir / "personal-job-finder.sqlite3"

    run_migrations(f"sqlite:///{database_path}")

    assert database_path.exists()
    if os.name == "posix":
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_0002_downgrade_restores_0001_company_schema(database_url: str) -> None:
    run_migrations(database_url)

    command.downgrade(alembic_config(database_url), "0001_initial_schema")
    downgraded_engine = create_engine_for_url(database_url)
    try:
        inspector = sa.inspect(downgraded_engine)
        columns = {column["name"] for column in inspector.get_columns("companies")}
        assert "policy_status" not in columns
        assert {
            "ck_companies_ats_type",
            "ck_companies_enabled",
        } <= {constraint["name"] for constraint in inspector.get_check_constraints("companies")}
        assert {"ix_companies_enabled", "ix_companies_ats_type"} <= {
            index["name"] for index in inspector.get_indexes("companies")
        }
    finally:
        downgraded_engine.dispose()

    run_migrations(database_url)
    upgraded_engine = create_engine_for_url(database_url)
    try:
        columns = {
            column["name"] for column in sa.inspect(upgraded_engine).get_columns("companies")
        }
    finally:
        upgraded_engine.dispose()
    assert "policy_status" in columns


def test_0002_preserves_existing_company_rows(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    database_url = f"sqlite:///{database_path}"
    command.upgrade(alembic_config(database_url), "0001_initial_schema")

    timestamp = format_utc(datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO companies "
            "(company_id, company_name, ats_type, enabled, created_at_utc, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("company_legacy", "Legacy Company", "none", 1, timestamp, timestamp),
        )
        connection.commit()
    finally:
        connection.close()

    run_migrations(database_url)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT company_name, policy_status FROM companies WHERE company_id = ?",
            ("company_legacy",),
        ).fetchone()
    finally:
        connection.close()

    assert row == ("Legacy Company", "unreviewed")
