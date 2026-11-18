"""API tests for the local opportunity feed and review queue."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import opportunities
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.main import create_app
from personal_job_finder_api.search.staging import OpportunityStagingService

JAKARTA = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=JAKARTA)
ROLE = TargetRoleSpec(role_id="role_backend", canonical_name="Backend Engineer")


def _client(engine: Engine) -> TestClient:
    app = create_app(engine=engine, clock=lambda: NOW)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def make_record(
    *,
    external_id: str = "1",
    company_id: str = "company_acme",
    company_name: str = "Acme Example",
    title: str = "Backend Engineer",
    location: str = "Jakarta, Indonesia",
    description: str = "<p>Build services.</p>",
    source_url: str | None = None,
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
            source_type="greenhouse",
            source_url=source_url or f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, description, (ROLE,)),
    )


def stage(engine: Engine, records, run_id: str = "run_1") -> None:
    OpportunityStagingService(engine, clock=lambda: NOW).stage(
        run_id=run_id,
        records=list(records),
    )


def test_main_feed_excludes_review_and_excluded_records(engine: Engine) -> None:
    stage(
        engine,
        [
            make_record(external_id="1", location="Jakarta"),
            make_record(external_id="2", location="Remote"),
            make_record(external_id="3", location="Singapore"),
            make_record(external_id="4", title="Accountant", location="Jakarta"),
        ],
    )

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_items"] == 1
    assert [item["company_name"] for item in payload["items"]] == ["Acme Example"]
    assert payload["items"][0]["feed_bucket"] == "main"
    assert response.headers["cache-control"] == "no-store"


def test_review_queue_returns_only_review_records(engine: Engine) -> None:
    stage(
        engine,
        [
            make_record(external_id="1", location="Jakarta"),
            make_record(external_id="2", location="Remote"),
            make_record(external_id="3", location="Singapore"),
        ],
    )

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=review")

    payload = response.json()
    assert payload["total_items"] == 1
    assert payload["items"][0]["location_raw"] == "Remote"
    assert payload["items"][0]["feed_bucket"] == "review"


def test_opportunity_item_exposes_provenance_and_run_association(
    engine: Engine,
) -> None:
    stage(engine, [make_record(external_id="1001")], run_id="run_1")
    stage(engine, [make_record(external_id="1001")], run_id="run_2")

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main")

    item = response.json()["items"][0]
    assert item["opportunity_id"].startswith("opp_")
    assert item["job_title"] == "Backend Engineer"
    assert item["target_role_id"] == "role_backend"
    assert item["role_match_type"] == "canonical_title"
    assert item["location_status"] == "eligible"
    assert item["work_mode"] == "onsite"
    assert len(item["sources"]) == 1
    source = item["sources"][0]
    assert source["source_type"] == "greenhouse"
    assert source["external_source_id"] == "1001"
    assert source["source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert source["source_url_is_safe"] is True
    assert source["run_ids"] == ["run_1", "run_2"]


def test_unsafe_source_url_is_not_exposed_as_a_link(engine: Engine) -> None:
    stage(
        engine,
        [make_record(external_id="1", source_url="javascript:alert(1)")],
    )

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main")

    source = response.json()["items"][0]["sources"][0]
    assert source["source_url"] is None
    assert source["source_url_is_safe"] is False


def test_description_is_bounded_in_the_list_response(engine: Engine) -> None:
    stage(engine, [make_record(description="x" * 5_000)])

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main")

    item = response.json()["items"][0]
    assert len(item["description_excerpt"]) <= 500
    assert item["has_more_description"] is True


def test_pagination_reports_stable_totals(engine: Engine) -> None:
    stage(
        engine,
        [make_record(external_id=str(index)) for index in range(1, 8)],
    )

    with _client(engine) as client:
        first = client.get("/api/v1/jobs?feed=main&page=1&page_size=3")
        second = client.get("/api/v1/jobs?feed=main&page=2&page_size=3")
        third = client.get("/api/v1/jobs?feed=main&page=3&page_size=3")

    first_payload = first.json()
    assert first_payload["page"] == 1
    assert first_payload["page_size"] == 3
    assert first_payload["total_items"] == 7
    assert first_payload["total_pages"] == 3
    assert len(first_payload["items"]) == 3
    assert len(second.json()["items"]) == 3
    assert len(third.json()["items"]) == 1

    first_ids = [item["opportunity_id"] for item in first_payload["items"]]
    second_ids = [item["opportunity_id"] for item in second.json()["items"]]
    assert set(first_ids).isdisjoint(second_ids)


def test_page_beyond_the_last_page_returns_an_empty_list(engine: Engine) -> None:
    stage(engine, [make_record()])

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main&page=5")

    payload = response.json()
    assert payload["items"] == []
    assert payload["total_items"] == 1


def test_empty_database_returns_an_empty_feed(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "page": 1,
        "page_size": 25,
        "total_items": 0,
        "total_pages": 0,
    }


def test_invalid_feed_value_is_rejected(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=everything")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_page_size_above_the_limit_is_rejected(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main&page_size=101")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_response_never_leaks_configuration_or_board_tokens(engine: Engine) -> None:
    stage(engine, [make_record()])

    with _client(engine) as client:
        body = client.get("/api/v1/jobs?feed=main").text.casefold()

    for forbidden in (
        "config_snapshot",
        "board_token",
        "ats_board_token",
        "client_secret",
        "access_token",
        "refresh_token",
    ):
        assert forbidden not in body


def test_feed_rejects_untrusted_host(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get(
            "/api/v1/jobs?feed=main",
            headers={"Host": "evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"


def test_feed_ordering_is_first_seen_first_and_stable_across_runs(
    engine: Engine,
) -> None:
    stage(engine, [make_record(external_id="older")], run_id="run_1")
    stage(engine, [make_record(external_id="newer")], run_id="run_2")
    # Re-observing the older record must not move it to the top of the feed.
    stage(engine, [make_record(external_id="older")], run_id="run_3")

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main")

    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["sources"][0]["external_source_id"] == "newer"
    assert items[1]["sources"][0]["external_source_id"] == "older"
    # The re-seen record keeps its first-seen position but records the new run.
    assert items[1]["sources"][0]["run_ids"] == ["run_1", "run_3"]


def test_row_count_matches_the_table(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1"), make_record(external_id="2")])

    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(sa.func.count()).select_from(opportunities)
        ).scalar_one()

    with _client(engine) as client:
        total = client.get("/api/v1/jobs?feed=main").json()["total_items"]

    assert stored == total == 2


# ---------------------------------------------------------------------------
# Application tracker (M4C3A)
# ---------------------------------------------------------------------------

LOCAL_ORIGIN = "http://127.0.0.1:5173"
TRACKER_FIELDS = (
    "job_type",
    "date_applied",
    "application_status",
    "follow_up_date",
    "interview_date",
    "vacancy_url",
    "notes",
)


def _patch(
    client: TestClient,
    opportunity_id: str,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
):
    return client.patch(
        f"/api/v1/opportunities/{opportunity_id}/tracker",
        headers=headers or {"Origin": LOCAL_ORIGIN},
        json=body,
    )


def _staged_id(engine: Engine, external_id: str = "1") -> str:
    with engine.connect() as connection:
        value = connection.execute(
            sa.select(opportunities.c.opportunity_id).where(
                opportunities.c.opportunity_id.like("opp_%")
            )
        ).scalars()
        ids = list(value)
    assert len(ids) == 1, ids
    return ids[0]


def _outbox_row(engine: Engine, opportunity_id: str) -> dict[str, object] | None:
    from personal_job_finder_api.db.tables import sync_outbox

    with engine.connect() as connection:
        row = (
            connection.execute(
                sync_outbox.select().where(sync_outbox.c.entity_id == opportunity_id)
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row is not None else None


def test_patch_updates_one_tracker_field_and_queues_sync(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"job_type": "Internship"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["opportunity_id"] == opportunity_id
    assert body["job_type"] == "Internship"
    assert body["sync_status"] == "pending"
    for field in TRACKER_FIELDS:
        assert field in body

    stored = _outbox_row(engine, opportunity_id)
    assert stored is not None
    assert stored["status"] == "pending"


def test_omitted_fields_are_not_changed(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"job_type": "Contract", "notes": "First call"})
        response = _patch(client, opportunity_id, {"application_status": "Applied"})

    body = response.json()
    assert body["job_type"] == "Contract"
    assert body["notes"] == "First call"
    assert body["application_status"] == "Applied"


def test_an_explicit_empty_string_clears_a_field(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"notes": "Temporary"})
        response = _patch(client, opportunity_id, {"notes": ""})

    assert response.json()["notes"] is None


def test_an_explicit_null_clears_a_field(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"date_applied": "2026-11-01"})
        response = _patch(client, opportunity_id, {"date_applied": None})

    assert response.json()["date_applied"] is None


def test_an_empty_patch_is_rejected(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.headers["cache-control"] == "no-store"


def test_an_unknown_field_is_rejected(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"salary": "1000"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_user_status_is_not_a_tracker_field(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"user_status": "saved"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_discovery_owned_fields_are_rejected(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"job_title_original": "Overridden"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_notes_accepts_ten_thousand_characters(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"notes": "x" * 10_000})

    assert response.status_code == 200
    assert response.json()["notes"] == "x" * 10_000


def test_notes_longer_than_ten_thousand_characters_is_rejected(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"notes": "x" * 10_001})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_opportunity_returns_404(engine: Engine) -> None:
    with _client(engine) as client:
        response = _patch(client, "opp_missing", {"job_type": "Internship"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "opportunity_not_found"
    assert response.headers["cache-control"] == "no-store"


def _read_model_row(opportunity_id: str, **values: object) -> dict[str, object]:
    from personal_job_finder_api.db.tables import opportunities_read_model

    row: dict[str, object] = dict.fromkeys(opportunities_read_model.c.keys())
    row.update(
        {
            "opportunity_id": opportunity_id,
            "company_name": "Acme Example",
            "job_title_original": "Backend Engineer",
            "job_title_normalized": "backend engineer",
            "target_role_id": "role_backend",
            "role_match_type": "canonical_title",
            "location_status": "eligible",
            "work_mode": "onsite",
            "remote_scope": "not_applicable",
            "availability_status": "unknown",
            "first_seen_at_utc": "2026-11-15T03:00:00.000000Z",
            "last_seen_at_utc": "2026-11-15T03:00:00.000000Z",
            "created_at_utc": "2026-11-15T03:00:00.000000Z",
            "updated_at_utc": "2026-11-15T03:00:00.000000Z",
        }
    )
    row.update(values)
    return row


def _publish_read_model(engine: Engine, **overrides: object) -> None:
    from personal_job_finder_api.repositories.read_model import ReadModelRepository

    ReadModelRepository(engine).replace_snapshot(
        opportunities=[_read_model_row(**overrides)],
        job_sources=[],
        refreshed_at=datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
    )


def _set_outbox(engine: Engine, opportunity_id: str, **values: object) -> None:
    from personal_job_finder_api.db.tables import sync_outbox

    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().where(sync_outbox.c.entity_id == opportunity_id).values(**values)
        )


def test_a_canonical_read_model_opportunity_can_be_updated(engine: Engine) -> None:
    _publish_read_model(engine, opportunity_id="opp_canonical")

    with _client(engine) as client:
        response = _patch(client, "opp_canonical", {"application_status": "Applied"})

    assert response.status_code == 200
    assert response.json()["sync_status"] == "pending"
    stored = _outbox_row(engine, "opp_canonical")
    assert stored is not None
    assert stored["status"] == "pending"
    # A tracker-only payload never invents discovery values.
    payload = json.loads(str(stored["payload_json"]))
    assert "company_name" not in payload


def test_a_staged_unsynced_opportunity_keeps_its_discovery_payload(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"notes": "Call back Friday"})

    stored = _outbox_row(engine, opportunity_id)
    assert stored is not None
    payload = json.loads(str(stored["payload_json"]))
    assert payload["job_title"] == "Backend Engineer"
    assert payload["company_name"] == "Acme Example"
    assert payload["notes"] == "Call back Friday"


def test_pending_tracker_edits_survive_a_second_patch(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"job_type": "Internship"})
        _patch(client, opportunity_id, {"application_status": "Applied"})

    payload = json.loads(str(_outbox_row(engine, opportunity_id)["payload_json"]))
    assert payload["job_type"] == "Internship"
    assert payload["application_status"] == "Applied"
    assert sorted(payload["_pending_user_fields"]) == ["application_status", "job_type"]


def test_the_outbox_identity_stays_stable_across_patches(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    before = str(_outbox_row(engine, opportunity_id)["outbox_id"])

    with _client(engine) as client:
        _patch(client, opportunity_id, {"job_type": "Contract"})
        _patch(client, opportunity_id, {"notes": "Second"})

    after = str(_outbox_row(engine, opportunity_id)["outbox_id"])
    assert after == before
    with engine.connect() as connection:
        from personal_job_finder_api.db.tables import sync_outbox

        count = connection.execute(
            sa.select(sa.func.count())
            .select_from(sync_outbox)
            .where(sync_outbox.c.entity_type == "opportunity")
        ).scalar_one()
    assert count == 1


def test_a_synced_outbox_becomes_pending_after_a_changed_patch(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    _set_outbox(
        engine,
        opportunity_id,
        status="synced",
        synced_at_utc="2026-11-15T04:00:00.000000Z",
        attempt_count=2,
    )

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"application_status": "Interview"})

    assert response.status_code == 200
    assert response.json()["sync_status"] == "pending"
    stored = _outbox_row(engine, opportunity_id)
    assert stored["status"] == "pending"
    assert stored["synced_at_utc"] is None
    payload = json.loads(str(stored["payload_json"]))
    # No stale tracker values are carried forward from the previous payload.
    assert payload["application_status"] == "Interview"
    assert "_pending_user_fields" in payload


def test_an_identical_patch_does_not_create_duplicate_work(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"notes": "Same"})
        first = _outbox_row(engine, opportunity_id)
        second_response = _patch(client, opportunity_id, {"notes": "Same"})

    assert second_response.status_code == 200
    assert second_response.json()["notes"] == "Same"
    second = _outbox_row(engine, opportunity_id)
    assert second["payload_hash"] == first["payload_hash"]
    assert second["updated_at_utc"] == first["updated_at_utc"]


def test_an_identical_patch_does_not_reset_a_retry_schedule(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"notes": "Waiting"})

    _set_outbox(
        engine,
        opportunity_id,
        status="retry_wait",
        attempt_count=3,
        next_attempt_at_utc="2026-11-15T11:00:00.000000Z",
        last_error_code="server_error",
    )

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"notes": "Waiting"})

    assert response.status_code == 200
    stored = _outbox_row(engine, opportunity_id)
    assert stored["status"] == "retry_wait"
    assert stored["attempt_count"] == 3
    assert stored["next_attempt_at_utc"] == "2026-11-15T11:00:00.000000Z"


def test_discovery_re_ingestion_cannot_overwrite_a_pending_tracker_edit(
    engine: Engine,
) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        _patch(client, opportunity_id, {"application_status": "Applied"})

    stage(engine, [make_record(external_id="1", title="Senior Backend Engineer")], run_id="run_2")

    payload = json.loads(str(_outbox_row(engine, opportunity_id)["payload_json"]))
    assert payload["application_status"] == "Applied"
    assert payload["job_title"] == "Senior Backend Engineer"


def test_a_failed_transaction_leaves_no_partial_change(
    engine: Engine,
    monkeypatch,
) -> None:
    from personal_job_finder_api.repositories.opportunities import (
        OpportunityStagingRepository,
    )
    from personal_job_finder_api.search.tracker import OpportunityTrackerService

    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    before = _outbox_row(engine, opportunity_id)

    original = OpportunityStagingRepository.find_outbox
    calls = {"count": 0}

    def flaky(self, connection, **kwargs):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise RuntimeError("simulated failure after the write")
        return original(self, connection, **kwargs)

    monkeypatch.setattr(OpportunityStagingRepository, "find_outbox", flaky)

    with pytest.raises(RuntimeError):
        OpportunityTrackerService(engine, clock=lambda: NOW).update(
            opportunity_id,
            {"notes": "Should roll back"},
        )

    after = _outbox_row(engine, opportunity_id)
    assert after["payload_hash"] == before["payload_hash"]
    assert after["status"] == before["status"]


def test_a_staged_record_without_a_canonical_row_is_refused(engine: Engine) -> None:
    from personal_job_finder_api.db.tables import sync_outbox

    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    with engine.begin() as connection:
        connection.execute(sync_outbox.delete().where(sync_outbox.c.entity_id == opportunity_id))

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"notes": "Unsafe"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tracker_state_inconsistent"
    assert _outbox_row(engine, opportunity_id) is None


def test_a_patch_never_calls_google(engine: Engine) -> None:
    class RecordingTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_spreadsheet(self, spreadsheet_id: str) -> dict:
            self.calls.append("get_spreadsheet")
            return {"sheets": []}

        def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict:
            self.calls.append("batch_get_values")
            return {"valueRanges": []}

        def batch_update_values(self, spreadsheet_id, data, *, value_input_option):
            self.calls.append("batch_update_values")
            return {}

        def batch_update_spreadsheet(self, spreadsheet_id, requests):
            self.calls.append("batch_update_spreadsheet")
            return {}

    transport = RecordingTransport()
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    app = create_app(engine=engine, clock=lambda: NOW, sync_transport=transport)

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = _patch(client, opportunity_id, {"job_type": "Internship"})

    assert response.status_code == 200
    assert transport.calls == []


def test_a_patch_does_not_mark_the_read_model_as_synced(engine: Engine) -> None:
    _publish_read_model(engine, opportunity_id="opp_canonical")

    with _client(engine) as client:
        _patch(client, "opp_canonical", {"application_status": "Applied"})

    with engine.connect() as connection:
        from personal_job_finder_api.db.tables import opportunities_read_model

        stored = connection.execute(
            sa.select(opportunities_read_model.c.application_status)
        ).scalar_one()
    # Settlement by the dispatcher is the only path that publishes tracker values.
    assert stored is None


def test_tracker_patch_enforces_host_and_origin(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        bad_host = client.patch(
            f"/api/v1/opportunities/{opportunity_id}/tracker",
            headers={"Origin": LOCAL_ORIGIN, "Host": "evil.test"},
            json={"job_type": "Internship"},
        )
        bad_origin = client.patch(
            f"/api/v1/opportunities/{opportunity_id}/tracker",
            headers={"Origin": "https://evil.test"},
            json={"job_type": "Internship"},
        )

    assert bad_host.status_code == 403
    assert bad_origin.status_code == 403


def test_free_text_tracker_values_are_accepted_verbatim(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)

    with _client(engine) as client:
        response = _patch(
            client,
            opportunity_id,
            {
                "job_type": "Development Program",
                "application_status": "Menunggu jadwal interview",
            },
        )

    body = response.json()
    assert body["job_type"] == "Development Program"
    assert body["application_status"] == "Menunggu jadwal interview"


def test_raw_date_text_is_preserved(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    raw = "01/11/2026 (pagi)"

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"date_applied": raw})

    assert response.json()["date_applied"] == raw
    payload = json.loads(str(_outbox_row(engine, opportunity_id)["payload_json"]))
    assert payload["date_applied"] == raw


def test_vacancy_url_stays_separate_from_the_source_url(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    vacancy_url = "https://careers.example.test/apply/1234"

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"vacancy_url": vacancy_url})

    assert response.json()["vacancy_url"] == vacancy_url
    payload = json.loads(str(_outbox_row(engine, opportunity_id)["payload_json"]))
    assert payload["vacancy_url"] == vacancy_url
    # The discovery-owned provenance URL is untouched.
    assert payload["source_url"] == "https://boards.greenhouse.io/acme/jobs/1"


def test_a_refused_patch_is_not_cached(engine: Engine) -> None:
    from personal_job_finder_api.db.tables import sync_outbox

    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    with engine.begin() as connection:
        connection.execute(sync_outbox.delete().where(sync_outbox.c.entity_id == opportunity_id))

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"notes": "Unsafe"})

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"


def test_clearing_a_synced_field_queues_work_when_the_canonical_value_is_unknown(
    engine: Engine,
) -> None:
    """A synced row without a cached canonical copy must not be treated as no-op.

    The sheet may already hold a tracker value this machine has not read. Skipping
    the patch would silently keep that value, so an unknown field is always queued.
    """

    stage(engine, [make_record(external_id="1")])
    opportunity_id = _staged_id(engine)
    _set_outbox(engine, opportunity_id, status="synced", attempt_count=1)

    with _client(engine) as client:
        response = _patch(client, opportunity_id, {"notes": ""})

    assert response.status_code == 200
    assert response.json()["sync_status"] == "pending"
    stored = _outbox_row(engine, opportunity_id)
    assert stored["status"] == "pending"
    payload = json.loads(str(stored["payload_json"]))
    assert payload["notes"] == ""
    assert payload["_pending_user_fields"] == ["notes"]
