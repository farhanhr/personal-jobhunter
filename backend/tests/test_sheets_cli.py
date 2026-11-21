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


@pytest.fixture(autouse=True)
def _stub_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real database from a CLI test.

    ``sync``/``recover``/``requeue`` prepare the schema before touching the outbox.
    A test that exercised the real function would create files under the user's
    data directory, so the default is a no-op; the tests that care patch it again.
    """

    monkeypatch.setattr(cli_module, "run_migrations", lambda _url: None)


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

    class StubOutcome:
        changed = False

        @staticmethod
        def message() -> str:
            return "Workspace already matches the expected structure."

    class StubFoundation:
        config = StubConfig()
        transport = object()

        @staticmethod
        def repair_workspace(*, force: bool = False) -> StubOutcome:
            assert force is False
            return StubOutcome()

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

    class StubOutcome:
        changed = False

        @staticmethod
        def message() -> str:
            return "Workspace already matches the expected structure."

    class StubFoundation:
        config = StubConfig()
        transport = object()

        @staticmethod
        def repair_workspace(*, force: bool = False) -> StubOutcome:
            assert force is False
            return StubOutcome()

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


class StubDashboardFoundation:
    def __init__(
        self,
        *,
        result: Any = None,
        error: Exception | None = None,
        removal: str = "Deleted the retired Dashboard cards tab.",
        removal_error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self._removal = removal
        self._removal_error = removal_error
        self.dashboard_calls = 0
        self.force_calls: list[bool] = []
        self.removal_calls = 0

    def check(self) -> PreflightResult:
        raise AssertionError("this command must not run preflight")

    def initialize(self) -> PreflightResult:
        raise AssertionError("this command must not run canonical init")

    def initialize_dashboard(self, *, force: bool = False) -> Any:
        """The metric board is ``metrics-init``."""

        self.dashboard_calls += 1
        self.force_calls.append(force)
        if self._error is not None:
            raise self._error
        return self._result

    def remove_dashboard(self) -> str:
        """The retired cards tab is ``dashboard-delete``."""

        self.removal_calls += 1
        if self._removal_error is not None:
            raise self._removal_error
        return self._removal


def test_metrics_init_is_an_explicit_command() -> None:
    parser = cli_module._build_parser()

    arguments = parser.parse_args(["metrics-init"])

    assert arguments.command == "metrics-init"


def test_dashboard_init_is_no_longer_a_command() -> None:
    """The cards tab is retired, so nothing may recreate it."""

    parser = cli_module._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["dashboard-init"])


def test_dashboard_delete_is_an_explicit_command() -> None:
    parser = cli_module._build_parser()

    arguments = parser.parse_args(["dashboard-delete"])

    assert arguments.command == "dashboard-delete"


def test_dashboard_delete_prints_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    foundation = StubDashboardFoundation()
    monkeypatch.setattr(
        cli_module,
        "build_google_sheets_foundation",
        lambda settings: foundation,
    )

    exit_code = cli_module.main(["dashboard-delete"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Deleted the retired Dashboard cards tab." in output.out
    assert foundation.removal_calls == 1
    assert SPREADSHEET_ID not in output.out
    assert SPREADSHEET_ID not in output.err


def test_dashboard_delete_reports_a_refusal_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    foundation = StubDashboardFoundation(
        removal_error=GoogleSheetsError(
            "dashboard_version_unsupported",
            "The Dashboard tab carries a different cards version.",
        )
    )
    monkeypatch.setattr(
        cli_module,
        "build_google_sheets_foundation",
        lambda settings: foundation,
    )

    exit_code = cli_module.main(["dashboard-delete"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "dashboard_version_unsupported" in output.err
    assert SPREADSHEET_ID not in output.out
    assert SPREADSHEET_ID not in output.err


def test_metrics_init_prints_the_success_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from personal_job_finder_api.integrations.google_sheets.dashboard import (
        MetricsInitResult,
        MetricsInitState,
    )

    foundation = StubDashboardFoundation(
        result=MetricsInitResult(
            state=MetricsInitState.CREATED,
            message="Metrics board created and initialized.",
        )
    )
    monkeypatch.setattr(
        cli_module,
        "build_google_sheets_foundation",
        lambda settings: foundation,
    )

    exit_code = cli_module.main(["metrics-init"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Metrics board created and initialized." in output.out
    assert foundation.dashboard_calls == 1
    assert foundation.force_calls == [False]
    assert SPREADSHEET_ID not in output.out
    assert SPREADSHEET_ID not in output.err


def test_metrics_init_passes_force_through(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After the header moves, an existing board needs its formulas rewritten."""

    from personal_job_finder_api.integrations.google_sheets.dashboard import (
        MetricsInitResult,
        MetricsInitState,
    )

    foundation = StubDashboardFoundation(
        result=MetricsInitResult(
            state=MetricsInitState.INITIALIZED,
            message="Metrics board already initialized.",
        )
    )
    monkeypatch.setattr(
        cli_module,
        "build_google_sheets_foundation",
        lambda settings: foundation,
    )

    exit_code = cli_module.main(["metrics-init", "--force"])

    capsys.readouterr()
    assert exit_code == 0
    assert foundation.force_calls == [True]


def test_canonical_init_never_calls_the_dashboard_initializer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    foundation = StubDashboardFoundation()
    monkeypatch.setattr(
        cli_module,
        "build_google_sheets_foundation",
        lambda settings: _CanonicalInitFoundation(foundation),
    )

    exit_code = cli_module.main(["init"])

    capsys.readouterr()
    assert exit_code == 0
    assert foundation.dashboard_calls == 0


