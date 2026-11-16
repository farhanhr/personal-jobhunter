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


def outbox_for(engine: Engine, entity_type: str) -> list[dict[str, object]]:
    """Return outbox rows for one entity type, ordered by creation."""

    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(sync_outbox)
                .where(sync_outbox.c.entity_type == entity_type)
                .order_by(sync_outbox.c.created_at_utc, sync_outbox.c.outbox_id)
            )
            .mappings()
            .all()
        ]


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
    before = outbox_for(engine, "opportunity")[0]

    service(engine).stage(run_id="run_2", records=[record()])
    after = outbox_for(engine, "opportunity")[0]

    assert count(engine, sync_outbox) == 2
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
    first = outbox_for(engine, "opportunity")[0]

    service(engine).stage(
        run_id="run_2",
        records=[record(description="<p>Changed description.</p>")],
    )

    updated = outbox_for(engine, "opportunity")[0]
    assert count(engine, sync_outbox) == 2
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

    updated = outbox_for(engine, "opportunity")[0]
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

    payload = outbox_for(engine, "opportunity")[0]["payload_json"]
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

    payload = json.loads(outbox_for(engine, "opportunity")[0]["payload_json"])
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
        assert count(restarted, sync_outbox) == 2
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
    assert count(engine, sync_outbox) == 2
    queued = json.loads(outbox_for(engine, "opportunity")[0]["payload_json"])
    assert queued["feed_bucket"] == "main"


def test_review_records_are_queued_for_sheets(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record(location="Remote")])

    assert count(engine, sync_outbox) == 2
    queued = json.loads(outbox_for(engine, "opportunity")[0]["payload_json"])
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

    payload = json.loads(outbox_for(engine, "opportunity")[0]["payload_json"])
    assert payload["first_seen_at_utc"] == first_seen
    assert payload["job_title"] == "Backend Engineer II"


def test_payload_hash_is_a_hash_of_the_payload(engine: Engine) -> None:
    import hashlib

    service(engine).stage(run_id="run_1", records=[record()])

    outbox = outbox_for(engine, "opportunity")[0]
    expected = hashlib.sha256(str(outbox["payload_json"]).encode("utf-8")).hexdigest()
    assert outbox["payload_hash"] == expected


def seed_pending_tracker_payload(
    engine: Engine,
    tracker_values: dict[str, str],
    *,
    status: str = "pending",
    attempt_count: int = 2,
    next_attempt_at_utc: str | None = None,
) -> None:
    """Simulate the pending user-owned payload a tracker patch will produce."""

    import hashlib

    stored = outbox_for(engine, "opportunity")[0]
    payload = json.loads(str(stored["payload_json"]))
    payload.update(tracker_values)
    payload["_pending_user_fields"] = sorted(tracker_values)
    payload_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == stored["outbox_id"])
            .values(
                payload_json=payload_json,
                payload_hash=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                status=status,
                attempt_count=attempt_count,
                next_attempt_at_utc=next_attempt_at_utc,
            )
        )


def test_reingestion_preserves_pending_tracker_fields(engine: Engine) -> None:
    import hashlib

    service(engine).stage(run_id="run_1", records=[record()])
    tracker = {
        "application_status": "Interview",
        "interview_date": "2026-11-20",
        "notes": "Panel scheduled.",
    }
    seed_pending_tracker_payload(engine, tracker)

    service(engine).stage(
        run_id="run_2",
        records=[record(description="<p>Changed description.</p>")],
    )

    stored = outbox_for(engine, "opportunity")[0]
    payload = json.loads(str(stored["payload_json"]))
    assert payload["description"] == "<p>Changed description.</p>"
    assert {header: payload[header] for header in tracker} == tracker
    assert set(payload["_pending_user_fields"]) == set(tracker)
    assert (
        stored["payload_hash"]
        == hashlib.sha256(str(stored["payload_json"]).encode("utf-8")).hexdigest()
    )
    assert stored["status"] == "pending"
    assert stored["attempt_count"] == 0


