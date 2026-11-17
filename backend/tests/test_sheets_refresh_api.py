"""Tests for ``POST /api/v1/sheets/refresh``.

The endpoint is a thin, manual wrapper: fake transport only, sanitized errors,
no scheduler, and no Google request when the M4B3 status route is read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.main import create_app
from personal_job_finder_api.sync.coordination import sheets_io_guard

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
SPREADSHEET_ID = "test_spreadsheet_id_1234567890"
ORIGIN = {"Origin": "http://127.0.0.1:5173"}


class FakeTransport:
    def __init__(self) -> None:
        self.tabs: dict[str, list[list[str]]] = {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
        self.fail_with: Exception | None = None
        self.calls: list[str] = []

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.calls.append("get_spreadsheet")
        return {"sheets": [{"properties": {"title": title}} for title in self.tabs]}

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.calls.append("batch_get_values")
        if self.fail_with is not None:
            raise self.fail_with
        value_ranges: list[dict[str, Any]] = []
        for requested in ranges:
            tab = requested.split("!", 1)[0]
            rows = self.tabs.get(tab, [])
            if requested.endswith("!1:1"):
                rows = rows[:1]
            value_ranges.append({"values": [list(row) for row in rows]} if rows else {})
        return {"valueRanges": value_ranges}


def row(headers: tuple[str, ...], **values: str) -> list[str]:
    cells = [values.get(header, "") for header in headers]
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def opportunity(**values: str) -> list[str]:
    defaults = {
        "opportunity_id": "opp_1",
        "company_name": "Acme Example",
        "job_title_original": "Backend Engineer",
        "role_match_type": "canonical_title",
        "location_status": "eligible",
        "work_mode": "onsite",
        "remote_scope": "not_applicable",
        "availability_status": "unknown",
    }
    defaults.update(values)
    return row(OPPORTUNITY_HEADERS, **defaults)


def client(
    engine: Engine,
    transport: FakeTransport,
    *,
    spreadsheet_id: str | None = SPREADSHEET_ID,
) -> TestClient:
    app = create_app(
        engine=engine,
        clock=lambda: NOW,
        sync_transport=transport,
        sync_spreadsheet_id=spreadsheet_id,
    )
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_refresh_returns_counts_and_never_caches(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity())
    transport.tabs["Job_Sources"].append(
        row(
            JOB_SOURCE_HEADERS,
            source_id="src_1",
            opportunity_id="opp_1",
            source_type="greenhouse",
            observation_status="seen",
        )
    )

    with client(engine, transport) as http:
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["outcome"] == "succeeded"
    assert body["counts"] == {"opportunities": 1, "job_sources": 1}
    assert body["last_full_refresh_at_utc"].startswith("2026-11-17T03:00:00")


def test_an_unconfigured_spreadsheet_is_rejected_before_any_read(engine: Engine) -> None:
    transport = FakeTransport()

    with client(engine, transport, spreadsheet_id="") as http:
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sheets_refresh_configuration_invalid"
    assert transport.calls == []


def test_a_schema_mismatch_returns_a_stable_sanitized_error(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"][0][1] = "company"

    with client(engine, transport) as http:
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "sheets_refresh_schema_invalid"
    assert error["details"]["reason"] == "schema_mismatch"


def test_invalid_data_returns_a_stable_sanitized_error(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity(location_status="maybe"))

    with client(engine, transport) as http:
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "sheets_refresh_data_invalid"
    assert error["details"]["reason"] == "snapshot_invalid_enum"


def test_an_unexpected_runtime_failure_returns_a_sanitized_error(engine: Engine) -> None:
    class RaisingTransport(FakeTransport):
        def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
            raise RuntimeError("provider secret must not escape")

    with client(engine, RaisingTransport()) as http:
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "sheets_refresh_unavailable"
    assert error["details"]["reason"] == "refresh_unexpected_error"
    assert "provider secret" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_a_google_failure_does_not_leak_provider_text(engine: Engine) -> None:
    transport = FakeTransport()
    transport.fail_with = GoogleSheetsError(
        "permission_denied",
        "raw provider body with a token",
    )

    with client(engine, transport) as http:
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "sheets_refresh_unavailable"
    assert "token" not in response.text
    assert "token" not in str(response.json())


def test_a_concurrent_refresh_returns_conflict(engine: Engine) -> None:
    transport = FakeTransport()

    with client(engine, transport) as http, sheets_io_guard():
        response = http.post("/api/v1/sheets/refresh", headers=ORIGIN)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "sheets_refresh_in_progress"
    assert transport.calls == []


def test_refresh_requires_a_local_origin(engine: Engine) -> None:
    with client(engine, FakeTransport()) as http:
        response = http.post(
            "/api/v1/sheets/refresh",
            headers={"Origin": "https://evil.test"},
        )

    assert response.status_code == 403
