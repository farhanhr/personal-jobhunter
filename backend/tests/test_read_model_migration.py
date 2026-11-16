"""Migration tests for the read-model foundation.

The migration must extend the existing chain without disturbing M4A/M4B
operational data, and it must be reversible.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import command
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.migrate import alembic_config, downgrade_database
from personal_job_finder_api.db.tables import (
    companies,
    job_sources,
    opportunities,
    opportunity_run_refs,
    search_runs,
    sync_outbox,
    target_roles,
)

READ_MODEL_TABLES = {
    "opportunities_read_model",
    "job_sources_read_model",
    "sheets_sync_state",
}
PREVIOUS_REVISION = "0003_opportunity_staging"
NOW = format_utc(datetime(2026, 11, 16, 5, 0, tzinfo=UTC))


def test_migration_revision_follows_the_existing_chain(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations

    run_migrations(database_url)
    engine = create_engine_for_url(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()

    assert revision == "0004_read_model"


def test_read_model_tables_exist_only_after_the_new_revision(database_url: str) -> None:
    engine = create_engine_for_url(database_url)
    try:
        command.upgrade(alembic_config(database_url), PREVIOUS_REVISION)
        before = set(sa.inspect(engine).get_table_names())
        assert not READ_MODEL_TABLES & before

        command.upgrade(alembic_config(database_url), "head")
        after = set(sa.inspect(engine).get_table_names())
        assert after >= READ_MODEL_TABLES
    finally:
        engine.dispose()


def test_the_migration_does_not_create_a_contacts_table(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations

    run_migrations(database_url)
    engine = create_engine_for_url(database_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert "contacts_read_model" not in tables


def test_the_migration_adds_nullable_tracker_columns(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations

    run_migrations(database_url)
    engine = create_engine_for_url(database_url)
    try:
        columns = {
            column["name"]: column
            for column in sa.inspect(engine).get_columns("opportunities_read_model")
        }
    finally:
        engine.dispose()

    for name in (
        "job_type",
        "date_applied",
        "application_status",
        "follow_up_date",
        "interview_date",
        "vacancy_url",
        "notes",
    ):
        assert name in columns, name
        assert columns[name]["nullable"] is True


def test_the_migration_keeps_external_identity_lookup_non_unique(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations
    from personal_job_finder_api.db.tables import (
        job_sources_read_model,
        opportunities_read_model,
    )

    run_migrations(database_url)
    engine = create_engine_for_url(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(opportunities_read_model.insert().values(opportunity_id="opp_1"))
            connection.execute(opportunities_read_model.insert().values(opportunity_id="opp_2"))
            connection.execute(
                job_sources_read_model.insert(),
                [
                    {
                        "source_id": "src_1",
                        "opportunity_id": "opp_1",
                        "source_type": "greenhouse",
                        "external_source_id": "1001",
                    },
                    {
                        "source_id": "src_2",
                        "opportunity_id": "opp_2",
                        "source_type": "greenhouse",
                        "external_source_id": "1001",
                    },
                ],
            )
            total = connection.execute(
                sa.select(sa.func.count()).select_from(job_sources_read_model)
            ).scalar_one()
    finally:
        engine.dispose()

    assert total == 2
    engine = create_engine_for_url(database_url)
    try:
        command.upgrade(alembic_config(database_url), PREVIOUS_REVISION)
        with engine.begin() as connection:
            connection.execute(
                target_roles.insert().values(
                    role_id="role_backend",
                    canonical_name="Backend Engineer",
                    aliases_json="[]",
                    excluded_terms_json="[]",
                    enabled=True,
                    created_at_utc=NOW,
                    updated_at_utc=NOW,
                )
            )
            connection.execute(
                companies.insert().values(
                    company_id="company_acme",
                    company_name="Acme Example",
                    ats_type="greenhouse",
                    ats_board_token="acme",
                    enabled=True,
                    policy_status="permitted",
                    created_at_utc=NOW,
                    updated_at_utc=NOW,
                )
            )
            connection.execute(
                search_runs.insert().values(
                    run_id="run_1",
                    idempotency_key="key-1",
                    request_fingerprint="a" * 64,
                    operational_day="2026-11-16",
                    status="completed",
                    accepted_at_utc=NOW,
                    config_snapshot_json="{}",
                )
            )
            connection.execute(
                opportunities.insert().values(
                    opportunity_id="opp_1",
                    company_id="company_acme",
                    company_name="Acme Example",
                    job_title="Backend Engineer",
                    location_status="eligible",
                    work_mode="onsite",
                    remote_scope="not_applicable",
                    first_seen_at_utc=NOW,
                    last_seen_at_utc=NOW,
                    availability_status="unknown",
                    feed_bucket="main",
                    content_hash="b" * 64,
                    created_at_utc=NOW,
                    updated_at_utc=NOW,
                )
            )
            connection.execute(
                job_sources.insert().values(
                    source_id="src_1",
                    opportunity_id="opp_1",
                    source_type="greenhouse",
                    company_id="company_acme",
                    external_source_id="1001",
                    source_url="https://boards.greenhouse.io/acme/jobs/1001",
                    captured_at_utc=NOW,
                    last_checked_at_utc=NOW,
                )
            )
            connection.execute(
                opportunity_run_refs.insert().values(
                    opportunity_id="opp_1",
                    run_id="run_1",
                    source_id="src_1",
                    first_seen_at_utc=NOW,
                    last_seen_at_utc=NOW,
                )
            )
            connection.execute(
                sync_outbox.insert().values(
                    outbox_id="outbox_1",
                    destination="google_sheets",
                    entity_type="opportunity",
                    entity_id="opp_1",
                    operation="upsert",
                    payload_json="{}",
                    payload_hash="c" * 64,
                    status="pending",
                    created_at_utc=NOW,
                    updated_at_utc=NOW,
                )
            )

        command.upgrade(alembic_config(database_url), "head")

        staged_tables = {
            "opportunities": opportunities,
            "job_sources": job_sources,
            "opportunity_run_refs": opportunity_run_refs,
            "sync_outbox": sync_outbox,
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(target_roles)
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(sa.select(sa.func.count()).select_from(companies)).scalar_one()
                == 1
            )
            assert (
                connection.execute(sa.select(sa.func.count()).select_from(search_runs)).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    sa.text("SELECT status FROM search_runs WHERE run_id = 'run_1'")
                ).scalar_one()
                == "completed"
            )
            for name, table in staged_tables.items():
                assert (
                    connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                    == 1
                ), name
            assert (
                connection.execute(
                    sa.text("SELECT status FROM sync_outbox WHERE outbox_id = 'outbox_1'")
                ).scalar_one()
                == "pending"
            )
    finally:
        engine.dispose()


def test_downgrade_removes_only_the_read_model(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations

    run_migrations(database_url)
    engine = create_engine_for_url(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                target_roles.insert().values(
                    role_id="role_backend",
                    canonical_name="Backend Engineer",
                    aliases_json="[]",
                    excluded_terms_json="[]",
                    enabled=True,
                    created_at_utc=NOW,
                    updated_at_utc=NOW,
                )
            )
    finally:
        engine.dispose()

    downgrade_database(database_url, PREVIOUS_REVISION)

    engine = create_engine_for_url(database_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not READ_MODEL_TABLES & tables
        assert {"opportunities", "job_sources", "sync_outbox", "search_runs"} <= tables
        with engine.connect() as connection:
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(target_roles)
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_downgrade_and_upgrade_round_trip_is_clean(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations

    run_migrations(database_url)
    downgrade_database(database_url, PREVIOUS_REVISION)
    run_migrations(database_url)

    engine = create_engine_for_url(database_url)
    try:
        assert set(sa.inspect(engine).get_table_names()) >= READ_MODEL_TABLES
    finally:
        engine.dispose()


def test_upgrade_is_idempotent(database_url: str) -> None:
    from personal_job_finder_api.db.migrate import run_migrations

    run_migrations(database_url)
    run_migrations(database_url)

    engine = create_engine_for_url(database_url)
    try:
        assert set(sa.inspect(engine).get_table_names()) >= READ_MODEL_TABLES
    finally:
        engine.dispose()


def test_metadata_matches_the_migrated_read_model_schema(engine: Engine) -> None:
    from personal_job_finder_api.db.tables import (
        job_sources_read_model,
        opportunities_read_model,
        sheets_sync_state,
    )

    inspector = sa.inspect(engine)
    for table in (
        opportunities_read_model,
        job_sources_read_model,
        sheets_sync_state,
    ):
        migrated_columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        assert set(migrated_columns) == {column.name for column in table.columns}
        for column in table.columns:
            migrated = migrated_columns[column.name]
            assert bool(migrated["nullable"]) == bool(column.nullable), (
                f"{table.name}.{column.name} nullability drifted"
            )
            assert str(migrated["type"]) == column.type.compile(dialect=engine.dialect), (
                f"{table.name}.{column.name} type drifted"
            )

        migrated_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints(table.name)
        }
        metadata_checks = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
        assert migrated_checks == metadata_checks, f"{table.name} CHECK constraints drifted"

        migrated_indexes = {index["name"] for index in inspector.get_indexes(table.name)}
        metadata_indexes = {index.name for index in table.indexes}
        assert migrated_indexes == metadata_indexes, f"{table.name} indexes drifted"
