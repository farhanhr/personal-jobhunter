"""API tests for the local opportunity feed and review queue."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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
