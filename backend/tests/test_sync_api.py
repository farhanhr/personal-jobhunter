"""Tests for the manual Google Sheets synchronization API.

The API is a thin layer over the M4B2 dispatcher: it must never reimplement
retry, projection, or reconciliation logic, and it must never touch connectors or
search quota. Every test injects a fake Sheets transport.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import (
    search_runs,
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
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    REQUIRED_TABS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.main import create_app
from personal_job_finder_api.search.staging import OpportunityStagingService

NOW = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)
SPREADSHEET_ID = "test_spreadsheet_id_1234567890"
ORIGIN = {"Origin": "http://127.0.0.1:5173"}
ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)


def _row_window(address: str) -> tuple[int | None, int | None]:
    """Return the row bounds of an A1 address such as ``A5:A5``.

    Column-only ranges (``A:AG``) have no row bounds and return ``(None, None)``.
    """

    start, _, end = address.partition(":")
    digits = "".join(ch for ch in start if ch.isdigit())
    if not digits:
        return None, None
    first = int(digits)
    end_digits = "".join(ch for ch in end if ch.isdigit())
    last = int(end_digits) if end_digits else first
    return first, last


class FakeSheet:
    """The subset of one spreadsheet the dispatcher reads and writes."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[str]]] = {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
        self.calls: list[tuple[str, Any]] = []


class FakeTransport:
    def __init__(self, sheet: FakeSheet | None = None) -> None:
        self.sheet = sheet or FakeSheet()
        self.calls: list[str] = []
        self.failures: list[Exception] = []
        self.after_write_error: Exception | None = None

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.calls.append("get_spreadsheet")
        return {"sheets": [{"properties": {"title": title}} for title in REQUIRED_TABS]}

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.calls.append("batch_get_values")
        value_ranges: list[dict[str, Any]] = []
        for requested in ranges:
            tab, address = str(requested).split("!", 1)
            rows = self.sheet.tabs.get(tab, [])
            first, last = _row_window(address)
            if first is None:
                # Whole-column range: return every row of the tab.
                value_ranges.append({"values": [list(row) for row in rows]} if rows else {})
                continue
            # Narrow range (the pre-write revalidation read): like the real API,
            # return only the requested rows; past-the-end rows come back empty.
            selected = rows[first - 1 : last] if first <= len(rows) else []
            value_ranges.append({"values": [list(row) for row in selected]} if selected else {})
        return {"valueRanges": value_ranges}

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        self.calls.append("batch_update_values")
        assert value_input_option == "RAW"
        if self.failures:
            raise self.failures.pop(0)
        for item in data:
            range_name = str(item["range"])
            tab = range_name.split("!", 1)[0]
            address = range_name.split("!", 1)[1]
            start_row = int("".join(ch for ch in address.split(":", 1)[0] if ch.isdigit()))
            rows = self.sheet.tabs[tab]
            for offset, values in enumerate(item["values"]):
                row_number = start_row + offset
                while len(rows) < row_number:
                    rows.append([""] * len(rows[0]))
                rows[row_number - 1] = [str(value) for value in values]
        if self.after_write_error is not None:
            raise self.after_write_error
        return {}

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append("batch_update_spreadsheet")
        return {}

    @property
    def google_calls(self) -> list[str]:
        return [call for call in self.calls if call != "batch_update_spreadsheet"]


def record(*, external_id: str = "1001", title: str = "Backend Engineer"):
    return OpportunityRecord(
        company_id="company_acme",
        company_name="Acme Example",
        job_title=title,
        location=classify_location("Jakarta, Indonesia"),
        description="<p>Build services.</p>",
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type="greenhouse",
            source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=NOW,
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, None, (ROLE,)),
    )


def stage(engine: Engine, *, run_id: str = "run_1", records: list | None = None) -> None:
    OpportunityStagingService(engine, clock=lambda: NOW).stage(
        run_id=run_id,
        records=records if records is not None else [record()],
    )


def _raise_google_error(code: str):
    """Build a factory that fails the way the production transport can."""

    def _factory() -> FakeTransport:
        raise GoogleSheetsError(code, "sanitized failure")

    return _factory