class _CanonicalInitFoundation:
    """A foundation whose canonical init works and whose dashboard path must not run."""

    def __init__(self, dashboard: StubDashboardFoundation) -> None:
        self._dashboard = dashboard

    def initialize(self) -> PreflightResult:
        return _ready_result()

    def initialize_dashboard(self, *, force: bool = False) -> Any:
        return self._dashboard.initialize_dashboard(force=force)


def test_sync_prepares_the_local_schema_before_dispatching(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: a fresh data directory made `sync` fail with "no such table"."""

    from personal_job_finder_api.sync.dispatcher import SyncOutcome

    prepared: list[str] = []

    class StubConfig:
        spreadsheet_id = SPREADSHEET_ID
        batch_size = 25
        max_batch_ranges = 100

    class StubOutcome:
        changed = False

        @staticmethod
        def message() -> str:
            return "Workspace already matches the expected structure."

    class StubFoundation:
        config = StubConfig()
        transport = object()

        @staticmethod
        def repair_workspace(*, force: bool = False) -> StubOutcome:
            assert force is False
            return StubOutcome()

    class StubDispatcher:
        def __init__(self, engine, **_kwargs) -> None:
            pass

        def dispatch_due(self) -> SyncOutcome:
            return SyncOutcome(synced=0, failed=0, blocked=0, remaining=0)

    class StubEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "run_migrations", prepared.append)
    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: StubFoundation()
    )
    monkeypatch.setattr(cli_module, "create_database_engine", lambda settings: StubEngine())
    monkeypatch.setattr(cli_module, "SyncDispatcher", StubDispatcher)

    exit_code = cli_module.main(["sync"])

    assert exit_code == 0
    assert len(prepared) == 1
    assert prepared[0].endswith(".sqlite3")


def test_a_database_failure_is_reported_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    def failing(_url: str) -> None:
        raise SQLAlchemyError("no such table: sync_outbox")

    class StubEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "run_migrations", failing)
    monkeypatch.setattr(cli_module, "create_database_engine", lambda settings: StubEngine())

    exit_code = cli_module.main(["sync"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "database_unavailable" in output.err
    assert "Traceback" not in output.err
    assert "sync_outbox" not in output.err


def test_the_real_foundation_exposes_the_dashboard_commands() -> None:
    """Regression: the CLI called a method the foundation did not have.

    The stubbed CLI tests could not catch it, because they replace the foundation
    entirely. This pins the real interface the CLI depends on.
    """

    from personal_job_finder_api.integrations.google_sheets.service import (
        GoogleSheetsFoundation,
    )

    assert hasattr(GoogleSheetsFoundation, "remove_dashboard")
    assert hasattr(GoogleSheetsFoundation, "initialize_dashboard")
    assert hasattr(GoogleSheetsFoundation, "repair_workspace")
    assert not hasattr(GoogleSheetsFoundation, "initialize_workspace")


def test_dashboard_delete_and_metrics_init_target_different_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One command per tab, so neither can silently act on the other."""

    calls: list[str] = []

    class RecordingFoundation:
        def remove_dashboard(self):
            calls.append("delete")
            return "deleted"

        def initialize_dashboard(self, *, force: bool = False):
            calls.append("metrics")

            class Result:
                message = "metrics"

            return Result()

    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: RecordingFoundation()
    )

    assert cli_module.main(["dashboard-delete"]) == 0
    assert cli_module.main(["metrics-init"]) == 0
    assert calls == ["delete", "metrics"]


# --------------------------------------------------------------- workspace repair


class _RepairOutcome:
    def __init__(self, *, changed: bool, message: str) -> None:
        self.changed = changed
        self._message = message

    def message(self) -> str:
        return self._message


def test_workspace_repair_prints_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    class StubFoundation:
        @staticmethod
        def repair_workspace(*, force: bool = False) -> _RepairOutcome:
            calls.append(force)
            return _RepairOutcome(changed=True, message="Repaired: created the workspace table")

    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: StubFoundation()
    )

    exit_code = cli_module.main(["workspace-repair"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "created the workspace table" in output.out
    assert calls == [False]


def test_workspace_repair_passes_force_through(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    class StubFoundation:
        @staticmethod
        def repair_workspace(*, force: bool = False) -> _RepairOutcome:
            calls.append(force)
            return _RepairOutcome(changed=True, message="Repaired: updated the workspace table")

    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: StubFoundation()
    )

    assert cli_module.main(["workspace-repair", "--force"]) == 0
    assert calls == [True]


def test_sync_runs_repair_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cleared header would send every projection to the wrong column."""

    from personal_job_finder_api.sync.dispatcher import SyncOutcome

    order: list[str] = []

    class StubConfig:
        spreadsheet_id = SPREADSHEET_ID
        batch_size = 25
        max_batch_ranges = 100

    class StubFoundation:
        config = StubConfig()
        transport = object()

        @staticmethod
        def repair_workspace(*, force: bool = False) -> _RepairOutcome:
            order.append("repair")
            return _RepairOutcome(changed=True, message="Repaired: restored the header row")

    class StubDispatcher:
        def __init__(self, engine, **_kwargs) -> None:
            pass

        def dispatch_due(self) -> SyncOutcome:
            order.append("dispatch")
            return SyncOutcome(synced=0, failed=0, blocked=0, remaining=0)

    class StubEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(
        cli_module, "build_google_sheets_foundation", lambda settings: StubFoundation()
    )
    monkeypatch.setattr(cli_module, "run_migrations", lambda url: None)
    monkeypatch.setattr(cli_module, "create_database_engine", lambda settings: StubEngine())
    monkeypatch.setattr(cli_module, "SyncDispatcher", StubDispatcher)

    assert cli_module.main(["sync"]) == 0
    assert order == ["repair", "dispatch"]
    assert "Workspace repair: Repaired: restored the header row" in capsys.readouterr().out