def test_reingestion_of_a_synced_row_drops_stale_tracker_values(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    seed_pending_tracker_payload(
        engine,
        {"application_status": "Interview", "notes": "Already written to Sheets."},
        status="synced",
    )

    service(engine).stage(
        run_id="run_2",
        records=[record(description="<p>Changed description.</p>")],
    )

    stored = outbox_for(engine, "opportunity")[0]
    payload = json.loads(str(stored["payload_json"]))
    assert "application_status" not in payload
    assert "notes" not in payload
    assert "_pending_user_fields" not in payload
    assert stored["status"] == "pending"


def test_unchanged_merged_payload_preserves_retry_state(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    tracker = {"application_status": "Assessment", "vacancy_url": "https://apply.example.test/1"}
    seed_pending_tracker_payload(
        engine,
        tracker,
        status="retry_wait",
        attempt_count=2,
        next_attempt_at_utc="2026-11-15T05:00:00.000000Z",
    )

    service(engine).stage(run_id="run_2", records=[record()])

    stored = outbox_for(engine, "opportunity")[0]
    payload = json.loads(str(stored["payload_json"]))
    assert {header: payload[header] for header in tracker} == tracker
    assert stored["status"] == "retry_wait"
    assert stored["attempt_count"] == 2
    assert stored["next_attempt_at_utc"] == "2026-11-15T05:00:00.000000Z"


def test_a_tracker_patch_keeps_the_existing_discovery_payload(engine: Engine) -> None:
    import hashlib

    from personal_job_finder_api.repositories.opportunities import (
        OpportunityStagingRepository,
    )

    service(engine).stage(run_id="run_1", records=[record()])
    stored = outbox_for(engine, "opportunity")[0]
    patch = {
        "opportunity_id": stored["entity_id"],
        "notes": "Referred by a former colleague.",
        "_pending_user_fields": ["notes"],
    }
    patch_json = json.dumps(patch, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    with engine.begin() as connection:
        queued = OpportunityStagingRepository().upsert_outbox(
            connection,
            outbox_id=str(stored["outbox_id"]),
            run_id=None,
            entity_id=str(stored["entity_id"]),
            payload_json=patch_json,
            payload_hash=hashlib.sha256(patch_json.encode("utf-8")).hexdigest(),
            now_utc="2026-11-15T06:00:00.000000Z",
        )

    merged = outbox_for(engine, "opportunity")[0]
    payload = json.loads(str(merged["payload_json"]))
    assert queued is True
    assert payload["company_name"] == "Acme Example"
    assert payload["job_title"] == "Backend Engineer"
    assert payload["notes"] == "Referred by a former colleague."
    assert payload["_pending_user_fields"] == ["notes"]


def test_a_reingest_of_a_blocked_row_still_preserves_pending_tracker_fields(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    tracker = {"application_status": "Offer", "notes": "Offer received."}
    seed_pending_tracker_payload(engine, tracker, status="blocked", attempt_count=5)

    service(engine).stage(
        run_id="run_2",
        records=[record(description="<p>Changed description.</p>")],
    )

    stored = outbox_for(engine, "opportunity")[0]
    payload = json.loads(str(stored["payload_json"]))
    assert {header: payload[header] for header in tracker} == tracker
    assert payload["description"] == "<p>Changed description.</p>"
    # New content supersedes the blocked attempt, so the row is claimable again.
    assert stored["status"] == "pending"
    assert stored["attempt_count"] == 0
    assert stored["last_error_code"] is None


def test_a_reingest_of_an_in_progress_row_supersedes_the_inflight_payload(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    tracker = {"application_status": "Interview"}
    seed_pending_tracker_payload(engine, tracker, status="in_progress", attempt_count=1)
    in_flight_hash = outbox_for(engine, "opportunity")[0]["payload_hash"]

    service(engine).stage(
        run_id="run_2",
        records=[record(description="<p>Changed description.</p>")],
    )

    stored = outbox_for(engine, "opportunity")[0]
    assert stored["status"] == "pending"
    assert stored["payload_hash"] != in_flight_hash
    # The in-flight worker's fenced update compares this hash, so it now matches
    # zero rows and cannot mark the newer payload as synced.
    payload = json.loads(str(stored["payload_json"]))
    assert payload["application_status"] == "Interview"
    assert payload["description"] == "<p>Changed description.</p>"


def test_a_pending_tracker_field_without_a_value_is_rejected(engine: Engine) -> None:
    import hashlib

    from personal_job_finder_api.repositories.opportunities import (
        OpportunityStagingRepository,
    )

    service(engine).stage(run_id="run_1", records=[record()])
    stored = outbox_for(engine, "opportunity")[0]
    patch_json = json.dumps(
        {
            "opportunity_id": stored["entity_id"],
            "_pending_user_fields": ["notes"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError), engine.begin() as connection:
        OpportunityStagingRepository().upsert_outbox(
            connection,
            outbox_id=str(stored["outbox_id"]),
            run_id=None,
            entity_id=str(stored["entity_id"]),
            payload_json=patch_json,
            payload_hash=hashlib.sha256(patch_json.encode("utf-8")).hexdigest(),
            now_utc="2026-11-15T06:00:00.000000Z",
        )

    # The stored payload is untouched, so the user's notes are not erased.
    assert json.loads(str(outbox_for(engine, "opportunity")[0]["payload_json"])) == json.loads(
        str(stored["payload_json"])
    )


def test_a_tracker_patch_must_declare_ownership_of_its_values(engine: Engine) -> None:
    import hashlib

    from personal_job_finder_api.repositories.opportunities import (
        OpportunityStagingRepository,
    )

    service(engine).stage(run_id="run_1", records=[record()])
    stored = outbox_for(engine, "opportunity")[0]
    # A user-owned value without ``_pending_user_fields`` would be silently
    # dropped, so the caller's mistake must surface instead.
    patch_json = json.dumps(
        {"opportunity_id": stored["entity_id"], "notes": "Lost without ownership."},
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError), engine.begin() as connection:
        OpportunityStagingRepository().upsert_outbox(
            connection,
            outbox_id=str(stored["outbox_id"]),
            run_id=None,
            entity_id=str(stored["entity_id"]),
            payload_json=patch_json,
            payload_hash=hashlib.sha256(patch_json.encode("utf-8")).hexdigest(),
            now_utc="2026-11-15T06:00:00.000000Z",
        )

    assert json.loads(str(outbox_for(engine, "opportunity")[0]["payload_json"])) == json.loads(
        str(stored["payload_json"])
    )


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

    assert len(pending) == 2
    assert {item.status for item in pending} == {"pending"}
    assert {item.entity_type for item in pending} == {"opportunity", "job_source"}
    opportunity = next(item for item in pending if item.entity_type == "opportunity")
    assert opportunity.entity_id.startswith("opp_")
    assert opportunity.run_id == "run_1"
    assert service(engine).list_outbox(status="synced") == []


def test_staging_reports_the_run_association(engine: Engine) -> None:
    summary = service(engine).stage(run_id="run_1", records=[record()])

    assert summary.run_id == "run_1"
    assert summary.feed_main == 1
    assert summary.feed_review == 0
    assert summary.feed_excluded == 0
    assert summary.outbox_enqueued == 2
    assert summary.pending_sync_count == 2


def test_pending_sync_count_drops_once_the_projection_is_synced(engine: Engine) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="synced"))

    summary = service(engine).stage(run_id="run_2", records=[record()])

    assert summary.outbox_enqueued == 0
    assert summary.pending_sync_count == 0


def test_record_that_becomes_local_only_withdraws_unsent_projections(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    assert count(engine, sync_outbox) == 2

    summary = service(engine).stage(
        run_id="run_2",
        records=[record(location="Singapore")],
    )

    assert summary.feed_excluded == 1
    assert summary.pending_sync_count == 0
    assert count(engine, sync_outbox) == 0


def test_withdrawn_projection_never_deletes_an_already_synced_row(
    engine: Engine,
) -> None:
    service(engine).stage(run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="synced"))

    service(engine).stage(run_id="run_2", records=[record(location="Singapore")])

    rows_after = outbox_for(engine, "opportunity")
    assert len(rows_after) == 1
    assert rows_after[0]["status"] == "pending"
    assert rows_after[0]["operation"] == "withdraw"