def client_for(
    engine: Engine,
    *,
    transport: FakeTransport | None = None,
    spreadsheet_id: str | None = SPREADSHEET_ID,
) -> tuple[TestClient, FakeTransport]:
    resolved = transport or FakeTransport()
    app = create_app(
        engine=engine,
        clock=lambda: NOW,
        sync_transport=resolved,
        sync_spreadsheet_id=spreadsheet_id,
    )
    return TestClient(app, base_url="http://127.0.0.1:8000"), resolved


def outbox_rows(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(sa.select(sync_outbox)).mappings().all()]


# --------------------------------------------------------------------- status


def test_status_reports_counts_per_entity_type_and_totals(engine: Engine) -> None:
    stage(engine)
    client, transport = client_for(engine)

    with client as http:
        response = http.get("/api/v1/sync/status")

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["opportunity"]["pending"] == 1
    assert body["counts"]["job_source"]["pending"] == 1
    assert body["totals"]["pending"] == 2
    assert body["totals"]["outstanding"] == 2
    assert body["counts"]["opportunity"]["synced"] == 0
    assert body["due_now"] == 2
    assert body["action_required"] is False
    assert body["blocked"] == []
    assert transport.google_calls == []


def test_status_never_exposes_payloads_credentials_or_configuration(
    engine: Engine,
) -> None:
    stage(engine)
    client, _ = client_for(engine)

    with client as http:
        body = http.get("/api/v1/sync/status").json()

    serialized = str(body).casefold()
    for forbidden in (
        "payload",
        "accesstoken",
        "access_token",
        "refresh_token",
        "client_secret",
        SPREADSHEET_ID.casefold(),
        "payload_json",
        "/home/",
        "sqlite",
    ):
        assert forbidden not in serialized


def test_status_sets_no_store(engine: Engine) -> None:
    client, _ = client_for(engine)

    with client as http:
        response = http.get("/api/v1/sync/status")

    assert response.headers["cache-control"] == "no-store"


def test_status_lists_blocked_projections_with_sanitized_codes(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=5,
                last_error_code="permission_denied",
            )
        )
    client, _ = client_for(engine)

    with client as http:
        body = http.get("/api/v1/sync/status").json()

    assert body["action_required"] is True
    assert body["totals"]["blocked"] == 2
    assert len(body["blocked"]) == 2
    entry = body["blocked"][0]
    assert entry["outbox_id"]
    assert entry["entity_type"] in {"opportunity", "job_source"}
    assert entry["attempt_count"] == 5
    assert entry["max_attempts"] == 5
    assert entry["last_error_code"] == "permission_denied"
    assert "payload" not in str(entry).casefold()


def test_status_reports_the_last_successful_sync(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="synced",
                synced_at_utc="2026-11-16T04:00:00.000000Z",
            )
        )
    client, _ = client_for(engine)

    with client as http:
        body = http.get("/api/v1/sync/status").json()

    assert body["totals"]["outstanding"] == 0
    assert body["due_now"] == 0
    assert body["last_synced_at_utc"] == "2026-11-16T04:00:00.000000Z"


