"""API and service tests for manual search-run start/status endpoints."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import search_runs
from personal_job_finder_api.domain.search_policy import RunStatus
from personal_job_finder_api.integrations.google_sheets.config import REQUIRED_TABS
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.main import create_app
from personal_job_finder_api.search.connectors import SourceRunResult, SourceRunStatus
from personal_job_finder_api.search.reservation import (
    IdempotencyKeyConflict,
    ReservationRejected,
    ReservationRequest,
    SearchReservationService,
)
from personal_job_finder_api.search.run_service import SearchRunService

JAKARTA = ZoneInfo("Asia/Jakarta")
LOCAL_ORIGIN = "http://127.0.0.1:5173"
KEY_ONE = "11111111-1111-4111-8111-111111111111"
KEY_TWO = "22222222-2222-4222-8222-222222222222"


def wib(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=JAKARTA)


class RunCounter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0

    def increment(self) -> None:
        with self.lock:
            self.calls += 1


class StubConnector:
    def __init__(
        self,
        counter: RunCounter | None = None,
        *,
        source_id: str = "company_1",
        records_found: int = 1,
        error: Exception | None = None,
        release_event: threading.Event | None = None,
    ) -> None:
        self.source_id = source_id
        self.records_found = records_found
        self.error = error
        self.release_event = release_event
        self._counter = counter

    def run(self) -> SourceRunResult:
        if self._counter is not None:
            self._counter.increment()
        if self.release_event is not None:
            self.release_event.wait(timeout=5)
        if self.error is not None:
            raise self.error
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.COMPLETED,
            records_found=self.records_found,
        )


def _factory(*connectors: StubConnector):
    return lambda _snapshot: list(connectors)


class _RefreshTransport:
    """A valid, empty canonical spreadsheet for pre-reservation reconciliation."""

    def __init__(self) -> None:
        self.tabs = {tab: [list(headers)] for tab, headers in REQUIRED_TABS.items()}
        self.calls: list[str] = []

    def get_spreadsheet(self, spreadsheet_id: str) -> dict:
        self.calls.append("get_spreadsheet")
        return {"sheets": [{"properties": {"title": title}} for title in self.tabs]}

    def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict:
        self.calls.append("batch_get_values")
        value_ranges = []
        for requested in ranges:
            tab = requested.split("!", 1)[0]
            rows = self.tabs.get(tab, [])
            if requested.endswith("!1:1"):
                rows = rows[:1]
            value_ranges.append({"values": [list(row) for row in rows]} if rows else {})
        return {"valueRanges": value_ranges}


def _client(
    engine: Engine,
    now: datetime,
    factory=None,
    transport: _RefreshTransport | None = None,
) -> TestClient:
    app = create_app(
        engine=engine,
        clock=lambda: now,
        connector_factory=factory or _factory(StubConnector()),
        sync_transport=transport or _RefreshTransport(),
        sync_spreadsheet_id="test_spreadsheet_id_1234567890",
    )
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _post(
    client: TestClient,
    *,
    key: str | None = KEY_ONE,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
):
    request_headers = {"Origin": LOCAL_ORIGIN}
    if key is not None:
        request_headers["Idempotency-Key"] = key
    request_headers.update(headers or {})
    return client.post(
        "/api/v1/search-runs",
        headers=request_headers,
        json={} if json_body is None else json_body,
    )


def _run_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(search_runs)).scalar_one()


def _insert_run(
    engine: Engine,
    *,
    run_id: str,
    key: str,
    status: str,
    accepted_at: datetime,
    operational_day: str = "2026-11-12",
    started_at: datetime | None = None,
    error_summary: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                run_id=run_id,
                idempotency_key=key,
                request_fingerprint="e" * 64,
                operational_day=operational_day,
                status=status,
                accepted_at_utc=format_utc(accepted_at),
                started_at_utc=format_utc(started_at) if started_at else None,
                config_snapshot_json="{}",
                error_summary=error_summary,
            )
        )


def test_start_rejects_missing_target_roles_before_reservation(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(roles=0)
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        response = _post(client)

    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "search_configuration_invalid"
    assert "no_target_roles" in payload["details"]["blocking_reasons"]
    assert response.headers["cache-control"] == "no-store"
    assert _run_count(engine) == 0


def test_start_rejects_no_usable_source_in_configuration(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(companies_count=0)
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        response = _post(client)

    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "search_configuration_invalid"
    assert "no_usable_source" in payload["details"]["blocking_reasons"]
    assert _run_count(engine) == 0


def test_start_rejects_when_no_connector_implementation_exists(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now, factory=_factory()) as client:
        response = _post(client)

    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "search_configuration_invalid"
    assert payload["details"]["blocking_reasons"] == ["no_usable_source"]
    assert _run_count(engine) == 0


def test_start_reads_the_canonical_snapshot_before_acceptance(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    transport = _RefreshTransport()

    with _client(engine, wib(2026, 11, 12, 7, 0), transport=transport) as client:
        response = _post(client)

    assert response.status_code == 202
    assert "get_spreadsheet" in transport.calls
    assert "batch_get_values" in transport.calls


def test_start_rejects_before_reservation_when_sheets_cannot_be_read(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()

    class FailingTransport(_RefreshTransport):
        def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict:
            raise GoogleSheetsError(
                "timeout",
                "provider body must never reach the response or state",
            )

    with _client(
        engine,
        wib(2026, 11, 12, 7, 0),
        transport=FailingTransport(),
    ) as client:
        response = _post(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "sheets_refresh_unavailable"
    assert "provider body" not in response.text
    assert _run_count(engine) == 0


def test_start_rejects_blocked_company_even_if_factory_could_build_it(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(policy_status="blocked")
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        response = _post(client)

    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "search_configuration_invalid"
    assert payload["details"]["blocking_reasons"] == ["no_usable_source"]
    assert _run_count(engine) == 0


def test_start_rejects_active_run(engine: Engine, seed_config) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        # Insert after startup so recovery does not interrupt this active run.
        SearchReservationService(engine, clock=lambda: now).reserve(
            ReservationRequest(idempotency_key=KEY_TWO)
        )
        response = _post(client)

    assert response.status_code == 409
    payload = response.json()["error"]
    assert payload["code"] == "search_already_running"
    assert "active_run_exists" in payload["details"]["blocking_reasons"]
    assert _run_count(engine) == 1


def test_start_rejects_exhausted_daily_quota(engine: Engine, seed_config) -> None:
    seed_config()
    _insert_run(
        engine,
        run_id="run_1",
        key=KEY_ONE,
        status="completed",
        accepted_at=wib(2026, 11, 12, 6, 0),
    )
    _insert_run(
        engine,
        run_id="run_2",
        key=KEY_TWO,
        status="failed",
        accepted_at=wib(2026, 11, 12, 6, 30),
    )
    now = wib(2026, 11, 12, 19, 0)

    with _client(engine, now) as client:
        response = _post(client, key=str(uuid.uuid4()))

    assert response.status_code == 429
    payload = response.json()["error"]
    assert payload["code"] == "daily_quota_exhausted"
    assert payload["details"]["next_reset_at"] == "2026-11-12T23:00:00Z"
    assert int(response.headers["retry-after"]) > 0
    assert _run_count(engine) == 2


def test_start_rejects_active_cooldown_with_retry_after(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    _insert_run(
        engine,
        run_id="run_1",
        key=KEY_ONE,
        status="completed",
        accepted_at=wib(2026, 11, 12, 6, 0),
    )
    now = wib(2026, 11, 12, 12, 0)

    with _client(engine, now) as client:
        response = _post(client, key=KEY_TWO)

    assert response.status_code == 429
    payload = response.json()["error"]
    assert payload["code"] == "search_cooldown_active"
    assert payload["details"]["next_eligible_at"] == "2026-11-12T11:00:00Z"
    assert response.headers["retry-after"] == str(6 * 60 * 60)
    assert _run_count(engine) == 1


def test_start_accepts_run_and_status_reaches_completed(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    counter = RunCounter()
    now = wib(2026, 11, 12, 7, 0)

    with _client(
        engine,
        now,
        factory=_factory(StubConnector(counter, records_found=2)),
    ) as client:
        response = _post(client)
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["run_id"].startswith("run_")
        assert accepted["status"] == "accepted"
        assert accepted["accepted_at_utc"].endswith("Z")
        assert accepted["operational_day"] == "2026-11-12"
        assert accepted["remaining_runs"] == 1
        assert accepted["status_url"] == f"/api/v1/search-runs/{accepted['run_id']}"
        assert response.headers["cache-control"] == "no-store"

        status_response = client.get(accepted["status_url"])

    assert counter.calls == 1
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["run_id"] == accepted["run_id"]
    assert status_payload["status"] == "completed"
    progress = status_payload["progress"]
    assert progress["stage"] == "completed"
    assert progress["completed_sources"] == 1
    assert progress["total_sources"] == 1
    assert status_payload["source_results"] == [
        {
            "source_id": "company_1",
            "status": "completed",
            "records_found": 2,
            "error_code": None,
        }
    ]
    assert status_payload["error_summary"] is None
    assert status_response.headers["cache-control"] == "no-store"
    assert _run_count(engine) == 1


def test_same_key_same_payload_replays_without_new_reservation(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    counter = RunCounter()
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now, factory=_factory(StubConnector(counter))) as client:
        first = _post(client)
        second = _post(client)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["status"] == "completed"
    assert second.json()["remaining_runs"] == 1
    assert counter.calls == 1
    assert _run_count(engine) == 1


def test_same_key_different_payload_conflicts(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)
    service = SearchRunService(
        engine,
        connector_factory=_factory(StubConnector(RunCounter())),
        reconcile=lambda: None,
        clock=lambda: now,
    )

    service.start(idempotency_key=KEY_ONE, payload={})

    try:
        service.start(idempotency_key=KEY_ONE, payload={"note": "different"})
    except IdempotencyKeyConflict as exc:
        assert exc.existing_run_id.startswith("run_")
    else:  # pragma: no cover - explicit failure message
        raise AssertionError("expected IdempotencyKeyConflict")
    assert _run_count(engine) == 1


def test_missing_idempotency_key_is_rejected(engine: Engine, seed_config) -> None:
    seed_config()

    with _client(engine, wib(2026, 11, 12, 7, 0)) as client:
        response = _post(client, key=None)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_idempotency_key"
    assert _run_count(engine) == 0


def test_invalid_idempotency_key_is_rejected(engine: Engine, seed_config) -> None:
    seed_config()

    with _client(engine, wib(2026, 11, 12, 7, 0)) as client:
        response = _post(client, key="not-a-uuid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"
    assert _run_count(engine) == 0


def test_concurrent_run_creation_accepts_exactly_one(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)
    service = SearchRunService(
        engine,
        connector_factory=_factory(StubConnector(RunCounter())),
        reconcile=lambda: None,
        clock=lambda: now,
    )

    def attempt(key: str) -> str:
        try:
            service.start(idempotency_key=key, payload={})
        except ReservationRejected as exc:
            return exc.decision.blocking_reasons[0].value
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, [KEY_ONE, KEY_TWO]))

    assert sorted(outcomes) == ["accepted", "active_run_exists"]
    assert _run_count(engine) == 1


def test_start_rejects_untrusted_host(engine: Engine, seed_config) -> None:
    seed_config()

    with _client(engine, wib(2026, 11, 12, 7, 0)) as client:
        response = _post(client, headers={"Host": "evil.test"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"
    assert _run_count(engine) == 0


def test_start_rejects_missing_or_untrusted_origin(engine: Engine, seed_config) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        missing = client.post(
            "/api/v1/search-runs",
            headers={"Idempotency-Key": KEY_ONE},
            json={},
        )
        untrusted = _post(client, headers={"Origin": "https://evil.test"})

    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "invalid_origin"
    assert untrusted.status_code == 403
    assert untrusted.json()["error"]["code"] == "invalid_origin"
    assert _run_count(engine) == 0


def test_connector_exception_after_reservation_consumes_quota_and_hides_secret(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)

    with _client(
        engine,
        now,
        factory=_factory(StubConnector(RunCounter(), error=RuntimeError("token=super-secret"))),
    ) as client:
        response = _post(client)
        assert response.status_code == 202
        status_response = client.get(response.json()["status_url"])
        policy_response = client.get("/api/v1/search-policy")

    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "failed"
    assert "super-secret" not in (payload["error_summary"] or "")
    assert payload["source_results"][0]["error_code"] == "connector_error"
    assert policy_response.json()["accepted_runs"] == 1
    assert policy_response.json()["remaining_runs"] == 1


def test_startup_recovery_marks_running_run_interrupted_without_resuming(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    _insert_run(
        engine,
        run_id="run_interrupted",
        key=KEY_ONE,
        status="running",
        accepted_at=wib(2026, 11, 12, 6, 0),
        started_at=wib(2026, 11, 12, 6, 1),
    )
    counter = RunCounter()

    with _client(
        engine,
        wib(2026, 11, 12, 7, 0),
        factory=_factory(StubConnector(counter)),
    ) as client:
        status_response = client.get("/api/v1/search-runs/run_interrupted")
        policy_response = client.get("/api/v1/search-policy")

    assert counter.calls == 0
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "interrupted"
    assert "Backend restarted" in payload["error_summary"]
    assert policy_response.json()["accepted_runs"] == 1


def test_status_returns_accepted_run_without_progress(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()

    with _client(engine, wib(2026, 11, 12, 7, 5)) as client:
        # Insert after startup so recovery does not interrupt this accepted run.
        _insert_run(
            engine,
            run_id="run_waiting",
            key=KEY_ONE,
            status="accepted",
            accepted_at=wib(2026, 11, 12, 7, 0),
        )
        response = client.get("/api/v1/search-runs/run_waiting")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["progress"] is None
    assert payload["source_results"] == []
    assert payload["started_at_utc"] is None
    assert payload["finished_at_utc"] is None
    assert response.headers["cache-control"] == "no-store"


def test_status_unknown_run_returns_not_found(engine: Engine) -> None:
    with _client(engine, wib(2026, 11, 12, 7, 0)) as client:
        response = client.get("/api/v1/search-runs/run_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "search_run_not_found"
    assert response.headers["cache-control"] == "no-store"


def test_status_serializes_persisted_source_summary(engine: Engine, seed_config) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)
    _insert_run(
        engine,
        run_id="run_done",
        key=KEY_ONE,
        status="completed_with_warnings",
        accepted_at=wib(2026, 11, 12, 6, 0),
        started_at=wib(2026, 11, 12, 6, 1),
    )
    with engine.begin() as connection:
        connection.execute(
            search_runs.update()
            .where(search_runs.c.run_id == "run_done")
            .values(
                finished_at_utc=format_utc(wib(2026, 11, 12, 6, 2)),
                progress_json='{"stage":"completed","completed_sources":1,"total_sources":2}',
                source_summary_json=(
                    '[{"source_id":"company_1","status":"completed",'
                    '"records_found":3,"error_code":null},'
                    '{"source_id":"company_2","status":"failed",'
                    '"records_found":0,"error_code":"source_failed"}]'
                ),
            )
        )

    with _client(engine, now) as client:
        response = client.get("/api/v1/search-runs/run_done")

    payload = response.json()
    assert payload["status"] == "completed_with_warnings"
    assert payload["progress"]["completed_sources"] == 1
    assert payload["source_results"][1]["error_code"] == "source_failed"
    assert payload["started_at_utc"].endswith("Z")
    assert payload["finished_at_utc"].endswith("Z")


def test_search_run_status_is_not_listed_as_active_after_terminal_state(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)
    _insert_run(
        engine,
        run_id="run_done",
        key=KEY_ONE,
        status=RunStatus.COMPLETED.value,
        accepted_at=wib(2026, 11, 12, 6, 0),
    )

    with _client(engine, now) as client:
        policy = client.get("/api/v1/search-policy")

    assert policy.json()["has_active_run"] is False


def test_utc_clock_is_used_for_accepted_timestamp(engine: Engine, seed_config) -> None:
    seed_config()
    now = wib(2026, 11, 12, 7, 0)

    with _client(engine, now) as client:
        response = _post(client)

    accepted = response.json()
    assert accepted["accepted_at_utc"] == "2026-11-12T00:00:00Z"
