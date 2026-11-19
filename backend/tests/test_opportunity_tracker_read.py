"""Read-side tracker resolution: effective values, precedence, and known fields.

These tests pin the contract the Opportunities UI depends on:

- a pending user-owned outbox edit wins over the Read Model;
- a field no local source knows is *unknown*, which is not the same as blank;
- a GET never writes, never settles the outbox, and never contacts Google.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event

from personal_job_finder_api.db.tables import (
    opportunities,
    opportunities_read_model,
    sync_outbox,
)
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    PENDING_USER_FIELDS_KEY,
)
from personal_job_finder_api.main import create_app
from personal_job_finder_api.repositories.read_model import ReadModelRepository
from personal_job_finder_api.search.staging import OpportunityStagingService

JAKARTA = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=JAKARTA)
ROLE = TargetRoleSpec(role_id="role_backend", canonical_name="Backend Engineer")
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


def _client(engine: Engine) -> TestClient:
    """A client whose Google transport can never be constructed."""

    def _forbidden_transport_factory() -> Any:
        raise AssertionError("A GET route must not build a Google Sheets transport.")

    app = create_app(
        engine=engine,
        clock=lambda: NOW,
        sync_transport_factory=_forbidden_transport_factory,
    )
    return TestClient(app, base_url="http://127.0.0.1:8000")


def make_record(
    *,
    external_id: str = "1",
    title: str = "Backend Engineer",
    description: str = "<p>Build services.</p>",
    location: str = "Jakarta, Indonesia",
) -> OpportunityRecord:
    return OpportunityRecord(
        company_id="company_acme",
        company_name="Acme Example",
        job_title=title,
        location=classify_location(location),
        description=description,
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type="greenhouse",
            source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, description, (ROLE,)),
    )


def stage(engine: Engine, records) -> None:
    OpportunityStagingService(engine, clock=lambda: NOW).stage(
        run_id="run_1",
        records=list(records),
    )


def all_opportunity_ids(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return sorted(
            str(value)
            for value in connection.execute(sa.select(opportunities.c.opportunity_id)).scalars()
        )


def _read_model_row(opportunity_id: str, **values: object) -> dict[str, object]:
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


def publish_read_models(engine: Engine, *rows: dict[str, object]) -> None:
    ReadModelRepository(engine).replace_snapshot(
        opportunities=list(rows),
        job_sources=[],
        refreshed_at=datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
    )


def only_opportunity_id(engine: Engine) -> str:
    with engine.connect() as connection:
        ids = list(connection.execute(sa.select(opportunities.c.opportunity_id)).scalars())
    assert len(ids) == 1, ids
    return str(ids[0])


def set_outbox(engine: Engine, opportunity_id: str, **values: object) -> None:
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().where(sync_outbox.c.entity_id == opportunity_id).values(**values)
        )


def outbox_payload(engine: Engine, opportunity_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        payload_json = connection.execute(
            sa.select(sync_outbox.c.payload_json).where(sync_outbox.c.entity_id == opportunity_id)
        ).scalar_one()
    return json.loads(payload_json)


def write_pending_payload(
    engine: Engine,
    opportunity_id: str,
    fields: dict[str, str],
    *,
    status: str = "pending",
) -> None:
    payload = {"opportunity_id": opportunity_id, **fields}
    payload[PENDING_USER_FIELDS_KEY] = sorted(fields)
    set_outbox(
        engine,
        opportunity_id,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        status=status,
    )


def detail(client: TestClient, opportunity_id: str):
    return client.get(f"/api/v1/jobs/{opportunity_id}")


def patch(client: TestClient, opportunity_id: str, body: dict[str, object]):
    return client.patch(
        f"/api/v1/opportunities/{opportunity_id}/tracker",
        headers={"Origin": LOCAL_ORIGIN},
        json=body,
    )


# ---------------------------------------------------------------- precedence


def test_tracker_values_come_from_the_read_model(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(
            opportunity_id,
            application_status="Applied",
            date_applied="2026-11-10",
            notes="Referred by a former colleague.",
        ),
    )

    with _client(engine) as client:
        response = detail(client, opportunity_id)

    assert response.status_code == 200
    body = response.json()
    assert body["application_status"] == "Applied"
    assert body["date_applied"] == "2026-11-10"
    assert body["notes"] == "Referred by a former colleague."
    assert body["sync_status"] == "pending"
    assert sorted(body["known_fields"]) == sorted(TRACKER_FIELDS)


def test_a_pending_outbox_field_overrides_the_read_model(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(
            opportunity_id,
            application_status="Applied",
            date_applied="2026-11-10",
        ),
    )

    with _client(engine) as client:
        assert patch(client, opportunity_id, {"application_status": "Interview"}).status_code == 200
        body = detail(client, opportunity_id).json()

    # The pending edit wins for its own field only.
    assert body["application_status"] == "Interview"
    assert body["date_applied"] == "2026-11-10"
    assert body["sync_status"] == "pending"


def test_a_read_never_writes_to_the_read_model(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )

    with _client(engine) as client:
        patch(client, opportunity_id, {"application_status": "Interview"})
        assert detail(client, opportunity_id).json()["application_status"] == "Interview"
        assert (
            client.get("/api/v1/jobs?feed=main").json()["items"][0]["tracker_summary"][
                "application_status"
            ]
            == "Interview"
        )

    # Reading reports the pending edit but publishes nothing.
    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(opportunities_read_model.c.application_status).where(
                opportunities_read_model.c.opportunity_id == opportunity_id
            )
        ).scalar_one()
    assert stored == "Applied"


def test_a_pending_blank_field_clears_a_read_model_value(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )

    with _client(engine) as client:
        patch(client, opportunity_id, {"application_status": ""})
        body = detail(client, opportunity_id).json()

    assert body["application_status"] is None
    # Cleared, not unknown: the field is still known.
    assert "application_status" in body["known_fields"]


def test_an_unpublished_record_reports_no_known_tracker_fields(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)

    with _client(engine) as client:
        body = detail(client, opportunity_id).json()

    assert body["known_fields"] == []
    assert all(body[field] is None for field in TRACKER_FIELDS)
    # An outbox row exists for the unsynced discovery record, so it is queued.
    assert body["sync_status"] == "pending"


def test_known_blank_and_unknown_are_distinguishable(engine: Engine) -> None:
    stage(engine, [make_record(external_id="1"), make_record(external_id="2")])
    ids = all_opportunity_ids(engine)
    assert len(ids) == 2
    canonical, unpublished = ids

    # A canonical row with a blank cell: known, and blank.
    publish_read_models(engine, _read_model_row(canonical, application_status=None))

    with _client(engine) as client:
        known = detail(client, canonical).json()
        unknown = detail(client, unpublished).json()

    assert known["application_status"] is None
    assert sorted(known["known_fields"]) == sorted(TRACKER_FIELDS)
    assert unknown["application_status"] is None
    assert unknown["known_fields"] == []


def test_a_synced_outbox_row_does_not_override_the_read_model(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )
    # A stale pending list left behind on a synced row must not win: the
    # canonical mirror already contains the settled value.
    write_pending_payload(
        engine,
        opportunity_id,
        {"application_status": "Rejected"},
        status="synced",
    )

    with _client(engine) as client:
        body = detail(client, opportunity_id).json()

    assert body["application_status"] == "Applied"
    assert body["sync_status"] == "synced"


# ---------------------------------------------------------------- malformed


def test_a_corrupt_outbox_payload_does_not_break_the_read(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )
    set_outbox(engine, opportunity_id, payload_json="{not json", status="pending")

    with _client(engine) as client:
        response = detail(client, opportunity_id)

    assert response.status_code == 200
    assert response.json()["application_status"] == "Applied"


def test_a_non_object_outbox_payload_is_ignored(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )
    set_outbox(engine, opportunity_id, payload_json='["not", "a", "mapping"]')

    with _client(engine) as client:
        body = detail(client, opportunity_id).json()

    assert body["application_status"] == "Applied"


def test_an_ownership_list_cannot_smuggle_in_a_non_tracker_field(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )
    payload = {
        "opportunity_id": opportunity_id,
        "user_status": "ignored",
        "application_status": "Interview",
        PENDING_USER_FIELDS_KEY: ["user_status", "not_a_column", "application_status"],
    }
    set_outbox(engine, opportunity_id, payload_json=json.dumps(payload))

    with _client(engine) as client:
        body = detail(client, opportunity_id).json()

    assert body["application_status"] == "Interview"
    # The unknown and non-tracker names are dropped, not exposed.
    assert "user_status" not in body["known_fields"]
    assert "not_a_column" not in body["known_fields"]


def test_a_payload_without_an_ownership_list_overlays_nothing(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, application_status="Applied"),
    )
    # A full discovery payload carries tracker columns as an ingestion copy.
    payload = {"opportunity_id": opportunity_id, "application_status": "Interview"}
    set_outbox(engine, opportunity_id, payload_json=json.dumps(payload))

    with _client(engine) as client:
        body = detail(client, opportunity_id).json()

    assert body["application_status"] == "Applied"


# ---------------------------------------------------------------- feed


def test_feed_items_carry_a_tracker_summary_per_page(engine: Engine) -> None:
    stage(
        engine,
        [
            make_record(external_id="1"),
            make_record(external_id="2"),
            make_record(external_id="3"),
        ],
    )
    ids = all_opportunity_ids(engine)
    assert len(ids) == 3
    publish_read_models(
        engine,
        _read_model_row(ids[0], application_status="Applied", date_applied="2026-11-10"),
        _read_model_row(ids[1], application_status="Interview"),
    )

    with _client(engine) as client:
        response = client.get("/api/v1/jobs?feed=main&page=1&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert body["total_items"] == 3
    assert len(body["items"]) == 2
    for item in body["items"]:
        summary = item["tracker_summary"]
        assert set(summary) == {
            "application_status",
            "date_applied",
            "follow_up_date",
            "interview_date",
            "sync_status",
            "known_fields",
        }
        # The list response stays compact.
        assert "notes" not in summary
        assert "description" not in item

    by_id = {item["opportunity_id"]: item["tracker_summary"] for item in body["items"]}
    assert by_id[ids[0]]["application_status"] == "Applied"
    assert by_id[ids[0]]["date_applied"] == "2026-11-10"
    assert by_id[ids[1]]["application_status"] == "Interview"


def test_the_feed_page_resolves_trackers_in_a_bounded_number_of_queries(
    engine: Engine,
) -> None:
    stage(engine, [make_record(external_id=str(index)) for index in range(5)])

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    def tracker_queries(page_size: int) -> int:
        statements.clear()
        with _client(engine) as client:
            response = client.get(f"/api/v1/jobs?feed=main&page=1&page_size={page_size}")
        assert response.status_code == 200
        return sum(
            1
            for statement in statements
            if "opportunities_read_model" in statement or "sync_outbox" in statement
        )

    event.listen(engine, "before_cursor_execute", _record)
    try:
        one_item = tracker_queries(1)
        five_items = tracker_queries(5)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    # One Read Model lookup plus one outbox lookup for the whole page.
    assert one_item == 2
    assert five_items == 2


def test_feed_pagination_and_bucket_semantics_are_unchanged(engine: Engine) -> None:
    stage(
        engine,
        [
            make_record(external_id="1"),
            make_record(external_id="2", location="Remote"),
        ],
    )

    with _client(engine) as client:
        main = client.get("/api/v1/jobs?feed=main&page=1&page_size=1").json()
        review = client.get("/api/v1/jobs?feed=review&page=1&page_size=25").json()

    assert main["page"] == 1
    assert main["page_size"] == 1
    assert main["total_items"] == 1
    assert main["total_pages"] == 1
    assert review["total_items"] == 1
    assert main["items"][0]["opportunity_id"] != review["items"][0]["opportunity_id"]


# ---------------------------------------------------------------- detail


def test_detail_returns_the_full_description(engine: Engine) -> None:
    long_description = "<p>" + ("Backend platform work. " * 60) + "</p>"
    assert len(long_description) > 500
    stage(engine, [make_record(description=long_description)])
    opportunity_id = only_opportunity_id(engine)

    with _client(engine) as client:
        feed = client.get("/api/v1/jobs?feed=main").json()["items"][0]
        body = detail(client, opportunity_id).json()

    assert feed["has_more_description"] is True
    assert len(feed["description_excerpt"]) == 500
    assert body["description"] == long_description
    assert body["sources"][0]["source_url"] == ("https://boards.greenhouse.io/acme/jobs/1")


def test_detail_of_an_unknown_opportunity_returns_a_stable_404(engine: Engine) -> None:
    with _client(engine) as client:
        response = detail(client, "opp_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "opportunity_not_found"
    assert response.headers["cache-control"] == "no-store"


def test_detail_is_not_cached_and_needs_no_google_transport(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)

    with _client(engine) as client:
        response = detail(client, opportunity_id)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_detail_rejects_an_untrusted_host(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)

    with _client(engine) as client:
        response = client.get(
            f"/api/v1/jobs/{opportunity_id}",
            headers={"Host": "evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"


# ---------------------------------------------------------------- patch


def test_patch_reports_known_fields_and_still_queues_the_edit(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)

    with _client(engine) as client:
        response = patch(client, opportunity_id, {"job_type": "Internship"})

    assert response.status_code == 200
    body = response.json()
    assert body["job_type"] == "Internship"
    assert body["known_fields"] == ["job_type"]
    assert body["sync_status"] == "pending"
    for field in TRACKER_FIELDS:
        assert field in body

    # The edit is queued, not published: no Read Model row was created.
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(opportunities_read_model)
            ).scalar_one()
            == 0
        )
    payload = outbox_payload(engine, opportunity_id)
    assert payload[PENDING_USER_FIELDS_KEY] == ["job_type"]


def test_patch_does_not_require_all_tracker_fields(engine: Engine) -> None:
    stage(engine, [make_record()])
    opportunity_id = only_opportunity_id(engine)
    publish_read_models(
        engine,
        _read_model_row(opportunity_id, date_applied="2026-11-10"),
    )

    with _client(engine) as client:
        response = patch(client, opportunity_id, {"application_status": "Applied"})
        body = detail(client, opportunity_id).json()

    assert response.status_code == 200
    assert body["date_applied"] == "2026-11-10"
    assert body["application_status"] == "Applied"
