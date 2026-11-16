"""Tests for the local Google Sheets setup command line."""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import cli as cli_module
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.preflight import (
    PreflightResult,
    SpreadsheetPreflightService,
)

SPREADSHEET_ID = "test_spreadsheet_id_1234567890"


class ReadyTransport:
    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        from personal_job_finder_api.integrations.google_sheets.config import (
            REQUIRED_TABS,
        )

        return {"sheets": [{"properties": {"title": title}} for title in REQUIRED_TABS]}

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        from personal_job_finder_api.integrations.google_sheets.config import (
            REQUIRED_TABS,
        )

        return {
            "valueRanges": [
                {"values": [list(REQUIRED_TABS[value_range.split("!", 1)[0]])]}
                for value_range in ranges
            ]
        }

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        raise AssertionError("preflight must not write values")

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise AssertionError("preflight must not modify the spreadsheet")


class StubFoundation:
    def __init__(self, result: PreflightResult) -> None:
        self._result = result

    def check(self) -> PreflightResult:
        return self._result

    def initialize(self) -> PreflightResult:
        return self._result


def _ready_result() -> PreflightResult:
    return SpreadsheetPreflightService(ReadyTransport()).check(SPREADSHEET_ID)


def test_check_reports_readiness_without_printing_the_spreadsheet_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "build_google_sheets_foundation",
        lambda settings: StubFoundation(_ready_result()),
    )

    exit_code = cli_module.main(["check"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Spreadsheet is ready." in output.out
    assert SPREADSHEET_ID not in output.out
    assert SPREADSHEET_ID not in output.err


def test_missing_credentials_are_reported_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(settings: object) -> StubFoundation:
        raise GoogleSheetsError(
            "credentials_missing",
            "No Google account is connected.",
            reconnect_required=True,
        )

    monkeypatch.setattr(cli_module, "build_google_sheets_foundation", fail)

    exit_code = cli_module.main(["check"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "credentials_missing" in output.err


def test_disconnect_removes_the_stored_credential(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deleted: list[bool] = []

    class Store:
        def delete(self) -> None:
            deleted.append(True)

    monkeypatch.setattr(cli_module, "build_credential_store", lambda settings: Store())

    exit_code = cli_module.main(["disconnect"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert deleted == [True]
    assert "removed" in output.out


def test_unexpected_failures_print_no_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(settings: object) -> StubFoundation:
        raise RuntimeError("provider body with token=super-secret")

    monkeypatch.setattr(cli_module, "build_google_sheets_foundation", fail)

    exit_code = cli_module.main(["check"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "super-secret" not in output.err
    assert "Traceback" not in output.err
    assert "google_sheets_error" in output.err


def test_sync_reports_counters_without_leaking_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from personal_job_finder_api.sync.dispatcher import SyncOutcome

    class StubConfig:
        spreadsheet_id = SPREADSHEET_ID
        batch_size = 25
        max_batch_ranges = 100

    class StubFoundation:
        config = StubConfig()
        transport = object()

    class StubDispatcher:
        def __init__(self, engine, **_kwargs) -> None:
            pass

        def recover(self) -> int:
            return 0

        def dispatch_due(self) -> SyncOutcome:
            return SyncOutcome(synced=3, failed=0, blocked=0, remaining=0)

    class StubEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: StubFoundation()
    )
    monkeypatch.setattr(cli_module, "create_database_engine", lambda settings: StubEngine())
    monkeypatch.setattr(cli_module, "SyncDispatcher", StubDispatcher)

    exit_code = cli_module.main(["sync"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "synced=3" in output.out
    assert SPREADSHEET_ID not in output.out


def test_sync_returns_a_nonzero_exit_code_when_work_is_left_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from personal_job_finder_api.sync.dispatcher import SyncOutcome

    class StubConfig:
        spreadsheet_id = SPREADSHEET_ID
        batch_size = 25
        max_batch_ranges = 100

    class StubFoundation:
        config = StubConfig()
        transport = object()

    class StubDispatcher:
        def __init__(self, engine, **_kwargs) -> None:
            pass

        def recover(self) -> int:
            return 0

        def dispatch_due(self) -> SyncOutcome:
            return SyncOutcome(synced=0, failed=1, blocked=1, remaining=1)

    class StubEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: StubFoundation()
    )
    monkeypatch.setattr(cli_module, "create_database_engine", lambda settings: StubEngine())
    monkeypatch.setattr(cli_module, "SyncDispatcher", StubDispatcher)

    exit_code = cli_module.main(["sync"])

    assert exit_code == 1


def test_auth_failure_returns_a_nonzero_exit_code_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingAuthorizer:
        def authorize(self) -> None:
            raise GoogleSheetsError(
                "oauth_state_mismatch",
                "The Google authorization response did not match this request.",
                reconnect_required=True,
            )

    monkeypatch.setattr(
        cli_module,
        "build_google_oauth_authorizer",
        lambda settings: FailingAuthorizer(),
    )

    exit_code = cli_module.main(["auth"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "oauth_state_mismatch" in output.err
    assert "Traceback" not in output.err
