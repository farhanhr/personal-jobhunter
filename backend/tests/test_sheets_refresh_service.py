"""Tests for the explicit Sheets -> SQLite refresh service.

Every test injects a fake transport; no Google credential or socket is used. The
suite covers the successful path, short rows, blank tracker cells, missing tabs
and headers, validation failures, failure-state sanitization, and serialization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import opportunities_read_model
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    REQUIRED_TABS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.repositories.read_model import ReadModelRepository
from personal_job_finder_api.sync.coordination import sheets_io_guard
from personal_job_finder_api.sync.dispatcher import dispatch_guard
from personal_job_finder_api.sync.refresh import (
    RefreshAlreadyRunning,
    RefreshSchemaError,
    RefreshSnapshotError,
    RefreshUnavailableError,
    SheetsRefreshService,
)

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
NOW_ISO = "2026-11-17T03:00:00.000000Z"
SPREADSHEET_ID = "test_spreadsheet_id_1234567890"


class FakeTransport:
    """The subset of the Sheets API used by preflight and refresh."""

    def __init__(self, tabs: dict[str, list[list[str]]] | None = None) -> None:
        self.tabs = tabs or {
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

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:  # pragma: no cover - refresh never writes
        raise AssertionError("refresh must not write to the spreadsheet")

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:  # pragma: no cover - refresh never writes
        raise AssertionError("refresh must not change spreadsheet structure")


def sheet_row(headers: tuple[str, ...], **values: str) -> list[str]:
    cells = [values.get(header, "") for header in headers]
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def opportunity_row(**values: str) -> list[str]:
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
    return sheet_row(OPPORTUNITY_HEADERS, **defaults)


def source_row(**values: str) -> list[str]:
    defaults = {
        "source_id": "src_1",
        "opportunity_id": "opp_1",
        "source_type": "greenhouse",
        "source_url": "https://boards.greenhouse.io/acme/jobs/1001",
        "external_source_id": "1001",
        "captured_at_utc": NOW_ISO,
        "provenance_method": "ats_api",
        "observation_status": "seen",
    }
    defaults.update(values)
    return sheet_row(JOB_SOURCE_HEADERS, **defaults)


def service(engine: Engine, transport: FakeTransport) -> SheetsRefreshService:
    return SheetsRefreshService(
        engine,
        transport_factory=lambda: transport,
        spreadsheet_id=lambda: SPREADSHEET_ID,
        clock=lambda: NOW,
    )


def state(engine: Engine) -> dict[str, Any] | None:
    return ReadModelRepository(engine).get_sync_state()


def counts(engine: Engine) -> dict[str, int]:
    return ReadModelRepository(engine).snapshot_counts()


def test_refresh_publishes_both_tabs_and_records_success(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row())
    transport.tabs["Job_Sources"].append(source_row())

    result = service(engine, transport).refresh()

    assert result.counts == {"opportunities": 1, "job_sources": 1}
    assert result.last_full_refresh_at_utc == NOW
    assert counts(engine) == {"opportunities": 1, "job_sources": 1}
    stored = state(engine)
    assert stored is not None
    assert stored["last_full_refresh_at_utc"] == NOW_ISO
    assert stored["last_refresh_outcome"] == "succeeded"
    assert stored["last_refresh_error_code"] is None

    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(
                    opportunities_read_model.c.company_name,
                    opportunities_read_model.c.user_status,
                    opportunities_read_model.c.job_type,
                    opportunities_read_model.c.notes,
                )
            )
            .mappings()
            .one()
        )
    assert row["company_name"] == "Acme Example"
    # No default and no tracker value was invented for an empty canonical cell.
    assert row["user_status"] is None
    assert row["job_type"] is None
    assert row["notes"] is None


def test_empty_tabs_with_only_headers_are_a_valid_empty_snapshot(engine: Engine) -> None:
    result = service(engine, FakeTransport()).refresh()

    assert result.counts == {"opportunities": 0, "job_sources": 0}
    assert counts(engine) == {"opportunities": 0, "job_sources": 0}
    assert state(engine)["last_refresh_outcome"] == "succeeded"


def test_a_missing_contacts_tab_is_not_a_refresh_dependency(engine: Engine) -> None:
    transport = FakeTransport()
    assert "Contacts" not in transport.tabs

    result = service(engine, transport).refresh()

    assert result.counts == {"opportunities": 0, "job_sources": 0}


def test_short_rows_are_padded_and_keep_explicit_tracker_blanks(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(
        opportunity_row(job_type="", application_status="", notes="")
    )

    service(engine, transport).refresh()

    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(
                    opportunities_read_model.c.job_type,
                    opportunities_read_model.c.application_status,
                    opportunities_read_model.c.notes,
                )
            )
            .mappings()
            .one()
        )
    assert row["job_type"] is None
    assert row["application_status"] is None
    assert row["notes"] is None


def test_a_missing_tab_rejects_the_refresh_and_keeps_the_last_snapshot(
    engine: Engine,
) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row())
    transport.tabs["Job_Sources"].append(source_row())
    refresh = service(engine, transport)
    refresh.refresh()

    del transport.tabs["Job_Sources"]

    with pytest.raises(RefreshSchemaError) as rejected:
        refresh.refresh()

    assert rejected.value.code == "missing_tab"
    assert counts(engine) == {"opportunities": 1, "job_sources": 1}
    stored = state(engine)
    assert stored is not None
    assert stored["last_full_refresh_at_utc"] == NOW_ISO
    assert stored["last_refresh_outcome"] == "failed"
    assert stored["last_refresh_error_code"] == "missing_tab"


def test_a_header_mismatch_rejects_the_refresh(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"][0][1] = "company"

    with pytest.raises(RefreshSchemaError) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "schema_mismatch"
    assert counts(engine) == {"opportunities": 0, "job_sources": 0}


def test_a_data_row_without_a_stable_identity_rejects_the_snapshot(
    engine: Engine,
) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row(opportunity_id=""))

    with pytest.raises(RefreshSnapshotError) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "snapshot_missing_identity"
    assert counts(engine) == {"opportunities": 0, "job_sources": 0}
    assert state(engine)["last_refresh_error_code"] == "snapshot_missing_identity"


def test_duplicate_identities_reject_the_snapshot(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].extend([opportunity_row(), opportunity_row()])

    with pytest.raises(RefreshSnapshotError) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "snapshot_duplicate_identity"


def test_an_orphan_job_source_rejects_the_snapshot(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row())
    transport.tabs["Job_Sources"].append(source_row(opportunity_id="opp_missing"))

    with pytest.raises(RefreshSnapshotError) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "snapshot_unknown_reference"
    assert counts(engine) == {"opportunities": 0, "job_sources": 0}


def test_an_unaccepted_enum_value_rejects_the_snapshot(engine: Engine) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row(location_status="maybe"))

    with pytest.raises(RefreshSnapshotError) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "snapshot_invalid_enum"
    assert counts(engine) == {"opportunities": 0, "job_sources": 0}


def test_a_google_failure_is_sanitized_and_keeps_the_last_snapshot(
    engine: Engine,
) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row())
    refresh = service(engine, transport)
    refresh.refresh()

    transport.fail_with = GoogleSheetsError(
        "permission_denied",
        "provider body contains a credential that must never be stored",
    )

    with pytest.raises(RefreshUnavailableError) as rejected:
        refresh.refresh()

    assert rejected.value.code == "permission_denied"
    assert "credential" not in str(rejected.value)
    stored = state(engine)
    assert stored is not None
    assert stored["last_refresh_error_code"] == "permission_denied"
    assert "credential" not in str(stored)
    assert counts(engine) == {"opportunities": 1, "job_sources": 0}


def test_a_concurrent_refresh_is_refused_without_reading_the_sheet(
    engine: Engine,
) -> None:
    transport = FakeTransport()
    with sheets_io_guard(), pytest.raises(RefreshAlreadyRunning) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "refresh_in_progress"
    assert transport.calls == []
    assert state(engine) is None


def test_refresh_and_dispatch_share_one_process_lock(engine: Engine) -> None:
    transport = FakeTransport()
    with dispatch_guard(), pytest.raises(RefreshAlreadyRunning) as rejected:
        service(engine, transport).refresh()

    assert rejected.value.code == "refresh_in_progress"
    assert transport.calls == []


def test_refresh_never_initializes_or_writes_the_spreadsheet(engine: Engine) -> None:
    transport = FakeTransport()
    service(engine, transport).refresh()

    assert "batch_update_values" not in transport.calls
    assert "batch_update_spreadsheet" not in transport.calls
    assert set(REQUIRED_TABS) == {"Opportunities", "Job_Sources"}


def test_a_validation_failure_keeps_the_previous_snapshot_and_timestamp(
    engine: Engine,
) -> None:
    transport = FakeTransport()
    transport.tabs["Opportunities"].append(opportunity_row())
    service(engine, transport).refresh()

    later = NOW + timedelta(hours=1)
    transport.tabs["Opportunities"].append(
        opportunity_row(opportunity_id="opp_2", location_status="maybe")
    )
    later_service = SheetsRefreshService(
        engine,
        transport_factory=lambda: transport,
        spreadsheet_id=lambda: SPREADSHEET_ID,
        clock=lambda: later,
    )

    with pytest.raises(RefreshSnapshotError) as rejected:
        later_service.refresh()

    assert rejected.value.code == "snapshot_invalid_enum"
    assert counts(engine) == {"opportunities": 1, "job_sources": 0}
    stored = state(engine)
    assert stored is not None
    assert stored["last_full_refresh_at_utc"] == NOW_ISO
    assert stored["last_refresh_outcome"] == "failed"
    assert stored["last_refresh_error_code"] == "snapshot_invalid_enum"


def test_an_unexpected_runtime_failure_is_recorded_and_sanitized(
    engine: Engine,
) -> None:
    def fail_factory() -> Any:
        raise RuntimeError("provider secret must not escape")

    refresh = SheetsRefreshService(
        engine,
        transport_factory=fail_factory,
        spreadsheet_id=lambda: SPREADSHEET_ID,
        clock=lambda: NOW,
    )

    with pytest.raises(RefreshUnavailableError) as rejected:
        refresh.refresh()

    assert rejected.value.code == "refresh_unexpected_error"
    assert "provider secret" not in str(rejected.value)
    stored = state(engine)
    assert stored is not None
    assert stored["last_refresh_outcome"] == "failed"
    assert stored["last_refresh_error_code"] == "refresh_unexpected_error"