def test_status_requires_an_allowed_host(engine: Engine) -> None:
    client, _ = client_for(engine)

    with client as http:
        response = http.get(
            "/api/v1/sync/status",
            headers={"Host": "evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"


# ---------------------------------------------------------------------- retry


def test_retry_dispatches_due_work_and_reports_the_outcome(engine: Engine) -> None:
    stage(engine)
    client, transport = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "dispatched"
    assert body["synced"] == 2
    assert body["failed"] == 0
    assert body["blocked"] == 0
    assert body["remaining"] == 0
    assert len(transport.sheet.tabs["Opportunities"]) == 2
    assert len(transport.sheet.tabs["Job_Sources"]) == 2
    assert {row["status"] for row in outbox_rows(engine)} == {"synced"}


def test_retry_with_no_due_work_makes_no_google_request(engine: Engine) -> None:
    client, transport = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "nothing_due"
    assert body["synced"] == 0
    assert body["remaining"] == 0
    assert transport.calls == []


def test_retry_with_only_blocked_work_makes_no_google_request(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=5,
                last_error_code="permission_denied",
            )
        )
    client, transport = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    assert response.json()["outcome"] == "nothing_due"
    assert transport.calls == []
    assert {row["status"] for row in outbox_rows(engine)} == {"blocked"}


def test_retry_reports_failures_without_leaking_provider_details(engine: Engine) -> None:
    stage(engine)
    transport = FakeTransport()
    transport.failures = [
        GoogleSheetsError("server_error", "provider body with token=super-secret"),
        GoogleSheetsError("server_error", "provider body with token=super-secret"),
    ]
    client, _ = client_for(engine, transport=transport)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 2
    assert body["remaining"] == 2
    assert "super-secret" not in str(body)
    assert {row["status"] for row in outbox_rows(engine)} == {"retry_wait"}


def test_retry_sets_no_store_and_serializes_concurrent_invocations(
    engine: Engine,
) -> None:
    stage(engine)
    client, transport = client_for(engine)
    original = transport.batch_update_values
    second: list[Any] = []

    def reenter(spreadsheet_id, data, *, value_input_option):  # noqa: ANN001
        if not second:
            second.append(client.post("/api/v1/sync/retry", headers=ORIGIN))
        return original(
            spreadsheet_id,
            data,
            value_input_option=value_input_option,
        )

    transport.batch_update_values = reenter  # type: ignore[method-assign]

    with client as http:
        first = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert len(second) == 1
    assert second[0].status_code == 409
    assert second[0].json()["error"]["code"] == "sync_already_running"


def test_retry_rejects_an_untrusted_origin(engine: Engine) -> None:
    stage(engine)
    client, transport = client_for(engine)

    with client as http:
        response = http.post(
            "/api/v1/sync/retry",
            headers={"Origin": "https://evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"
    assert transport.calls == []
    assert {row["status"] for row in outbox_rows(engine)} == {"pending"}


def test_retry_rejects_a_missing_origin(engine: Engine) -> None:
    client, transport = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"
    assert transport.calls == []


def test_retry_without_configuration_is_a_sanitized_error(engine: Engine) -> None:
    stage(engine)
    client, transport = client_for(engine, spreadsheet_id=None)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "sync_configuration_invalid"
    assert "replace_with" not in str(body)
    assert SPREADSHEET_ID not in str(body)
    assert transport.calls == []
    assert {row["status"] for row in outbox_rows(engine)} == {"pending"}


def test_retry_recovers_stale_in_progress_work_before_dispatching(
    engine: Engine,
) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="in_progress",
                updated_at_utc="2026-11-16T04:00:00.000000Z",
            )
        )
    client, _ = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    body = response.json()
    assert body["recovered"] == 2
    assert body["synced"] == 2
    assert {row["status"] for row in outbox_rows(engine)} == {"synced"}


def test_retry_does_not_recover_recent_in_progress_work(engine: Engine) -> None:
    stage(engine)
    recent = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="in_progress",
                updated_at_utc=recent,
            )
        )
    client, transport = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    assert response.json()["recovered"] == 0
    assert response.json()["outcome"] == "nothing_due"
    assert transport.calls == []


def test_retry_does_not_run_connectors_or_consume_quota(
    engine: Engine,
    monkeypatch,
) -> None:
    stage(engine)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("sync must never execute a connector")

    from personal_job_finder_api.search import connectors as connectors_module
    from personal_job_finder_api.search import reservation as reservation_module

    monkeypatch.setattr(connectors_module, "connector_snapshot", explode)
    monkeypatch.setattr(
        reservation_module.SearchReservationService,
        "reserve",
        explode,
    )

    client, _ = client_for(engine)
    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    assert response.json()["synced"] == 2


