"""Tests for durable opportunity staging, deduplication, and outbox creation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.tables import (
    job_sources,
    opportunities,
    opportunity_run_refs,
    sync_outbox,
)
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.search.staging import OpportunityStagingService

FIXED_NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
BACKEND_ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)


def record(
    *,
    external_id: str = "1001",
    company_id: str = "company_acme",
    company_name: str = "Acme Example",
    title: str = "Backend Engineer",
    location: str | None = "Jakarta, Indonesia",
    description: str | None = "<p>Build services.</p>",
    source_url: str = "https://boards.greenhouse.io/acme/jobs/1001",
    source_type: str = "greenhouse",
    captured_at: datetime = FIXED_NOW,
) -> OpportunityRecord:
    return OpportunityRecord(
        company_id=company_id,
        company_name=company_name,
        job_title=title,
        location=classify_location(location),
        description=description,
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type=source_type,
            source_url=source_url,
            external_source_id=external_id,
            captured_at=captured_at,
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, description, (BACKEND_ROLE,)),
    )


def service(engine: Engine, now: datetime = FIXED_NOW) -> OpportunityStagingService:
    return OpportunityStagingService(engine, clock=lambda: now)


def count(engine: Engine, table) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def rows(engine: Engine, table) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(sa.select(table)).mappings().all()]


def test_successful_staging_persists_the_record_and_its_provenance(
    engine: Engine,
) -> None:
    summary = service(engine).stage(
        run_id="run_1",
        records=[record()],
    )

    assert summary.records_received == 1
    assert summary.records_new == 1
    assert summary.records_updated == 0
    assert summary.records_unchanged == 0

    opportunity = rows(engine, opportunities)[0]
    assert opportunity["company_id"] == "company_acme"
    assert opportunity["company_name"] == "Acme Example"
    assert opportunity["job_title"] == "Backend Engineer"
    assert opportunity["description"] == "<p>Build services.</p>"
    assert opportunity["location_raw"] == "Jakarta, Indonesia"
    assert opportunity["city"] == "Jakarta"
    assert opportunity["country"] == "Indonesia"
    assert opportunity["location_status"] == "eligible"
    assert opportunity["work_mode"] == "onsite"
    assert opportunity["target_role_id"] == "role_backend"
    assert opportunity["role_match_type"] == "canonical_title"
    assert opportunity["feed_bucket"] == "main"
    assert opportunity["availability_status"] == "unknown"
    assert opportunity["first_seen_at_utc"] == opportunity["last_seen_at_utc"]

    source = rows(engine, job_sources)[0]
    assert source["source_type"] == "greenhouse"
    assert source["external_source_id"] == "1001"
    assert source["source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert source["safe_source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert source["opportunity_id"] == opportunity["opportunity_id"]

    run_ref = rows(engine, opportunity_run_refs)[0]
    assert run_ref["run_id"] == "run_1"
    assert run_ref["opportunity_id"] == opportunity["opportunity_id"]


def test_empty_connector_result_stages_nothing(engine: Engine) -> None:
    summary = service(engine).stage(run_id="run_1", records=[])

    assert summary.records_received == 0
    assert summary.outbox_enqueued == 0
    assert count(engine, opportunities) == 0
    assert count(engine, sync_outbox) == 0


def test_reingesting_the_same_record_does_not_duplicate_it(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    summary = service(engine).stage(run_id="run_2", records=[record()])

    assert summary.records_new == 0
    assert summary.records_unchanged == 1
    assert count(engine, opportunities) == 1
    assert count(engine, job_sources) == 1


def test_reingesting_unchanged_data_does_not_queue_new_outbox_work(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    before = rows(engine, sync_outbox)[0]

    service(engine).stage(run_id="run_2", records=[record()])
    after = rows(engine, sync_outbox)[0]

    assert count(engine, sync_outbox) == 1
    assert after["outbox_id"] == before["outbox_id"]
    assert after["updated_at_utc"] == before["updated_at_utc"]


def test_changed_record_updates_the_row_and_keeps_its_identity(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    original_id = rows(engine, opportunities)[0]["opportunity_id"]

    summary = service(engine).stage(
        run_id="run_2",
        records=[record(title="Senior Backend Engineer", description="<p>New scope.</p>")],
    )

    assert summary.records_updated == 1
    assert summary.records_new == 0
    stored = rows(engine, opportunities)[0]
    assert stored["opportunity_id"] == original_id
    assert stored["job_title"] == "Senior Backend Engineer"
    assert stored["description"] == "<p>New scope.</p>"
    assert count(engine, opportunities) == 1


def test_changed_record_produces_pending_sync_work(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    first = rows(engine, sync_outbox)[0]

    service(engine).stage(
        run_id="run_2",
        records=[record(description="<p>Changed description.</p>")],
    )

    updated = rows(engine, sync_outbox)[0]
    assert count(engine, sync_outbox) == 1
    assert updated["outbox_id"] == first["outbox_id"]
    assert updated["payload_hash"] != first["payload_hash"]
    assert updated["status"] == "pending"
    assert json.loads(updated["payload_json"])["description"] == ("<p>Changed description.</p>")


def test_changed_record_after_sync_requeues_the_projection(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="synced", synced_at_utc="2026-11-15T04:00:00.000000Z"
            )
        )

    service(engine).stage(
        run_id="run_2",
        records=[record(title="Backend Engineer II")],
    )

    updated = rows(engine, sync_outbox)[0]
    assert updated["status"] == "pending"
    assert updated["synced_at_utc"] is None


def test_same_title_from_different_sources_stays_separate(engine: Engine) -> None:
    service(engine).stage(
        run_id="run_1",
        records=[
            record(external_id="1001", company_id="company_acme"),
            record(external_id="2002", company_id="company_other"),
            record(external_id="1001", company_id="company_acme", source_type="lever"),
        ],
    )

    assert count(engine, opportunities) == 3
    assert count(engine, job_sources) == 3
    assert len({row["opportunity_id"] for row in rows(engine, opportunities)}) == 3


def test_a_later_run_retains_provenance_to_both_runs(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    service(engine).stage(run_id="run_2", records=[record()])

    refs = rows(engine, opportunity_run_refs)
    assert len(refs) == 2
    assert {ref["run_id"] for ref in refs} == {"run_1", "run_2"}
    assert len({ref["opportunity_id"] for ref in refs}) == 1


def test_feed_bucket_separates_main_review_and_excluded(engine: Engine) -> None:
    service(engine).stage(
        run_id="run_1",
        records=[
            record(external_id="1", title="Backend Engineer", location="Jakarta"),
            record(external_id="2", title="Backend Engineer", location="Remote"),
            record(external_id="3", title="Backend Engineer", location="Singapore"),
            record(external_id="4", title="Accountant", location="Jakarta"),
        ],
    )

    with engine.connect() as connection:
        joined = connection.execute(
            sa.select(
                job_sources.c.external_source_id,
                opportunities.c.feed_bucket,
            ).join(
                opportunities,
                opportunities.c.opportunity_id == job_sources.c.opportunity_id,
            )
        ).mappings()
        buckets = {row["external_source_id"]: row["feed_bucket"] for row in joined}

    assert buckets == {
        "1": "main",
        "2": "review",
        "3": "excluded",
        "4": "excluded",
    }


def test_outbox_payload_contains_no_credentials_or_configuration(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    payload = rows(engine, sync_outbox)[0]["payload_json"]
    lowered = str(payload).casefold()
    for forbidden in (
        "access_token",
        "refresh_token",
        "client_secret",
        "board_token",
        "config_snapshot",
        "spreadsheet_id",
    ):
        assert forbidden not in lowered


def test_outbox_payload_bounds_the_description(engine: Engine) -> None:
    huge = "x" * 200_000
    service(engine).stage(run_id="run_1", records=[record(description=huge)])

    payload = json.loads(rows(engine, sync_outbox)[0]["payload_json"])
    assert len(payload["description"]) <= 50_000


def test_staging_rolls_back_completely_when_a_write_fails(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_job_finder_api.repositories import opportunities as repo_module

    def explode(self, connection, **kwargs):
        raise RuntimeError("simulated outbox failure")

    monkeypatch.setattr(
        repo_module.OpportunityStagingRepository,
        "upsert_outbox",
        explode,
    )

    with pytest.raises(RuntimeError):
        service(engine).stage(
            run_id="run_1",
            records=[record(external_id="1"), record(external_id="2")],
        )

    assert count(engine, opportunities) == 0
    assert count(engine, job_sources) == 0
    assert count(engine, opportunity_run_refs) == 0
    assert count(engine, sync_outbox) == 0


def test_staged_data_survives_a_new_engine_instance(
    engine: Engine,
    database_url: str,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    restarted = create_engine_for_url(database_url)
    try:
        assert count(restarted, opportunities) == 1
        assert count(restarted, sync_outbox) == 1
        assert rows(restarted, opportunities)[0]["job_title"] == "Backend Engineer"
    finally:
        restarted.dispose()


def test_unsafe_source_url_is_stored_but_not_marked_safe(engine: Engine) -> None:
    service(engine).stage(
        run_id="run_1",
        records=[record(source_url="javascript:alert(1)")],
    )

    source = rows(engine, job_sources)[0]
    assert source["source_url"] == "javascript:alert(1)"
    assert source["safe_source_url"] is None


def test_unchanged_payload_preserves_retry_state(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="retry_wait",
                attempt_count=2,
                next_attempt_at_utc="2026-11-15T05:00:00.000000Z",
            )
        )

    service(engine).stage(run_id="run_2", records=[record()])

    stored = rows(engine, sync_outbox)[0]
    assert stored["status"] == "retry_wait"
    assert stored["attempt_count"] == 2
    assert stored["next_attempt_at_utc"] == "2026-11-15T05:00:00.000000Z"


def test_excluded_records_are_staged_but_not_queued_for_sheets(
    engine: Engine,
) -> None:
    service(engine).stage(
        run_id="run_1",
        records=[
            record(external_id="1", location="Jakarta"),
            record(external_id="2", location="Singapore"),
        ],
    )

    assert count(engine, opportunities) == 2
    # Only the main-feed record needs a Sheets projection.
    assert count(engine, sync_outbox) == 1
    queued = json.loads(rows(engine, sync_outbox)[0]["payload_json"])
    assert queued["feed_bucket"] == "main"


def test_review_records_are_queued_for_sheets(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record(location="Remote")])

    assert count(engine, sync_outbox) == 1
    queued = json.loads(rows(engine, sync_outbox)[0]["payload_json"])
    assert queued["feed_bucket"] == "review"


def test_payload_keeps_the_original_first_seen_time_after_an_update(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    first_seen = rows(engine, opportunities)[0]["first_seen_at_utc"]

    service(engine).stage(
        run_id="run_2",
        records=[record(title="Backend Engineer II")],
    )

    payload = json.loads(rows(engine, sync_outbox)[0]["payload_json"])
    assert payload["first_seen_at_utc"] == first_seen
    assert payload["job_title"] == "Backend Engineer II"


def test_payload_hash_is_a_hash_of_the_payload(engine: Engine) -> None:
    import hashlib

    service(engine).stage(run_id="run_1", records=[record()])

    outbox = rows(engine, sync_outbox)[0]
    expected = hashlib.sha256(str(outbox["payload_json"]).encode("utf-8")).hexdigest()
    assert outbox["payload_hash"] == expected


def test_unchanged_reingest_bumps_last_seen(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    before = rows(engine, opportunities)[0]["last_seen_at_utc"]

    later = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
    service(engine, now=later).stage(run_id="run_2", records=[record()])

    after = rows(engine, opportunities)[0]["last_seen_at_utc"]
    assert after != before
    assert after.startswith("2026-11-15T05:00:00")


def test_duplicate_identity_inside_one_batch_collapses(engine: Engine) -> None:
    summary = service(engine).stage(
        run_id="run_1",
        records=[record(external_id="1"), record(external_id="1")],
    )

    assert count(engine, opportunities) == 1
    assert summary.records_received == 2
    assert summary.records_new == 1


def test_staging_requires_a_run_id(engine: Engine) -> None:
    for run_id in ("", "   "):
        try:
            service(engine).stage(run_id=run_id, records=[record()])
        except ValueError:
            continue
        raise AssertionError("expected ValueError for an empty run id")


def test_outbox_entries_can_be_listed_for_a_later_sync(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])

    pending = service(engine).list_outbox(status="pending")

    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert pending[0].entity_id.startswith("opp_")
    assert pending[0].run_id == "run_1"
    assert service(engine).list_outbox(status="synced") == []


def test_staging_reports_the_run_association(engine: Engine) -> None:
    summary = service(engine).stage(run_id="run_1", records=[record()])

    assert summary.run_id == "run_1"
    assert summary.feed_main == 1
    assert summary.feed_review == 0
    assert summary.feed_excluded == 0
    assert summary.outbox_enqueued == 1
