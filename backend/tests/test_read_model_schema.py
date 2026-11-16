"""Schema tests for the SQLite read-model foundation.

The read model is a local projection of the canonical Google Sheets tabs, so its
columns must mirror the accepted sheet headers exactly. These tests pin that
mapping so a header change cannot silently drift away from the cache schema.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import (
    job_sources_read_model,
    opportunities_read_model,
    sheets_sync_state,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    OPPORTUNITY_TRACKER_HEADERS,
)

NOW = format_utc(datetime(2026, 11, 16, 5, 0, tzinfo=UTC))

SYNC_STATE_COLUMNS = {
    "state_id",
    "last_full_refresh_at_utc",
    "last_attempt_at_utc",
    "last_refresh_outcome",
    "last_refresh_error_code",
    "spreadsheet_schema_version",
    "updated_at_utc",
}


def columns_of(engine: Engine, table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table_name)}


def opportunity_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "opportunity_id": "opp_1",
        "company_name": "Acme Example",
        "job_title_original": "Backend Engineer",
        "job_title_normalized": "backend engineer",
        "location_status": "eligible",
        "work_mode": "onsite",
        "remote_scope": "not_applicable",
        "availability_status": "unknown",
        "user_status": "new",
        "first_seen_at_utc": NOW,
        "last_seen_at_utc": NOW,
        "created_at_utc": NOW,
        "updated_at_utc": NOW,
    }
    row.update(overrides)
    return row


def source_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "source_id": "src_1",
        "opportunity_id": "opp_1",
        "source_type": "greenhouse",
        "source_url": "https://boards.greenhouse.io/acme/jobs/1001",
        "external_source_id": "1001",
        "captured_at_utc": NOW,
        "provenance_method": "ats_api",
        "observation_status": "seen",
    }
    row.update(overrides)
    return row


def insert_opportunity(engine: Engine, **overrides: object) -> None:
    with engine.begin() as connection:
        connection.execute(opportunities_read_model.insert().values(**opportunity_row(**overrides)))


def insert_source(engine: Engine, **overrides: object) -> None:
    with engine.begin() as connection:
        connection.execute(job_sources_read_model.insert().values(**source_row(**overrides)))


def test_read_model_tables_are_created_by_migrations(engine: Engine) -> None:
    tables = set(sa.inspect(engine).get_table_names())
    assert {
        "opportunities_read_model",
        "job_sources_read_model",
        "sheets_sync_state",
    } <= tables
    assert "contacts_read_model" not in tables


def test_opportunity_read_model_mirrors_the_accepted_sheet_headers(engine: Engine) -> None:
    assert columns_of(engine, "opportunities_read_model") == set(OPPORTUNITY_HEADERS)


def test_job_source_read_model_mirrors_the_accepted_sheet_headers(engine: Engine) -> None:
    assert columns_of(engine, "job_sources_read_model") == set(JOB_SOURCE_HEADERS)


def test_sync_state_columns_support_stale_and_outcome_reporting(engine: Engine) -> None:
    assert columns_of(engine, "sheets_sync_state") == SYNC_STATE_COLUMNS


def test_opportunity_identity_is_the_primary_key(engine: Engine) -> None:
    primary_key = sa.inspect(engine).get_pk_constraint("opportunities_read_model")
    assert primary_key["constrained_columns"] == ["opportunity_id"]


def test_job_source_identity_is_the_primary_key(engine: Engine) -> None:
    primary_key = sa.inspect(engine).get_pk_constraint("job_sources_read_model")
    assert primary_key["constrained_columns"] == ["source_id"]


def test_duplicate_opportunity_identity_is_rejected(engine: Engine) -> None:
    insert_opportunity(engine)

    with pytest.raises(IntegrityError):
        insert_opportunity(engine)


def test_duplicate_source_id_is_rejected(engine: Engine) -> None:
    insert_opportunity(engine)
    insert_source(engine)

    with pytest.raises(IntegrityError):
        insert_source(engine)


def test_source_requires_an_existing_opportunity(engine: Engine) -> None:
    with pytest.raises(IntegrityError):
        insert_source(engine, opportunity_id="opp_missing")


def test_deleting_an_opportunity_cascades_to_its_sources(engine: Engine) -> None:
    insert_opportunity(engine)
    insert_source(engine)

    with engine.begin() as connection:
        connection.execute(
            opportunities_read_model.delete().where(
                opportunities_read_model.c.opportunity_id == "opp_1"
            )
        )
        remaining = connection.execute(
            sa.select(sa.func.count()).select_from(job_sources_read_model)
        ).scalar_one()

    assert remaining == 0


def test_source_dedup_lookup_attributes_are_not_unique(engine: Engine) -> None:
    """A non-unique lookup is a dedup signal, not a database constraint.

    Two companies can legitimately publish the same ATS external id, and the
    canonical identity is always ``source_id``. Enforcing uniqueness here would
    reject valid canonical rows and force an arbitrary merge later.
    """

    insert_opportunity(engine)
    insert_source(engine)
    insert_source(engine, source_id="src_2")

    with engine.connect() as connection:
        total = connection.execute(
            sa.select(sa.func.count()).select_from(job_sources_read_model)
        ).scalar_one()

    assert total == 2


def test_the_same_external_id_from_a_different_source_type_is_allowed(
    engine: Engine,
) -> None:
    insert_opportunity(engine)
    insert_source(engine)
    insert_source(engine, source_id="src_2", source_type="lever")

    with engine.connect() as connection:
        total = connection.execute(
            sa.select(sa.func.count()).select_from(job_sources_read_model)
        ).scalar_one()

    assert total == 2


def test_multiple_sources_without_an_external_id_are_allowed(engine: Engine) -> None:
    insert_opportunity(engine)
    insert_source(engine, external_source_id=None)
    insert_source(engine, source_id="src_2", external_source_id=None)

    with engine.connect() as connection:
        total = connection.execute(
            sa.select(sa.func.count()).select_from(job_sources_read_model)
        ).scalar_one()

    assert total == 2


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("location_status", "possibly_closed"),
        ("role_match_type", "semantic_similarity"),
        ("work_mode", "fully_remote"),
        ("remote_scope", "asia"),
        ("availability_status", "not_seen"),
        ("user_status", "archived"),
    ],
)
def test_opportunity_enum_columns_reject_unknown_values(
    engine: Engine,
    column: str,
    value: str,
) -> None:
    with pytest.raises(IntegrityError):
        insert_opportunity(engine, **{column: value})


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("source_type", "linkedin_scrape"),
        ("provenance_method", "browser_automation"),
        ("observation_status", "possibly_closed"),
    ],
)
def test_source_enum_columns_reject_unknown_values(
    engine: Engine,
    column: str,
    value: str,
) -> None:
    insert_opportunity(engine)

    with pytest.raises(IntegrityError):
        insert_source(engine, **{column: value})


def test_absent_optional_values_stay_null_rather_than_being_fabricated(
    engine: Engine,
) -> None:
    insert_opportunity(engine, employment_type=None, deadline=None, description=None)
    insert_source(engine, author_name=None, source_text_excerpt=None, posted_at=None)

    with engine.connect() as connection:
        opportunity = connection.execute(sa.select(opportunities_read_model)).mappings().one()
        source = connection.execute(sa.select(job_sources_read_model)).mappings().one()

    assert opportunity["employment_type"] is None
    assert opportunity["deadline"] is None
    assert opportunity["description"] is None
    assert source["author_name"] is None
    assert source["source_text_excerpt"] is None
    assert source["posted_at"] is None


def test_user_status_is_preserved_as_a_stored_value(engine: Engine) -> None:
    insert_opportunity(engine, user_status="saved")

    with engine.connect() as connection:
        stored = connection.execute(sa.select(opportunities_read_model.c.user_status)).scalar_one()

    assert stored == "saved"


def test_blank_user_status_is_stored_blank_not_defaulted(engine: Engine) -> None:
    insert_opportunity(engine, user_status=None)

    with engine.connect() as connection:
        stored = connection.execute(sa.select(opportunities_read_model.c.user_status)).scalar_one()

    assert stored is None


def test_tracker_columns_mirror_the_canonical_header_suffix(engine: Engine) -> None:
    assert OPPORTUNITY_HEADERS[-len(OPPORTUNITY_TRACKER_HEADERS) :] == OPPORTUNITY_TRACKER_HEADERS
    assert columns_of(engine, "opportunities_read_model") >= set(OPPORTUNITY_TRACKER_HEADERS)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("job_type", "Development Program"),
        ("date_applied", "2026-11-10"),
        ("application_status", "No Feedback"),
        ("follow_up_date", "2026-11-18"),
        ("interview_date", "2026-11-20"),
        ("vacancy_url", "https://apply.example.test/acme/1001"),
        ("notes", "Referred by a former colleague."),
    ],
)
def test_tracker_values_are_stored_verbatim(
    engine: Engine,
    column: str,
    value: str,
) -> None:
    insert_opportunity(engine, **{column: value})

    with engine.connect() as connection:
        stored = connection.execute(sa.select(opportunities_read_model.c[column])).scalar_one()

    assert stored == value


def test_free_text_tracker_statuses_accept_any_user_wording(engine: Engine) -> None:
    insert_opportunity(
        engine,
        job_type="Contract to hire",
        application_status="Waiting on panel debrief",
    )

    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(
                opportunities_read_model.c.job_type,
                opportunities_read_model.c.application_status,
            )
        ).one()

    assert stored.job_type == "Contract to hire"
    assert stored.application_status == "Waiting on panel debrief"


def test_absent_tracker_values_stay_blank(engine: Engine) -> None:
    insert_opportunity(engine)

    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(
                *(opportunities_read_model.c[column] for column in OPPORTUNITY_TRACKER_HEADERS)
            )
        ).one()

    assert tuple(stored) == (None,) * len(OPPORTUNITY_TRACKER_HEADERS)


def test_sync_state_is_a_single_row(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sheets_sync_state.insert().values(
                state_id="google_sheets",
                last_full_refresh_at_utc=NOW,
                last_attempt_at_utc=NOW,
                last_refresh_outcome="succeeded",
                last_refresh_error_code=None,
                spreadsheet_schema_version="1",
                updated_at_utc=NOW,
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sheets_sync_state.insert().values(
                state_id="google_sheets",
                last_full_refresh_at_utc=NOW,
                last_attempt_at_utc=NOW,
                last_refresh_outcome="succeeded",
                last_refresh_error_code=None,
                spreadsheet_schema_version="1",
                updated_at_utc=NOW,
            )
        )


def test_sync_state_rejects_a_second_state_identity(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sheets_sync_state.insert().values(
                state_id="google_sheets",
                updated_at_utc=NOW,
            )
        )

    # The singleton CHECK must reject any other state id on its own, not just
    # rely on the primary key rejecting a repeated "google_sheets" row.
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sheets_sync_state.insert().values(
                state_id="another_provider",
                updated_at_utc=NOW,
            )
        )


def test_sync_state_rejects_an_unknown_outcome(engine: Engine) -> None:
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sheets_sync_state.insert().values(
                state_id="google_sheets",
                last_full_refresh_at_utc=None,
                last_attempt_at_utc=NOW,
                last_refresh_outcome="partially_broken",
                last_refresh_error_code=None,
                spreadsheet_schema_version=None,
                updated_at_utc=NOW,
            )
        )


def test_indexes_cover_dashboard_and_lookup_queries(engine: Engine) -> None:
    opportunity_indexes = {
        index["name"] for index in sa.inspect(engine).get_indexes("opportunities_read_model")
    }
    source_indexes = {
        index["name"] for index in sa.inspect(engine).get_indexes("job_sources_read_model")
    }

    assert {
        "ix_opportunities_read_model_status_seen",
        "ix_opportunities_read_model_user_status",
        "ix_opportunities_read_model_target_role",
        "ix_opportunities_read_model_date_applied",
        "ix_opportunities_read_model_interview_date",
    } <= opportunity_indexes
    assert {
        "ix_job_sources_read_model_opportunity",
        "ix_job_sources_read_model_external_identity",
        "ix_job_sources_read_model_source_url",
    } <= source_indexes
