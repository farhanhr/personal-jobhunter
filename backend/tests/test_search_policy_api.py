"""API tests for the read-only search-policy endpoint."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.db.tables import search_runs
from personal_job_finder_api.main import create_app
from personal_job_finder_api.search.connectors import SourceRunResult, SourceRunStatus
from personal_job_finder_api.search.reservation import (
    ReservationRequest,
    SearchReservationService,
)

JAKARTA = ZoneInfo("Asia/Jakarta")


class _AvailableConnector:
    source_id = "company_1"

    def run(self) -> SourceRunResult:  # pragma: no cover - policy never runs it
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.COMPLETED,
            records_found=0,
        )


def _available_connectors(_snapshot):
    return [_AvailableConnector()]


def wib(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=JAKARTA)


def _client(engine: Engine, now: datetime) -> TestClient:
    app = create_app(
        engine=engine,
        clock=lambda: now,
        connector_factory=_available_connectors,
    )
    return TestClient(app)


def _insert_accepted_run(
    engine: Engine,
    *,
    run_id: str = "run_existing",
    key: str = "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    accepted_at: datetime,
    operational_day: str = "2026-11-12",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                run_id=run_id,
                idempotency_key=key,
                request_fingerprint="d" * 64,
                operational_day=operational_day,
                status="accepted",
                accepted_at_utc=accepted_at.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                config_snapshot_json="{}",
            )
        )


def test_policy_endpoint_reports_eligible_configuration(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 12, 0)

    with _client(engine, now) as client:
        response = client.get("/api/v1/search-policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["timezone"] == "Asia/Jakarta"
    assert payload["operational_day"] == "2026-11-12"
    assert payload["max_runs"] == 2
    assert payload["accepted_runs"] == 0
    assert payload["remaining_runs"] == 2
    assert payload["last_accepted_at"] is None
    assert payload["cooldown_until"] is None
    assert payload["cooldown_remaining_seconds"] == 0
    assert payload["has_active_run"] is False
    assert payload["can_start"] is True
    assert payload["block_reason"] is None
    assert payload["blocking_reasons"] == []
    assert payload["next_reset_at"] == "2026-11-12T23:00:00Z"
    assert response.headers["cache-control"] == "no-store"


def test_policy_endpoint_reports_configuration_blocker(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(roles=0, companies_count=1)

    with _client(engine, wib(2026, 11, 12, 12, 0)) as client:
        response = client.get("/api/v1/search-policy")

    payload = response.json()
    assert payload["can_start"] is False
    assert payload["block_reason"] == "no_target_roles"
    assert payload["blocking_reasons"] == ["no_target_roles"]


def test_policy_endpoint_sees_active_run_reserved_after_startup(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        service = SearchReservationService(engine, clock=lambda: now)
        outcome = service.reserve(
            ReservationRequest(idempotency_key="dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        )
        response = client.get("/api/v1/search-policy")

    payload = response.json()
    assert outcome.run.status.value == "accepted"
    assert payload["has_active_run"] is True
    assert payload["accepted_runs"] == 1
    assert payload["remaining_runs"] == 1
    assert payload["can_start"] is False
    assert payload["block_reason"] == "active_run_exists"


def test_startup_recovery_marks_stale_run_interrupted(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    _insert_accepted_run(engine, accepted_at=wib(2026, 11, 12, 7, 0))

    with _client(engine, wib(2026, 11, 12, 8, 0)) as client:
        response = client.get("/api/v1/search-policy")

    payload = response.json()
    assert payload["has_active_run"] is False
    assert payload["accepted_runs"] == 1
    assert payload["block_reason"] == "cooldown_active"

    with engine.connect() as connection:
        status = connection.execute(
            sa.select(search_runs.c.status).where(search_runs.c.run_id == "run_existing")
        ).scalar_one()
    assert status == "interrupted"


def test_health_endpoint_is_preserved(engine: Engine) -> None:
    with _client(engine, wib(2026, 11, 12, 12, 0)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "personal-job-finder-api",
        "version": "0.1.0",
    }


def test_startup_path_creates_private_database_and_migrates(tmp_path) -> None:
    data_dir = tmp_path / "missing" / "private-data"
    app = create_app(
        app_settings=Settings(data_dir=data_dir),
        clock=lambda: wib(2026, 11, 12, 12, 0),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/search-policy")

    assert response.status_code == 200
    assert response.json()["operational_day"] == "2026-11-12"

    database_path = data_dir / "personal-job-finder.sqlite3"
    assert database_path.exists()
    if os.name == "posix":
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
