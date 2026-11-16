"""Tests for read-model persistence primitives.

The repository owns the atomic publication boundary that a later refresh service
will call. A failed or partial refresh must leave the last known good snapshot
untouched, so most of these tests assert what survives a failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import (
    job_sources_read_model,
    opportunities_read_model,
)
from personal_job_finder_api.repositories.read_model import (
    ReadModelRepository,
    SnapshotWriteError,
)

FIRST_REFRESH = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)
SECOND_REFRESH = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
FAILED_ATTEMPT = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
FIRST_REFRESH_ISO = format_utc(FIRST_REFRESH)
SECOND_REFRESH_ISO = format_utc(SECOND_REFRESH)
FAILED_ATTEMPT_ISO = format_utc(FAILED_ATTEMPT)


def opportunity(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "opportunity_id": "opp_1",
        "company_name": "Acme Example",
        "job_title_original": "Backend Engineer",
        "job_title_normalized": "backend engineer",
        "target_role_id": "role_backend",
        "role_match_type": "canonical_title",
        "location_status": "eligible",
        "work_mode": "onsite",
        "remote_scope": "not_applicable",
        "availability_status": "unknown",
        "user_status": "new",
        "first_seen_at_utc": FIRST_REFRESH_ISO,
        "last_seen_at_utc": FIRST_REFRESH_ISO,
        "created_at_utc": FIRST_REFRESH_ISO,
        "updated_at_utc": FIRST_REFRESH_ISO,
    }
    row.update(overrides)
    return row


def source(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_id": "src_1",
        "opportunity_id": "opp_1",
        "source_type": "greenhouse",
        "source_url": "https://boards.greenhouse.io/acme/jobs/1001",
        "external_source_id": "1001",
        "captured_at_utc": FIRST_REFRESH_ISO,
        "provenance_method": "ats_api",
        "observation_status": "seen",
    }
    row.update(overrides)
    return row


def repository(engine: Engine) -> ReadModelRepository:
    return ReadModelRepository(engine)


def publish(engine: Engine, **overrides: Any) -> None:
    """Publish one complete snapshot using the default fixture rows."""

    payload: dict[str, Any] = {
        "opportunities": [opportunity()],
        "job_sources": [source()],
        "refreshed_at": FIRST_REFRESH,
        "spreadsheet_schema_version": "1",
    }
    payload.update(overrides)
    repository(engine).replace_snapshot(**payload)


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            "opportunities": connection.execute(
                sa.select(sa.func.count()).select_from(opportunities_read_model)
            ).scalar_one(),
            "job_sources": connection.execute(
                sa.select(sa.func.count()).select_from(job_sources_read_model)
            ).scalar_one(),
        }


def titles(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(opportunities_read_model.c.job_title_original).order_by(
                    opportunities_read_model.c.opportunity_id
                )
            ).scalars()
        )


def test_replacing_an_empty_read_model_publishes_every_row(engine: Engine) -> None:
    publish(engine)

    assert counts(engine) == {"opportunities": 1, "job_sources": 1}


def test_a_successful_refresh_records_the_full_refresh_time(engine: Engine) -> None:
    publish(engine)

    state = repository(engine).get_sync_state()

    assert state is not None
    assert state["last_full_refresh_at_utc"] == FIRST_REFRESH_ISO
    assert state["last_attempt_at_utc"] == FIRST_REFRESH_ISO
    assert state["last_refresh_outcome"] == "succeeded"
    assert state["last_refresh_error_code"] is None
    assert state["spreadsheet_schema_version"] == "1"


def test_a_second_refresh_replaces_the_previous_snapshot(engine: Engine) -> None:
    publish(engine)
    publish(
        engine,
        opportunities=[opportunity(opportunity_id="opp_2", job_title_original="Data Engineer")],
        job_sources=[],
        refreshed_at=SECOND_REFRESH,
    )

    assert counts(engine) == {"opportunities": 1, "job_sources": 0}
    assert titles(engine) == ["Data Engineer"]
    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["last_full_refresh_at_utc"] == SECOND_REFRESH_ISO


def test_a_manual_sheet_edit_becomes_visible_after_the_next_refresh(
    engine: Engine,
) -> None:
    publish(engine)
    publish(
        engine,
        opportunities=[opportunity(user_status="saved", job_title_original="Renamed Role")],
        refreshed_at=SECOND_REFRESH,
    )

    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(
                opportunities_read_model.c.user_status,
                opportunities_read_model.c.job_title_original,
            )
        ).one()

    assert stored.user_status == "saved"
    assert stored.job_title_original == "Renamed Role"


def test_a_snapshot_that_violates_the_schema_publishes_nothing(engine: Engine) -> None:
    publish(engine)

    with pytest.raises(SnapshotWriteError):
        publish(
            engine,
            opportunities=[opportunity(opportunity_id="opp_2", location_status="excluded_now")],
            job_sources=[],
            refreshed_at=SECOND_REFRESH,
        )

    # The previous snapshot and its timestamp survive a rejected publication.
    assert counts(engine) == {"opportunities": 1, "job_sources": 1}
    assert titles(engine) == ["Backend Engineer"]
    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["last_full_refresh_at_utc"] == FIRST_REFRESH_ISO


def test_a_source_without_its_opportunity_aborts_the_whole_publication(
    engine: Engine,
) -> None:
    publish(engine)

    with pytest.raises(SnapshotWriteError):
        publish(
            engine,
            opportunities=[opportunity(opportunity_id="opp_2")],
            job_sources=[source(opportunity_id="opp_missing")],
            refreshed_at=SECOND_REFRESH,
        )

    assert counts(engine) == {"opportunities": 1, "job_sources": 1}


def test_recording_a_failed_refresh_keeps_the_last_known_good_snapshot(
    engine: Engine,
) -> None:
    publish(engine)

    repository(engine).record_refresh_failure(
        attempted_at=FAILED_ATTEMPT,
        error_code="timeout",
    )

    assert counts(engine) == {"opportunities": 1, "job_sources": 1}
    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["last_full_refresh_at_utc"] == FIRST_REFRESH_ISO
    assert state["last_attempt_at_utc"] == FAILED_ATTEMPT_ISO
    assert state["last_refresh_outcome"] == "failed"
    assert state["last_refresh_error_code"] == "timeout"


def test_recording_a_failure_before_any_refresh_creates_the_state_row(
    engine: Engine,
) -> None:
    repository(engine).record_refresh_failure(
        attempted_at=FAILED_ATTEMPT,
        error_code="spreadsheet_not_found",
    )

    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["last_full_refresh_at_utc"] is None
    assert state["last_refresh_outcome"] == "failed"


def test_sync_state_absent_before_the_first_attempt(engine: Engine) -> None:
    assert repository(engine).get_sync_state() is None


def test_a_later_successful_refresh_clears_a_previous_failure(engine: Engine) -> None:
    publish(engine)
    repository(engine).record_refresh_failure(
        attempted_at=FAILED_ATTEMPT,
        error_code="server_error",
    )

    publish(engine, refreshed_at=SECOND_REFRESH)

    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["last_refresh_outcome"] == "succeeded"
    assert state["last_refresh_error_code"] is None
    assert state["last_full_refresh_at_utc"] == SECOND_REFRESH_ISO


def test_publishing_nothing_yet_still_records_a_successful_empty_snapshot(
    engine: Engine,
) -> None:
    publish(engine, opportunities=[], job_sources=[])

    assert counts(engine) == {"opportunities": 0, "job_sources": 0}
    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["last_refresh_outcome"] == "succeeded"


def test_snapshot_counts_report_the_projected_rows(engine: Engine) -> None:
    publish(
        engine,
        opportunities=[opportunity(), opportunity(opportunity_id="opp_2")],
        job_sources=[
            source(),
            source(
                source_id="src_2",
                opportunity_id="opp_2",
                external_source_id="2002",
            ),
        ],
    )

    assert repository(engine).snapshot_counts() == {
        "opportunities": 2,
        "job_sources": 2,
    }


def test_tracker_values_round_trip_through_a_snapshot(engine: Engine) -> None:
    tracked = {
        "job_type": "Development Program",
        "date_applied": "2026-11-10",
        "application_status": "Interview",
        "follow_up_date": "2026-11-18",
        "interview_date": "2026-11-20",
        "vacancy_url": "https://apply.example.test/acme/1001",
        "notes": "Referred by a former colleague.",
    }
    publish(engine, opportunities=[opportunity(**tracked)])

    with engine.connect() as connection:
        stored = connection.execute(sa.select(opportunities_read_model)).mappings().one()

    assert {column: stored[column] for column in tracked} == tracked


def test_blank_user_status_survives_a_refresh_as_blank(engine: Engine) -> None:
    publish(engine, opportunities=[opportunity(user_status=None)])

    with engine.connect() as connection:
        stored = connection.execute(sa.select(opportunities_read_model.c.user_status)).scalar_one()

    assert stored is None


def test_external_identity_lookup_returns_every_match_in_stable_order(
    engine: Engine,
) -> None:
    """The lookup key is not unique, so every canonical row must be returned."""

    publish(
        engine,
        opportunities=[opportunity(), opportunity(opportunity_id="opp_2")],
        job_sources=[
            source(source_id="src_b", external_source_id="1001"),
            source(
                source_id="src_a",
                opportunity_id="opp_2",
                external_source_id="1001",
            ),
        ],
    )

    matches = repository(engine).find_job_sources_by_external_identity(
        source_type="greenhouse",
        external_source_id="1001",
    )

    assert [row["source_id"] for row in matches] == ["src_a", "src_b"]


def test_external_identity_lookup_returns_empty_for_an_unknown_identity(
    engine: Engine,
) -> None:
    publish(engine)

    assert (
        repository(engine).find_job_sources_by_external_identity(
            source_type="lever",
            external_source_id="1001",
        )
        == []
    )


def test_external_identity_lookup_ignores_an_absent_external_id(engine: Engine) -> None:
    publish(engine, job_sources=[source(external_source_id=None)])

    assert (
        repository(engine).find_job_sources_by_external_identity(
            source_type="greenhouse",
            external_source_id=None,
        )
        == []
    )


def test_url_lookup_returns_every_match_in_stable_order(engine: Engine) -> None:
    """A duplicated URL is ambiguous; the caller must review, not auto-merge."""

    publish(
        engine,
        opportunities=[opportunity(), opportunity(opportunity_id="opp_2")],
        job_sources=[
            source(source_id="src_b", external_source_id="2002"),
            source(
                source_id="src_a",
                opportunity_id="opp_2",
                external_source_id="3003",
            ),
        ],
    )

    matches = repository(engine).find_job_sources_by_url(
        source_url="https://boards.greenhouse.io/acme/jobs/1001"
    )

    assert [row["source_id"] for row in matches] == ["src_a", "src_b"]


def test_omitting_the_schema_version_on_success_keeps_the_previous_version(
    engine: Engine,
) -> None:
    publish(engine, spreadsheet_schema_version="3")
    publish(engine, spreadsheet_schema_version=None, refreshed_at=SECOND_REFRESH)

    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["spreadsheet_schema_version"] == "3"


def test_a_first_refresh_without_a_schema_version_stores_none(engine: Engine) -> None:
    publish(engine, spreadsheet_schema_version=None)

    state = repository(engine).get_sync_state()
    assert state is not None
    assert state["spreadsheet_schema_version"] is None


@pytest.mark.parametrize(
    "error_code",
    [
        "Drive read failed",
        "timeout.",
        "Timeout",
        "../secret",
        "a" * 65,
    ],
)
def test_a_unsanitized_failure_code_is_rejected_before_it_is_stored(
    engine: Engine,
    error_code: str,
) -> None:
    with pytest.raises(ValueError):
        repository(engine).record_refresh_failure(
            attempted_at=FAILED_ATTEMPT,
            error_code=error_code,
        )

    assert repository(engine).get_sync_state() is None


def test_refresh_does_not_touch_operational_staging_tables(engine: Engine) -> None:
    """The read model must not be confused with the M4A staging projection."""

    from personal_job_finder_api.db.tables import opportunities, sync_outbox

    publish(engine)

    with engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(opportunities)).scalar_one()
            == 0
        )
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(sync_outbox)).scalar_one()
            == 0
        )


def test_replace_snapshot_requires_ordered_identities_to_be_unique(engine: Engine) -> None:
    with pytest.raises(SnapshotWriteError):
        publish(engine, opportunities=[opportunity(), opportunity()])

    assert counts(engine) == {"opportunities": 0, "job_sources": 0}
