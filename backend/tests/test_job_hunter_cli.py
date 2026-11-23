"""Tests for the ``job-hunter`` entry point (root CLI)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_job_finder_api import cli as cli_module
from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.orchestration import SetupResult

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake-not-real\n-----END PRIVATE KEY-----\n"
EMAIL = "pjf-reader@pjf-test-project.iam.gserviceaccount.com"
SYNTHETIC_ID = "1SyntheticSheetId0123456789abcdefXYZ"
OTHER_SYNTHETIC_ID = "1OtherSyntheticSheetId0123456789XYZ"
SYNTHETIC_URL = f"https://docs.google.com/spreadsheets/d/{SYNTHETIC_ID}/edit"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _key_bytes() -> bytes:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "pjf-test-project",
            "private_key_id": "k",
            "private_key": FAKE_KEY,
            "client_email": EMAIL,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ).encode()


@pytest.fixture(autouse=True)
def _stub_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "run_migrations", lambda _url: None)

    class StubEngine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "create_database_engine", lambda settings: StubEngine())


@pytest.fixture(autouse=True)
def _assume_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default doctor tests to a migrated database unless the test opts out."""

    monkeypatch.setattr(cli_module, "_settings_table_missing", lambda engine: False)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    key_path = tmp_path / "credentials" / "google-service-account.json"
    monkeypatch.setattr(Settings, "google_service_account_file", property(lambda self: key_path))
    return Settings(data_dir=tmp_path)


def _ready(**overrides) -> SetupResult:
    values = {
        "ready": True,
        "stage": None,
        "code": "ready",
        "message": "Google Sheets is connected and ready.",
        "service_account_email": EMAIL,
        "source": "cli",
        "workspace_ready": True,
    }
    values.update(overrides)
    return SetupResult(**values)


