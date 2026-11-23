"""Offline tests for the shared setup orchestration (fake Sheets foundation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets import orchestration
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    GoogleSheetsSettingsStore,
)
from personal_job_finder_api.integrations.google_sheets.workspace_adoption import (
    WorkspaceInspection,
    WorkspaceKind,
)
from personal_job_finder_api.sync.coordination import SheetsOperationBusy

SYNTHETIC_ID = "1SyntheticSheetId0123456789abcdefXYZ"
OTHER_ID = "1OtherSyntheticSheetId0123456789XYZ"
SYNTHETIC_URL = f"https://docs.google.com/spreadsheets/d/{SYNTHETIC_ID}/edit"
OTHER_URL = f"https://docs.google.com/spreadsheets/d/{OTHER_ID}/edit"
FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n"
EMAIL = "pjf-reader@pjf-test-project.iam.gserviceaccount.com"


class FakeFoundation:
    def __init__(
        self,
        *,
        check_error: GoogleSheetsError | None = None,
        repair_error: GoogleSheetsError | None = None,
    ) -> None:
        self.check_error = check_error
        self.repair_error = repair_error
        self.client = object()
        self.repaired = False
        self.dashboard_initialized = False

    def check(self):
        if self.check_error is not None:
            raise self.check_error

    def repair_workspace(self, *, force: bool = False):
        if self.repair_error is not None:
            raise self.repair_error
        self.repaired = True

    def initialize_dashboard(self, *, force: bool = False):
        self.dashboard_initialized = True


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    key_path = tmp_path / "credentials" / "google-service-account.json"
    monkeypatch.setattr(Settings, "google_service_account_file", property(lambda self: key_path))
    return Settings(data_dir=tmp_path)


def _install_key(settings: Settings) -> None:
    path = settings.google_service_account_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "pjf-test-project",
                "private_key_id": "k",
                "private_key": FAKE_KEY,
                "client_email": EMAIL,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )


def _use(
    monkeypatch: pytest.MonkeyPatch,
    foundation: FakeFoundation,
    *,
    inspection: WorkspaceInspection | None = None,
) -> None:
    monkeypatch.setattr(orchestration, "build_google_sheets_foundation", lambda s: foundation)
    monkeypatch.setattr(
        orchestration,
        "inspect_workspace",
        lambda client, spreadsheet_id: inspection or WorkspaceInspection(kind=WorkspaceKind.EMPTY),
    )


def test_missing_key_stops_at_credentials_stage(settings: Settings, engine: Engine) -> None:
    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.ready is False
    assert result.stage == "credentials"
    assert result.code == "credentials_missing"


def test_empty_target_is_initialized_and_persisted_as_ready(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    foundation = FakeFoundation()
    _use(monkeypatch, foundation)

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    state = GoogleSheetsSettingsStore(engine).load_state()
    assert result.ready is True
    assert result.workspace_ready is True
    assert result.adopted_existing is False
    assert result.service_account_email == EMAIL
    assert result.source == "ui"
    assert foundation.repaired is True
    assert foundation.dashboard_initialized is True
    assert state is not None
    assert state.spreadsheet_id == SYNTHETIC_ID
    assert state.workspace_ready_at is not None
    assert state.configured_via == "ui"


def test_compatible_target_is_adopted_and_refreshed(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    foundation = FakeFoundation()
    _use(
        monkeypatch,
        foundation,
        inspection=WorkspaceInspection(kind=WorkspaceKind.COMPATIBLE),
    )
    refreshed: list[str] = []

    def refresh(spreadsheet_id: str) -> dict[str, int]:
        refreshed.append(spreadsheet_id)
        return {"opportunities": 12, "job_sources": 3}

    result = orchestration.run_setup(
        settings,
        engine,
        spreadsheet_input=SYNTHETIC_URL,
        refresh=refresh,
    )

    assert result.ready is True
    assert result.adopted_existing is True
    assert result.counts == {"opportunities": 12, "job_sources": 3}
    assert refreshed == [SYNTHETIC_ID]


def test_legacy_header_target_is_repaired(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    foundation = FakeFoundation()
    _use(
        monkeypatch,
        foundation,
        inspection=WorkspaceInspection(
            kind=WorkspaceKind.COMPATIBLE,
            legacy_header=True,
            needs_repair=True,
        ),
    )

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.ready is True
    assert result.adopted_existing is True
    assert foundation.repaired is True


def test_incompatible_target_changes_nothing(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    GoogleSheetsSettingsStore(engine).save_spreadsheet_id(
        OTHER_ID, workspace_ready_at=None, configured_via="ui"
    )
    foundation = FakeFoundation()
    _use(
        monkeypatch,
        foundation,
        inspection=WorkspaceInspection(
            kind=WorkspaceKind.INCOMPATIBLE,
            reason="required_header_mismatch",
        ),
    )

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.ready is False
    assert result.stage == "workspace"
    assert result.code == "workspace_incompatible"
    assert "Nothing was changed" in result.preserved
    assert foundation.repaired is False
    assert foundation.dashboard_initialized is False
    assert GoogleSheetsSettingsStore(engine).load_saved_spreadsheet_id() == OTHER_ID


def test_permission_denied_names_the_service_account_for_sharing(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    _use(
        monkeypatch,
        FakeFoundation(
            check_error=GoogleSheetsError("permission_denied", "Google denied access."),
        ),
    )

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.ready is False
    assert result.stage == "access"
    assert result.code == "permission_denied"
    assert EMAIL in result.message
    assert GoogleSheetsSettingsStore(engine).load_saved_spreadsheet_id() is None


def test_schema_failure_is_reported_without_saving(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    _use(
        monkeypatch,
        FakeFoundation(repair_error=GoogleSheetsError("schema_mismatch", "Headers differ.")),
    )

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.stage == "schema"
    assert result.retry_safe is True
    assert "preserved" in result.preserved.lower()
    assert GoogleSheetsSettingsStore(engine).load_saved_spreadsheet_id() is None


def test_optional_metrics_failure_keeps_required_setup_ready(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)

    class MetricsFails(FakeFoundation):
        def initialize_dashboard(self, *, force: bool = False):
            raise GoogleSheetsError(
                "dashboard_metadata_incomplete", "Metrics could not be formatted."
            )

    _use(monkeypatch, MetricsFails())

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.ready is True
    assert result.optional_warning is not None
    assert "dashboard_metadata_incomplete" in result.optional_warning


def test_refresh_failure_keeps_workspace_ready_with_a_warning(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    _use(monkeypatch, FakeFoundation())

    def refresh(spreadsheet_id: str) -> dict[str, int]:
        raise GoogleSheetsError("permission_denied", "Google denied access.")

    result = orchestration.run_setup(
        settings,
        engine,
        spreadsheet_input=SYNTHETIC_URL,
        refresh=refresh,
    )

    state = GoogleSheetsSettingsStore(engine).load_state()
    assert result.ready is True
    assert result.workspace_ready is True
    assert result.refresh_warning is not None
    assert "permission_denied" in result.refresh_warning
    assert state is not None
    assert state.workspace_ready_at is not None


def test_busy_sheets_io_returns_a_retry_safe_failure(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    _use(monkeypatch, FakeFoundation())

    def busy(*, wait_seconds: float = 0.0) -> None:
        raise SheetsOperationBusy

    monkeypatch.setattr(orchestration, "acquire_sheets_io", busy)

    result = orchestration.run_setup(settings, engine, spreadsheet_input=SYNTHETIC_URL)

    assert result.ready is False
    assert result.stage == "workspace"
    assert result.code == "workspace_busy"
    assert result.retry_safe is True
    assert GoogleSheetsSettingsStore(engine).load_saved_spreadsheet_id() is None


def test_cli_setup_persists_the_cli_source(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    _use(monkeypatch, FakeFoundation())

    result = orchestration.run_setup(
        settings,
        engine,
        spreadsheet_input=SYNTHETIC_URL,
        configured_via="cli",
    )

    state = GoogleSheetsSettingsStore(engine).load_state()
    assert result.ready is True
    assert result.source == "cli"
    assert state is not None
    assert state.configured_via == "cli"


def test_setup_without_a_url_reuses_the_saved_spreadsheet(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    GoogleSheetsSettingsStore(engine).save_spreadsheet_id(
        OTHER_ID, workspace_ready_at=None, configured_via="ui"
    )
    foundation = FakeFoundation()
    _use(monkeypatch, foundation)

    result = orchestration.run_setup(settings, engine)

    assert result.ready is True
    assert result.source == "ui"
    assert GoogleSheetsSettingsStore(engine).load_saved_spreadsheet_id() == OTHER_ID


def test_malformed_url_stops_at_spreadsheet_stage(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_key(settings)
    _use(monkeypatch, FakeFoundation())

    result = orchestration.run_setup(settings, engine, spreadsheet_input="https://evil.test/x")

    assert result.stage == "spreadsheet"
    assert result.code == "spreadsheet_url_invalid"
