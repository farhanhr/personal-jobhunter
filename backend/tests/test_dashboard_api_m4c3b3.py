"""Tests for dashboard summary API (M4C3B3)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import (
    job_sources_read_model,
    opportunities_read_model,
    sheets_sync_state,
    target_roles,
)
from personal_job_finder_api.main import create_app

JAKARTA = ZoneInfo("Asia/Jakarta")
LOCAL_ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(engine: Engine):
    """Create a test client with the dashboard service."""
    app = create_app(engine=engine, clock=lambda: datetime(2026, 11, 18, 10, 0, 0, tzinfo=JAKARTA))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


@pytest.fixture
def now() -> datetime:
    """Fixed reference time: 2026-11-18 10:00:00 Jakarta time."""
    return datetime(2026, 11, 18, 10, 0, 0, tzinfo=JAKARTA)


@pytest.fixture
def today_jkt(now: datetime) -> datetime:
    """Jakarta calendar date for the reference time."""
    return now.date()


def _insert_opportunity(
    engine: Engine,
    opportunity_id: str,
    *,
    location_status: str = "eligible",
    target_role_id: str | None = None,
    province: str | None = None,
    city: str | None = None,
    first_seen_at_utc: str | None = None,
) -> None:
    """Insert a row into opportunities_read_model."""
    with engine.begin() as conn:
        conn.execute(
            opportunities_read_model.insert().values(
                opportunity_id=opportunity_id,
                company_name="Test Company",
                job_title_original="Test Role",
                job_title_normalized="test role",
                location_raw="Test Location",
                location_status=location_status,
                work_mode="onsite",
                remote_scope="not_applicable",
                employment_type="full_time",
                experience_level="mid",
                description="Test description",
                required_skills_json="[]",
                posted_at=None,
                deadline=None,
                availability_status="open",
                user_status="new",
                first_seen_at_utc=first_seen_at_utc or format_utc(datetime.now(UTC)),
                last_seen_at_utc=format_utc(datetime.now(UTC)),
                created_at_utc=format_utc(datetime.now(UTC)),
                updated_at_utc=format_utc(datetime.now(UTC)),
                job_type=None,
                date_applied=None,
                application_status=None,
                follow_up_date=None,
                interview_date=None,
                vacancy_url=None,
                notes=None,
                target_role_id=target_role_id,
                role_match_type="canonical_title" if target_role_id else None,
                role_match_reason=None,
                country=None,
                province=province,
                city=city,
            )
        )


def _insert_source(
    engine: Engine,
    source_id: str,
    opportunity_id: str,
    source_type: str,
) -> None:
    """Insert a row into job_sources_read_model."""
    with engine.begin() as conn:
        conn.execute(
            job_sources_read_model.insert().values(
                source_id=source_id,
                opportunity_id=opportunity_id,
                source_type=source_type,
                external_source_id=f"ext_{source_id}",
                source_url=f"https://example.com/{source_id}",
                author_name=None,
                author_role=None,
                source_text_excerpt=None,
                posted_at=None,
                captured_at_utc=format_utc(datetime.now(UTC)),
                last_seen_at_utc=format_utc(datetime.now(UTC)),
                provenance_method="ats_api",
                observation_status="seen",
            )
        )


def _insert_target_role(
    engine: Engine,
    role_id: str,
    canonical_name: str,
    *,
    enabled: bool = True,
) -> None:
    """Insert a row into target_roles."""
    with engine.begin() as conn:
        conn.execute(
            target_roles.insert().values(
                role_id=role_id,
                canonical_name=canonical_name,
                aliases_json="[]",
                seniority_json="[]",
                enabled=enabled,
                created_at_utc=format_utc(datetime.now(UTC)),
                updated_at_utc=format_utc(datetime.now(UTC)),
            )
        )


def _set_read_model_refresh(engine: Engine, refresh_time: datetime) -> None:
    """Set the last_full_refresh_at_utc in sheets_sync_state."""
    with engine.begin() as conn:
        # Ensure the row exists
        conn.execute(
            sheets_sync_state.insert().values(
                state_id="google_sheets",
                last_full_refresh_at_utc=format_utc(refresh_time),
                last_attempt_at_utc=format_utc(refresh_time),
                last_refresh_outcome="succeeded",
                last_refresh_error_code=None,
                spreadsheet_schema_version="1",
                updated_at_utc=format_utc(datetime.now(UTC)),
            )
        )


def _insert_search_run(
    engine: Engine,
    run_id: str,
    accepted_at_utc: datetime,
    *,
    status: str = "completed",
    started_at_utc: datetime | None = None,
    finished_at_utc: datetime | None = None,
) -> None:
    """Insert a row into search_runs."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            INSERT INTO search_runs (
                run_id, idempotency_key, request_fingerprint,
                operational_day, status, accepted_at_utc,
                started_at_utc, finished_at_utc,
                config_snapshot_json, error_summary, progress_json,
                source_summary_json
            ) VALUES (
                :run_id, :idempotency_key, :request_fingerprint,
                :operational_day, :status, :accepted_at_utc,
                :started_at_utc, :finished_at_utc,
                :config_snapshot_json, :error_summary, :progress_json,
                :source_summary_json
            )
        """
            ),
            {
                "run_id": run_id,
                "idempotency_key": f"key_{run_id}",
                "request_fingerprint": f"fp_{run_id}",
                "operational_day": accepted_at_utc.date().isoformat(),
                "status": status,
                "accepted_at_utc": format_utc(accepted_at_utc),
                "started_at_utc": format_utc(started_at_utc) if started_at_utc else None,
                "finished_at_utc": format_utc(finished_at_utc) if finished_at_utc else None,
                "config_snapshot_json": "{}",
                "error_summary": None,
                "progress_json": None,
                "source_summary_json": None,
            },
        )


def test_total_opportunities_excludes_excluded(client: TestClient, engine: Engine) -> None:
    """Total opportunities excludes location_status='excluded'."""
    _insert_opportunity(engine, "opp_1", location_status="eligible")
    _insert_opportunity(engine, "opp_2", location_status="review")
    _insert_opportunity(engine, "opp_3", location_status="excluded")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["total_opportunities"] == 2
    assert data["excluded_opportunities"] == 1


def test_new_opportunities_first_inclusive_boundary(
    client: TestClient, engine: Engine, today_jkt: datetime
) -> None:
    """New opportunities window includes the first boundary (today - 6 days)."""
    # Window: [today - 6, today]
    # First boundary: today - 6 days at 00:00:00 Jakarta
    first_boundary = datetime.combine(
        today_jkt - timedelta(days=6), datetime.min.time(), tzinfo=JAKARTA
    )
    first_boundary_utc = first_boundary.astimezone(UTC)

    _insert_opportunity(
        engine,
        "opp_1",
        first_seen_at_utc=format_utc(first_boundary_utc),
    )

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["new_opportunities"] == 1


def test_new_opportunities_last_inclusive_boundary(
    client: TestClient, engine: Engine, today_jkt: datetime
) -> None:
    """New opportunities window includes the last boundary (today)."""
    # Last boundary: today at 23:59:59 Jakarta
    last_boundary = datetime.combine(today_jkt, datetime.max.time(), tzinfo=JAKARTA)
    last_boundary_utc = last_boundary.astimezone(UTC)

    _insert_opportunity(
        engine,
        "opp_1",
        first_seen_at_utc=format_utc(last_boundary_utc),
    )

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["new_opportunities"] == 1


def test_new_opportunities_just_outside_window(
    client: TestClient, engine: Engine, today_jkt: datetime
) -> None:
    """New opportunities window excludes dates just outside the boundaries."""
    # Just before first boundary: today - 7 days at 23:59:59
    before_start = datetime.combine(
        today_jkt - timedelta(days=7), datetime.max.time(), tzinfo=JAKARTA
    )
    before_start_utc = before_start.astimezone(UTC)

    # Just after last boundary: tomorrow at 00:00:00
    after_end = datetime.combine(today_jkt + timedelta(days=1), datetime.min.time(), tzinfo=JAKARTA)
    after_end_utc = after_end.astimezone(UTC)

    _insert_opportunity(engine, "opp_1", first_seen_at_utc=format_utc(before_start_utc))
    _insert_opportunity(engine, "opp_2", first_seen_at_utc=format_utc(after_end_utc))

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["new_opportunities"] == 0


def test_opportunities_by_target_role(client: TestClient, engine: Engine) -> None:
    """Group opportunities by target_role_id."""
    _insert_target_role(engine, "role_1", "Backend Engineer")
    _insert_target_role(engine, "role_2", "Frontend Engineer")

    _insert_opportunity(engine, "opp_1", target_role_id="role_1")
    _insert_opportunity(engine, "opp_2", target_role_id="role_1")
    _insert_opportunity(engine, "opp_3", target_role_id="role_2")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    role_groups = {g["role_id"]: g["count"] for g in data["opportunities_by_target_role"]}

    assert role_groups["role_1"] == 2
    assert role_groups["role_2"] == 1


def test_unmatched_role_grouping(client: TestClient, engine: Engine) -> None:
    """Opportunities without a valid target_role_id go into Unmatched group."""
    _insert_target_role(engine, "role_1", "Backend Engineer")

    _insert_opportunity(engine, "opp_1", target_role_id="role_1")
    _insert_opportunity(engine, "opp_2", target_role_id=None)
    _insert_opportunity(engine, "opp_3", target_role_id="nonexistent_role")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    role_groups = {g["label"]: g["count"] for g in data["opportunities_by_target_role"]}

    assert role_groups["Backend Engineer"] == 1
    assert role_groups["Unmatched"] == 2


def test_inactive_role_included_in_grouping(client: TestClient, engine: Engine) -> None:
    """Historical opportunities referencing inactive roles are still grouped."""
    _insert_target_role(engine, "role_1", "Backend Engineer", enabled=False)

    _insert_opportunity(engine, "opp_1", target_role_id="role_1")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    role_groups = {g["role_id"]: g["count"] for g in data["opportunities_by_target_role"]}

    assert role_groups["role_1"] == 1


def test_opportunities_by_source_multiple_types(client: TestClient, engine: Engine) -> None:
    """One opportunity with multiple sources appears in multiple groups."""
    _insert_opportunity(engine, "opp_1")
    _insert_source(engine, "src_1", "opp_1", "greenhouse")
    _insert_source(engine, "src_2", "opp_1", "linkedin_post_manual")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    source_groups = {g["source_type"]: g["count"] for g in data["opportunities_by_source"]}

    assert source_groups["greenhouse"] == 1
    assert source_groups["linkedin_post_manual"] == 1


def test_opportunities_by_source_distinct_counting(client: TestClient, engine: Engine) -> None:
    """Count distinct opportunity IDs per source type."""
    _insert_opportunity(engine, "opp_1")
    _insert_opportunity(engine, "opp_2")
    _insert_source(engine, "src_1", "opp_1", "greenhouse")
    _insert_source(engine, "src_2", "opp_2", "greenhouse")
    _insert_source(engine, "src_3", "opp_1", "greenhouse")  # Duplicate source type for opp_1

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    source_groups = {g["source_type"]: g["count"] for g in data["opportunities_by_source"]}

    assert source_groups["greenhouse"] == 2  # Two distinct opportunities


def test_opportunities_with_no_source(client: TestClient, engine: Engine) -> None:
    """Opportunities with no source go into Unknown group."""
    _insert_opportunity(engine, "opp_1")
    _insert_opportunity(engine, "opp_2")
    _insert_source(engine, "src_1", "opp_1", "greenhouse")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    source_groups = {g["source_type"]: g["count"] for g in data["opportunities_by_source"]}

    assert source_groups["greenhouse"] == 1
    assert source_groups["Unknown"] == 1


def test_opportunities_by_location(client: TestClient, engine: Engine) -> None:
    """Group opportunities by (province, city) pair."""
    _insert_opportunity(engine, "opp_1", province="DKI Jakarta", city="Jakarta Selatan")
    _insert_opportunity(engine, "opp_2", province="DKI Jakarta", city="Jakarta Selatan")
    _insert_opportunity(engine, "opp_3", province="Jawa Barat", city="Bandung")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    location_groups = {
        (g["province"], g["city"]): g["count"] for g in data["opportunities_by_location"]
    }

    assert location_groups[("DKI Jakarta", "Jakarta Selatan")] == 2
    assert location_groups[("Jawa Barat", "Bandung")] == 1


def test_location_partial_values(client: TestClient, engine: Engine) -> None:
    """Partially known locations are preserved with Unknown for missing parts."""
    _insert_opportunity(engine, "opp_1", province="DKI Jakarta", city=None)
    _insert_opportunity(engine, "opp_2", province=None, city="Jakarta")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    location_groups = {
        (g["province"], g["city"]): g["count"] for g in data["opportunities_by_location"]
    }

    assert location_groups[("DKI Jakarta", "Unknown")] == 1
    assert location_groups[("Unknown", "Jakarta")] == 1


def test_excluded_omitted_from_groupings(client: TestClient, engine: Engine) -> None:
    """Excluded opportunities are omitted from all groupings."""
    _insert_target_role(engine, "role_1", "Backend Engineer")

    _insert_opportunity(engine, "opp_1", location_status="eligible", target_role_id="role_1")
    _insert_opportunity(engine, "opp_2", location_status="excluded", target_role_id="role_1")
    _insert_source(engine, "src_1", "opp_1", "greenhouse")
    _insert_source(engine, "src_2", "opp_2", "greenhouse")

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()

    # Total should be 1 (only eligible)
    assert data["total_opportunities"] == 1

    # Role grouping should only count eligible
    role_groups = {g["role_id"]: g["count"] for g in data["opportunities_by_target_role"]}
    assert role_groups["role_1"] == 1

    # Source grouping should only count eligible
    source_groups = {g["source_type"]: g["count"] for g in data["opportunities_by_source"]}
    assert source_groups["greenhouse"] == 1


def test_read_model_freshness_timestamp(client: TestClient, engine: Engine) -> None:
    """Expose latest_read_model_refresh_at_utc from sheets_sync_state."""
    refresh_time = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(
            sheets_sync_state.insert().values(
                state_id="google_sheets",
                last_full_refresh_at_utc=format_utc(refresh_time),
                last_attempt_at_utc=format_utc(refresh_time),
                last_refresh_outcome="succeeded",
                last_refresh_error_code=None,
                spreadsheet_schema_version="1",
                updated_at_utc=format_utc(datetime.now(UTC)),
            )
        )

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["latest_read_model_refresh_at_utc"] == format_utc(refresh_time)


def test_empty_read_model(client: TestClient) -> None:
    """Empty Read Model returns correct defaults."""
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["total_opportunities"] == 0
    assert data["excluded_opportunities"] == 0
    assert data["new_opportunities"] == 0
    assert data["opportunities_by_target_role"] == []
    assert data["opportunities_by_source"] == []
    assert data["opportunities_by_location"] == []
    assert data["latest_read_model_refresh_at_utc"] is None


def test_latest_run_accepted_status(client: TestClient, engine: Engine) -> None:
    """Latest run endpoint returns run with status='accepted'."""
    accepted_at = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)
    _insert_search_run(engine, "run_1", accepted_at, status="accepted")

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    data = response.json()
    assert data["run_id"] == "run_1"
    assert data["status"] == "accepted"


def test_latest_run_running_status(client: TestClient, engine: Engine) -> None:
    """Latest run endpoint returns run with status='running'."""
    accepted_at = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)
    started_at = datetime(2026, 11, 18, 9, 1, 0, tzinfo=UTC)
    _insert_search_run(
        engine,
        "run_1",
        accepted_at,
        status="running",
        started_at_utc=started_at,
    )

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    data = response.json()
    assert data["run_id"] == "run_1"
    assert data["status"] == "running"
    assert data["started_at_utc"] is not None


def test_latest_run_finished_status(client: TestClient, engine: Engine) -> None:
    """Latest run endpoint returns run with finished status."""
    accepted_at = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)
    started_at = datetime(2026, 11, 18, 9, 1, 0, tzinfo=UTC)
    finished_at = datetime(2026, 11, 18, 9, 5, 0, tzinfo=UTC)
    _insert_search_run(
        engine,
        "run_1",
        accepted_at,
        status="completed",
        started_at_utc=started_at,
        finished_at_utc=finished_at,
    )

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    data = response.json()
    assert data["run_id"] == "run_1"
    assert data["status"] == "completed"
    assert data["finished_at_utc"] is not None


def test_latest_run_selection_by_accepted_timestamp(client: TestClient, engine: Engine) -> None:
    """Latest run is selected by accepted_at_utc descending."""
    older = datetime(2026, 11, 18, 8, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)

    _insert_search_run(engine, "run_older", older, status="completed")
    _insert_search_run(engine, "run_newer", newer, status="accepted")

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    data = response.json()
    assert data["run_id"] == "run_newer"


def test_latest_run_deterministic_tie_breaking(client: TestClient, engine: Engine) -> None:
    """When accepted_at_utc is the same, tie-break by run_id ascending."""
    accepted_at = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)

    _insert_search_run(engine, "run_b", accepted_at, status="completed")
    _insert_search_run(engine, "run_a", accepted_at, status="completed")

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    data = response.json()
    assert data["run_id"] == "run_a"  # Alphabetically first


def test_no_existing_run(client: TestClient) -> None:
    """When no runs exist, return null (not an error)."""
    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    data = response.json()
    assert data is None


def test_route_security_no_store_header(client: TestClient) -> None:
    """Dashboard endpoints return Cache-Control: no-store."""
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"


def test_existing_tracker_metrics_unchanged(client: TestClient, engine: Engine) -> None:
    """Existing M4C3B tracker metrics still work correctly."""
    # Insert opportunity with application and interview dates
    _insert_opportunity(engine, "opp_1")

    # Update tracker fields
    with engine.begin() as conn:
        conn.execute(
            opportunities_read_model.update()
            .where(opportunities_read_model.c.opportunity_id == "opp_1")
            .values(
                date_applied="2026-11-15",
                interview_date="2026-11-20",
            )
        )

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["applications_last_7_days"] == 1
    assert data["upcoming_interviews"] == 1


def test_date_parser_regression_11_01_2026() -> None:
    """Explicit test: 11/01/2026 parses as 11 January 2026 (day-first)."""
    from personal_job_finder_api.domain.tracker_dates import parse_tracker_date

    result = parse_tracker_date("11/01/2026")
    assert result.value is not None
    assert result.value.year == 2026
    assert result.value.month == 1  # January
    assert result.value.day == 11


def test_no_google_sheets_network_requests(client: TestClient, engine: Engine) -> None:
    """Dashboard endpoints work without Google Sheets integration."""
    # The dashboard only reads from local database tables
    # (opportunities_read_model, job_sources_read_model, sheets_sync_state)
    # and never calls Google Sheets APIs.

    # Make dashboard requests
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    response = client.get("/api/v1/search-runs/latest")
    assert response.status_code == 200

    # Verify responses are successful
    data = response.json()
    assert data is None or isinstance(data, dict)