def test_retry_reconciles_the_affected_run(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                run_id="run_1",
                idempotency_key="key-run-1",
                request_fingerprint="fingerprint",
                operational_day="2026-11-16",
                status="completed_pending_sync",
                accepted_at_utc="2026-11-16T03:00:00.000000Z",
                started_at_utc="2026-11-16T03:00:00.000000Z",
                finished_at_utc="2026-11-16T03:00:00.000000Z",
                config_snapshot_json="{}",
                error_summary=None,
                progress_json='{"pending_sync":2}',
                source_summary_json=(
                    '[{"source_id":"company_acme","status":"completed"},'
                    '{"source_id":"company_other","status":"failed"}]'
                ),
            )
        )
    client, _ = client_for(engine)

    with client as http:
        http.post("/api/v1/sync/retry", headers=ORIGIN)

    with engine.connect() as connection:
        status = connection.execute(
            sa.select(search_runs.c.status).where(search_runs.c.run_id == "run_1")
        ).scalar_one()
        summaries = connection.execute(
            sa.select(search_runs.c.source_summary_json).where(search_runs.c.run_id == "run_1")
        ).scalar_one()

    assert status == "completed_with_warnings"
    assert '"status": "failed"' in summaries or '"status":"failed"' in summaries


# -------------------------------------------------------------------- requeue


def test_requeue_re_enables_a_blocked_projection(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=5,
                last_error_code="permission_denied",
            )
        )
    blocked_id = outbox_rows(engine)[0]["outbox_id"]
    client, _ = client_for(engine)

    with client as http:
        response = http.post(
            f"/api/v1/sync/outbox/{blocked_id}/requeue",
            headers=ORIGIN,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["outbox_id"] == blocked_id
    assert body["status"] == "pending"
    requeued = next(row for row in outbox_rows(engine) if row["outbox_id"] == blocked_id)
    assert requeued["status"] == "pending"
    assert requeued["attempt_count"] == 0
    assert requeued["last_error_code"] is None


def test_requeued_projection_is_dispatched_by_the_next_retry(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=5,
                last_error_code="spreadsheet_not_found",
            )
        )
    blocked_ids = [row["outbox_id"] for row in outbox_rows(engine)]
    client, transport = client_for(engine)

    with client as http:
        for blocked_id in blocked_ids:
            requeued = http.post(
                f"/api/v1/sync/outbox/{blocked_id}/requeue",
                headers=ORIGIN,
            )
            assert requeued.status_code == 200
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    assert response.json()["synced"] == 2
    assert {row["status"] for row in outbox_rows(engine)} == {"synced"}
    assert transport.sheet.tabs["Opportunities"][1][0].startswith("opp_")


def test_a_blocked_projection_is_untouched_until_explicitly_requeued(
    engine: Engine,
) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=5,
                last_error_code="permission_denied",
            )
        )
    blocked_ids = [row["outbox_id"] for row in outbox_rows(engine)]
    client, _ = client_for(engine)

    with client as http:
        http.post(
            f"/api/v1/sync/outbox/{blocked_ids[0]}/requeue",
            headers=ORIGIN,
        )
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    # Only the explicitly requeued projection moves; the other stays blocked.
    assert response.json()["synced"] == 1
    statuses = {row["outbox_id"]: row["status"] for row in outbox_rows(engine)}
    assert statuses[blocked_ids[0]] == "synced"
    assert statuses[blocked_ids[1]] == "blocked"


