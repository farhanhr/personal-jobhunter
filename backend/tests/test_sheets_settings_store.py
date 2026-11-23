"""Offline tests for persisted spreadsheet selection and precedence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    GoogleSheetsSettingsStore,
    resolve_effective_spreadsheet,
    workspace_ready,
    workspace_ready_for,
)

SYNTHETIC_ID = "1SyntheticSheetId0123456789abcdefXYZ"
OTHER_ID = "1OtherSyntheticSheetId0123456789XYZ"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def test_saved_value_wins_over_environment_and_reports_source() -> None:
    effective = resolve_effective_spreadsheet(saved_id=SYNTHETIC_ID, environment_id=OTHER_ID)

    assert effective.spreadsheet_id == SYNTHETIC_ID
    assert effective.source == "ui"


def test_environment_is_used_when_nothing_is_saved() -> None:
    effective = resolve_effective_spreadsheet(saved_id=None, environment_id=OTHER_ID)

    assert effective.source == "environment"


def test_no_configuration_reports_none() -> None:
    effective = resolve_effective_spreadsheet(saved_id=None, environment_id=None)

    assert effective.spreadsheet_id is None
    assert effective.source == "none"


def test_store_round_trip_is_a_single_row(engine: Engine) -> None:
    store = GoogleSheetsSettingsStore(engine, clock=lambda: NOW)

    assert store.load_saved_spreadsheet_id() is None
    store.save_spreadsheet_id(SYNTHETIC_ID)
    store.save_spreadsheet_id(OTHER_ID)

    assert store.load_saved_spreadsheet_id() == OTHER_ID

    store.clear()
    assert store.load_saved_spreadsheet_id() is None


def test_readiness_is_persisted_and_can_be_cleared(engine: Engine) -> None:
    store = GoogleSheetsSettingsStore(engine, clock=lambda: NOW)

    store.save_spreadsheet_id(SYNTHETIC_ID, configured_via="cli")
    assert workspace_ready(engine) is False

    store.save_spreadsheet_id(
        SYNTHETIC_ID,
        workspace_ready_at=NOW,
        configured_via="cli",
    )
    state = store.load_state()
    assert state is not None
    assert state.spreadsheet_id == SYNTHETIC_ID
    assert state.workspace_ready_at == NOW
    assert state.configured_via == "cli"
    assert workspace_ready(engine) is True

    store.save_spreadsheet_id(OTHER_ID, configured_via="ui")
    state = store.load_state()
    assert state is not None
    assert state.workspace_ready_at is None
    assert workspace_ready(engine) is False


def test_saved_source_is_reported_after_setup() -> None:
    effective = resolve_effective_spreadsheet(
        saved_id=SYNTHETIC_ID,
        environment_id=OTHER_ID,
        saved_source="cli",
    )

    assert effective.source == "cli"


def test_an_environment_setup_follows_a_changed_env_value() -> None:
    """The link lives in `.env` for a CLI-only user, so editing it must take effect."""

    effective = resolve_effective_spreadsheet(
        saved_id=SYNTHETIC_ID,
        environment_id=OTHER_ID,
        saved_source="environment",
    )

    assert effective.spreadsheet_id == OTHER_ID
    assert effective.source == "environment"


def test_a_ui_or_cli_value_still_wins_over_env() -> None:
    for source in ("ui", "cli"):
        effective = resolve_effective_spreadsheet(
            saved_id=SYNTHETIC_ID,
            environment_id=OTHER_ID,
            saved_source=source,
        )

        assert effective.spreadsheet_id == SYNTHETIC_ID
        assert effective.source == source


def test_a_changed_env_sheet_requires_setup_again(engine: Engine) -> None:
    store = GoogleSheetsSettingsStore(engine, clock=lambda: NOW)
    store.save_spreadsheet_id(SYNTHETIC_ID, workspace_ready_at=NOW, configured_via="environment")

    unchanged = Settings(google_sheets_spreadsheet_id=SYNTHETIC_ID)
    changed = Settings(google_sheets_spreadsheet_id=OTHER_ID)
    removed = Settings(google_sheets_spreadsheet_id=None, google_sheets_spreadsheet_url=None)

    assert workspace_ready_for(engine, unchanged) is True
    assert workspace_ready_for(engine, changed) is False
    assert workspace_ready_for(engine, removed) is True


def test_a_changed_env_sheet_does_not_affect_a_ui_setup(engine: Engine) -> None:
    store = GoogleSheetsSettingsStore(engine, clock=lambda: NOW)
    store.save_spreadsheet_id(SYNTHETIC_ID, workspace_ready_at=NOW, configured_via="ui")

    assert workspace_ready_for(engine, Settings(google_sheets_spreadsheet_id=OTHER_ID)) is True