def test_setup_with_key_file_and_url_persists_the_canonical_key(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    downloaded = tmp_path / "Downloads" / "key.json"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(_key_bytes())
    seen: list[tuple[str | None, str | None]] = []

    def fake_run_setup(_settings, _engine, **kwargs):
        seen.append((kwargs.get("spreadsheet_input"), kwargs.get("configured_via")))
        return _ready()

    monkeypatch.setattr(cli_module, "run_setup", fake_run_setup)

    exit_code = cli_module.main(
        ["setup", "--key-file", str(downloaded), "--spreadsheet-url", SYNTHETIC_URL]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert seen == [(SYNTHETIC_URL, "cli")]
    assert settings.google_service_account_file.exists()
    assert oct(settings.google_service_account_file.stat().st_mode)[-3:] == "600"
    assert EMAIL in output.out
    assert "PRIVATE KEY" not in output.out
    assert "PRIVATE KEY" not in output.err


def test_setup_rejects_an_invalid_key_without_running_setup(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    downloaded = tmp_path / "key.json"
    downloaded.write_text(json.dumps({"installed": {"client_id": "x"}}), encoding="utf-8")
    called: list[bool] = []
    monkeypatch.setattr(cli_module, "run_setup", lambda *a, **k: called.append(True))

    exit_code = cli_module.main(["setup", "--key-file", str(downloaded)])

    output = capsys.readouterr()
    assert exit_code == 1
    assert called == []
    assert "oauth_client_rejected" in output.err
    assert not settings.google_service_account_file.exists()


def test_setup_reports_a_missing_key_file(
    settings: Settings,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli_module.main(["setup", "--key-file", str(tmp_path / "absent.json")])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Could not read the key file" in output.err


def test_setup_without_flags_keeps_the_environment_behavior(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str | None, str | None]] = []

    def fake_run_setup(_settings, _engine, **kwargs):
        seen.append((kwargs.get("spreadsheet_input"), kwargs.get("configured_via")))
        return _ready(source="environment")

    monkeypatch.setattr(cli_module, "run_setup", fake_run_setup)

    exit_code = cli_module.main(["setup"])

    assert exit_code == 0
    assert seen == [(None, "cli")]


def test_failed_setup_exits_nonzero_and_explains_preservation(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_setup",
        lambda *a, **k: SetupResult(
            ready=False,
            stage="workspace",
            code="workspace_incompatible",
            message="This spreadsheet is not a Personal JobHunter workspace.",
            preserved="Nothing was changed.",
        ),
    )

    exit_code = cli_module.main(["setup", "--spreadsheet-url", SYNTHETIC_URL])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "workspace_incompatible" in output.err
    assert "Nothing was changed." in output.err


def test_doctor_reports_readiness_and_source(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = settings.google_service_account_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_key_bytes())
    monkeypatch.setattr(
        cli_module,
        "effective_spreadsheet",
        lambda engine, s: _Effective(SYNTHETIC_ID, "cli"),
    )
    monkeypatch.setattr(
        cli_module,
        "GoogleSheetsSettingsStore",
        lambda engine: _Saved(SYNTHETIC_ID, NOW, "cli"),
    )

    exit_code = cli_module.main(["doctor"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Workspace ready: yes" in output.out
    assert "Configured via: cli" in output.out


def test_doctor_reports_not_ready_when_env_names_another_sheet(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The link lives in `.env`; changing it requires a fresh setup."""

    path = settings.google_service_account_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_key_bytes())
    monkeypatch.setattr(
        cli_module,
        "effective_spreadsheet",
        lambda engine, s: _Effective(SYNTHETIC_ID, "environment"),
    )
    monkeypatch.setattr(
        cli_module,
        "GoogleSheetsSettingsStore",
        lambda engine: _Saved(SYNTHETIC_ID, NOW, "environment"),
    )
    monkeypatch.setattr(
        cli_module,
        "environment_spreadsheet_id",
        lambda s: OTHER_SYNTHETIC_ID,
    )

    exit_code = cli_module.main(["doctor"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Workspace ready: no" in output.out


def test_doctor_stays_ready_when_env_matches_the_validated_sheet(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = settings.google_service_account_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_key_bytes())
    monkeypatch.setattr(
        cli_module,
        "effective_spreadsheet",
        lambda engine, s: _Effective(SYNTHETIC_ID, "environment"),
    )
    monkeypatch.setattr(
        cli_module,
        "GoogleSheetsSettingsStore",
        lambda engine: _Saved(SYNTHETIC_ID, NOW, "environment"),
    )
    monkeypatch.setattr(
        cli_module,
        "environment_spreadsheet_id",
        lambda s: SYNTHETIC_ID,
    )

    exit_code = cli_module.main(["doctor"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Workspace ready: yes" in output.out


def test_doctor_fails_before_setup_completes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = settings.google_service_account_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_key_bytes())
    monkeypatch.setattr(
        cli_module,
        "effective_spreadsheet",
        lambda engine, s: _Effective(SYNTHETIC_ID, "cli"),
    )
    monkeypatch.setattr(
        cli_module,
        "GoogleSheetsSettingsStore",
        lambda engine: _Saved(SYNTHETIC_ID, None, "cli"),
    )

    exit_code = cli_module.main(["doctor"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Workspace ready: no" in output.out


class _Effective:
    def __init__(self, spreadsheet_id: str, source: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.source = source


def _raise_invalid():
    from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

    raise GoogleSheetsError(
        "spreadsheet_url_invalid",
        "GOOGLE_SHEETS_SPREADSHEET_URL is not a valid Google Sheets URL.",
    )


def test_doctor_handles_a_fresh_unmigrated_database(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No traceback, actionable next step, non-zero exit on a fresh database."""

    monkeypatch.setattr(cli_module, "_settings_table_missing", lambda engine: True)

    exit_code = cli_module.main(["doctor"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "migrations pending" in output.out
    assert "job-hunter setup" in output.out
    assert "Traceback" not in output.err


def test_doctor_reports_an_invalid_environment_spreadsheet_url(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed `.env` URL is invalid configuration, not 'not configured'."""

    path = settings.google_service_account_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_key_bytes())
    monkeypatch.setattr(
        cli_module,
        "effective_spreadsheet",
        lambda engine, s: _raise_invalid(),
    )

    exit_code = cli_module.main(["doctor"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "spreadsheet_url_invalid" in output.out
    assert "Workspace ready: no" in output.out


class _Saved:
    def __init__(self, spreadsheet_id: str, ready_at, configured_via: str) -> None:
        self._state = _State(spreadsheet_id, ready_at, configured_via)

    def load_state(self):
        return self._state


class _State:
    def __init__(self, spreadsheet_id: str, ready_at, configured_via: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.workspace_ready_at = ready_at
        self.configured_via = configured_via