def test_requeue_rejects_an_unknown_projection(engine: Engine) -> None:
    client, _ = client_for(engine)

    with client as http:
        response = http.post(
            "/api/v1/sync/outbox/outbox_missing/requeue",
            headers=ORIGIN,
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "sync_outbox_not_found"


def test_requeue_rejects_a_projection_that_is_not_blocked(engine: Engine) -> None:
    stage(engine)
    pending_id = outbox_rows(engine)[0]["outbox_id"]
    client, _ = client_for(engine)

    with client as http:
        response = http.post(
            f"/api/v1/sync/outbox/{pending_id}/requeue",
            headers=ORIGIN,
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "sync_requeue_invalid_state"
    assert body["error"]["details"]["status"] == "pending"


def test_requeue_rejects_a_synced_projection(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="synced"))
    synced_id = outbox_rows(engine)[0]["outbox_id"]
    client, _ = client_for(engine)

    with client as http:
        response = http.post(
            f"/api/v1/sync/outbox/{synced_id}/requeue",
            headers=ORIGIN,
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "sync_requeue_invalid_state"


def test_requeue_rejects_an_untrusted_origin(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="blocked"))
    blocked_id = outbox_rows(engine)[0]["outbox_id"]
    client, _ = client_for(engine)

    with client as http:
        response = http.post(
            f"/api/v1/sync/outbox/{blocked_id}/requeue",
            headers={"Origin": "https://evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"
    assert {row["status"] for row in outbox_rows(engine)} == {"blocked"}


def test_retry_reports_a_failing_transport_factory_as_a_sanitized_error(
    engine: Engine,
) -> None:
    """A credential/config failure must not escape as an unhandled 500."""

    stage(engine)
    app = create_app(
        engine=engine,
        clock=lambda: NOW,
        sync_transport_factory=_raise_google_error("credentials_missing"),
        sync_spreadsheet_id=SPREADSHEET_ID,
    )
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "sync_configuration_invalid"
    assert response.headers["cache-control"] == "no-store"
    assert "credentials_missing" in str(body["error"]["details"])
    assert {row["status"] for row in outbox_rows(engine)} == {"pending"}


def test_retry_reconciles_a_settled_run_when_nothing_is_due(engine: Engine) -> None:
    """A crash between settling a row and reconciling its run must still resolve."""

    stage(engine)
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(status="synced"))
        connection.execute(
            search_runs.insert().values(
                run_id="run_1",
                idempotency_key="key-run-1",
                request_fingerprint="fingerprint",
                operational_day="2026-11-16",
                status="completed_pending_sync",
                accepted_at_utc="2026-11-16T03:00:00.000000Z",
                started_at_utc="2026-11-16T03:00:00.000000Z",
                finished_at_utc="2026-11-16T03:00:00.000000Z",
                config_snapshot_json="{}",
                error_summary=None,
                progress_json='{"pending_sync":2}',
                source_summary_json='[{"source_id":"company_acme","status":"completed"}]',
            )
        )
    client, transport = client_for(engine)

    with client as http:
        response = http.post("/api/v1/sync/retry", headers=ORIGIN)

    assert response.status_code == 200
    assert response.json()["outcome"] == "nothing_due"
    assert transport.calls == []
    with engine.connect() as connection:
        status = connection.execute(
            sa.select(search_runs.c.status).where(search_runs.c.run_id == "run_1")
        ).scalar_one()
    assert status == "completed"


def test_nothing_due_still_reports_outstanding_blocked_work(engine: Engine) -> None:
    stage(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="blocked",
                attempt_count=5,
                last_error_code="permission_denied",
            )
        )
    client, _ = client_for(engine)

    with client as http:
        body = http.post("/api/v1/sync/retry", headers=ORIGIN).json()

    assert body["outcome"] == "nothing_due"
    assert body["remaining"] == 2


def test_a_bare_dispatch_still_holds_the_guard(engine: Engine) -> None:
    """The M4B2 default must still refuse to overlap, not silently run."""

    from personal_job_finder_api.sync.dispatcher import (
        SyncAlreadyRunning,
        SyncDispatcher,
        dispatch_guard,
    )

    transport = FakeTransport()
    dispatcher = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id=SPREADSHEET_ID,
        clock=lambda: NOW,
    )

    with dispatch_guard():
        try:
            dispatcher.dispatch_due()
        except SyncAlreadyRunning:
            pass
        else:  # pragma: no cover - explicit failure path
            raise AssertionError("dispatch_due() must not overlap")


# ------------------------------------------------------------------- lifecycle


def test_the_application_does_not_sync_at_startup(engine: Engine) -> None:
    stage(engine)
    client, transport = client_for(engine)

    with client:
        pass

    assert transport.calls == []
    assert {row["status"] for row in outbox_rows(engine)} == {"pending"}


def test_api_never_requires_google_credentials_for_a_status_read(
    engine: Engine,
) -> None:
    client, transport = client_for(engine, transport=FakeTransport())

    with client as http:
        response = http.get("/api/v1/sync/status")

    assert response.status_code == 200
    assert transport.calls == []
