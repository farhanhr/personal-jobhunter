"""Tests for Job_Sources outbox projection during durable staging.

The canonical ``Job_Sources`` tab holds one row per source record, so staging must
queue that projection alongside the opportunity projection without creating
duplicate work when a record is re-ingested unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import sync_outbox
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.search.staging import OpportunityStagingService

FIXED_NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)


def record(
    *,
    external_id: str = "1001",
    company_id: str = "company_acme",
    source_type: str = "greenhouse",
    location: str | None = "Jakarta, Indonesia",
) -> OpportunityRecord:
    return OpportunityRecord(
        company_id=company_id,
        company_name="Acme Example",
        job_title="Backend Engineer",
        location=classify_location(location),
        description="<p>Build services.</p>",
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type=source_type,
            source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=FIXED_NOW,
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role("Backend Engineer", None, (ROLE,)),
    )


def service(engine: Engine) -> OpportunityStagingService:
    return OpportunityStagingService(engine, clock=lambda: FIXED_NOW)


def outbox_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(sync_outbox).order_by(sync_outbox.c.entity_type)
            )
            .mappings()
            .all()
        ]


def entity_types(engine: Engine) -> list[str]:
    return [str(row["entity_type"]) for row in outbox_rows(engine)]


def test_staging_queues_both_an_opportunity_and_a_source_projection(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    assert entity_types(engine) == ["job_source", "opportunity"]


def test_source_projection_uses_the_source_identity(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    source_projection = next(
        row for row in outbox_rows(engine) if row["entity_type"] == "job_source"
    )
    payload = json.loads(str(source_projection["payload_json"]))

    assert source_projection["entity_id"] == payload["source_id"]
    assert source_projection["entity_id"].startswith("src_")
    assert payload["opportunity_id"].startswith("opp_")
    assert payload["source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert payload["safe_source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert payload["provenance_method"] == "ats_api"
    assert payload["observation_status"] == "seen"
    assert payload["captured_at_utc"] == "2026-11-15T03:00:00.000000Z"


def test_each_source_record_gets_its_own_source_projection(engine: Engine) -> None:
    service(engine).stage(
        run_id="run_1",
        records=[
            record(external_id="1001", source_type="greenhouse"),
            record(external_id="1001", source_type="lever"),
        ],
    )

    rows = outbox_rows(engine)
    source_rows = [row for row in rows if row["entity_type"] == "job_source"]
    opportunity_rows = [row for row in rows if row["entity_type"] == "opportunity"]

    # Identity includes the source type, so two sources stay two records. The
    # schema still allows several source rows to reference one opportunity, and
    # the dispatcher writes each of them keyed by its own source_id.
    assert len(source_rows) == 2
    assert len(opportunity_rows) == 2
    assert {row["entity_id"] for row in source_rows} == {
        json.loads(str(row["payload_json"]))["source_id"] for row in source_rows
    }


def test_reingesting_an_unchanged_record_does_not_requeue_the_source(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    before = {row["entity_type"]: row for row in outbox_rows(engine)}

    service(engine).stage(run_id="run_2", records=[record()])
    after = {row["entity_type"]: row for row in outbox_rows(engine)}

    assert after["job_source"]["outbox_id"] == before["job_source"]["outbox_id"]
    assert after["job_source"]["updated_at_utc"] == before["job_source"]["updated_at_utc"]
    assert after["job_source"]["status"] == "pending"


def test_later_observation_requeues_the_source_projection(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    before = {row["entity_type"]: row for row in outbox_rows(engine)}
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="synced"))

    later = FIXED_NOW.replace(hour=4)
    OpportunityStagingService(engine, clock=lambda: later).stage(
        run_id="run_2",
        records=[record()],
    )

    after = {row["entity_type"]: row for row in outbox_rows(engine)}
    assert after["job_source"]["outbox_id"] == before["job_source"]["outbox_id"]
    assert after["job_source"]["status"] == "pending"
    assert after["job_source"]["synced_at_utc"] is None
    assert json.loads(str(after["job_source"]["payload_json"]))["last_seen_at_utc"] == (
        "2026-11-15T04:00:00.000000Z"
    )


def test_reobserving_at_the_same_instant_keeps_a_synced_source_quiet(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="synced"))

    service(engine).stage(run_id="run_2", records=[record()])

    after = {row["entity_type"]: row for row in outbox_rows(engine)}
    assert after["job_source"]["status"] == "synced"


def test_excluded_records_queue_no_projection_at_all(engine: Engine) -> None:
    summary = service(engine).stage(
        run_id="run_1",
        records=[record(location="Singapore")],
    )

    assert summary.feed_excluded == 1
    assert outbox_rows(engine) == []
    assert summary.pending_sync_count == 0


def test_pending_sync_count_includes_source_projections(engine: Engine) -> None:
    summary = service(engine).stage(run_id="run_1", records=[record()])

    assert summary.pending_sync_count == 2


def test_source_projection_payload_has_no_credentials_or_spreadsheet_id(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    for row in outbox_rows(engine):
        lowered = str(row["payload_json"]).casefold()
        for forbidden in (
            "access_token",
            "refresh_token",
            "client_secret",
            "board_token",
            "spreadsheet_id",
        ):
            assert forbidden not in lowered


def test_excluded_opportunity_tombstones_its_existing_projections(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="synced",
                synced_at_utc="2026-11-15T03:10:00.000000Z",
            )
        )

    service(engine).stage(
        run_id="run_2",
        records=[record(location="Singapore")],
    )

    rows = outbox_rows(engine)
    # The opportunity carries the withdrawal. The source row is provenance and has
    # no feed status of its own, so it is deliberately left untouched.
    opportunity_rows = [row for row in rows if row["entity_type"] == "opportunity"]
    source_rows = [row for row in rows if row["entity_type"] == "job_source"]
    assert len(opportunity_rows) == 1
    assert opportunity_rows[0]["status"] == "pending"
    assert opportunity_rows[0]["operation"] == "withdraw"
    payload = json.loads(str(opportunity_rows[0]["payload_json"]))
    assert payload["operation"] == "withdraw"
    assert payload["location_status"] == "excluded"
    assert len(source_rows) == 1
    assert source_rows[0]["status"] == "synced"


def test_a_retried_projection_is_withdrawn_rather_than_discarded(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="retry_wait",
                attempt_count=1,
                last_error_code="timeout",
            )
        )

    summary = service(engine).stage(
        run_id="run_2",
        records=[record(location="Singapore")],
    )

    opportunity_row = next(
        row for row in outbox_rows(engine) if row["entity_type"] == "opportunity"
    )
    # A retried projection may already exist remotely, so it becomes a withdrawal
    # instead of being deleted as if it had never been sent.
    assert opportunity_row["operation"] == "withdraw"
    assert opportunity_row["status"] == "pending"
    assert summary.pending_sync_count >= 1


def test_a_blocked_projection_is_withdrawn_rather_than_discarded(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=2,
                last_error_code="permission_denied",
            )
        )

    service(engine).stage(
        run_id="run_2",
        records=[record(location="Singapore")],
    )

    opportunity_row = next(
        row for row in outbox_rows(engine) if row["entity_type"] == "opportunity"
    )
    assert opportunity_row["operation"] == "withdraw"
    assert opportunity_row["status"] == "pending"


def test_a_never_attempted_projection_is_discarded_without_a_withdrawal(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    summary = service(engine).stage(
        run_id="run_2",
        records=[record(location="Singapore")],
    )

    assert outbox_rows(engine) == []
    assert summary.pending_sync_count == 0
