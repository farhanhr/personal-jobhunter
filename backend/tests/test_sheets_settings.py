"""Tests for local Google Sheets settings and credential-store wiring."""

from __future__ import annotations

import stat
from pathlib import Path

import keyring.errors

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.credentials import (
    build_credential_store,
)


class BrokenKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        raise keyring.errors.NoKeyringError("no backend")

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, service_name: str, username: str) -> None:
        raise keyring.errors.NoKeyringError("no backend")


def test_google_paths_default_under_the_private_data_directory(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.google_sheets_spreadsheet_id is None
    assert settings.google_oauth_client_file == (
        tmp_path / "credentials" / "google-oauth-client.json"
    )
    assert settings.google_token_file == (tmp_path / "credentials" / "google-token.json")
    assert settings.google_sheets_timeout_seconds == 10.0
    assert settings.google_sheets_max_batch_ranges == 100


def test_google_settings_read_environment_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_SHEETS_SPREADSHEET_ID",
        "test_spreadsheet_id_1234567890",
    )
    monkeypatch.setenv("GOOGLE_SHEETS_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("GOOGLE_SHEETS_MAX_BATCH_RANGES", "25")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(tmp_path / "client.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("GOOGLE_KEYRING_SERVICE_NAME", "personal-job-finder-tests")
    monkeypatch.setenv("GOOGLE_OAUTH_TIMEOUT_SECONDS", "45")

    settings = Settings(data_dir=tmp_path / "data")

    assert settings.google_sheets_spreadsheet_id == "test_spreadsheet_id_1234567890"
    assert settings.google_sheets_timeout_seconds == 4.5
    assert settings.google_sheets_max_batch_ranges == 25
    assert settings.google_oauth_client_file == tmp_path / "client.json"
    assert settings.google_token_file == tmp_path / "token.json"
    assert settings.google_keyring_service_name == "personal-job-finder-tests"
    assert settings.google_oauth_timeout_seconds == 45.0


def test_explicit_google_settings_win_over_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOOGLE_SHEETS_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "ignored.json"))

    settings = Settings(
        data_dir=tmp_path,
        google_sheets_timeout_seconds=3.0,
        google_token_file=tmp_path / "chosen.json",
    )

    assert settings.google_sheets_timeout_seconds == 3.0
    assert settings.google_token_file == tmp_path / "chosen.json"


def test_credential_store_falls_back_to_the_private_token_file(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    store = build_credential_store(settings, keyring_module=BrokenKeyring())

    store.save('{"refresh_token":"synthetic"}')

    assert store.load() == '{"refresh_token":"synthetic"}'
    assert settings.google_token_file is not None
    assert settings.google_token_file.exists()
    assert stat.S_IMODE(settings.google_token_file.stat().st_mode) == 0o600


def test_settings_repr_does_not_expose_the_spreadsheet_id(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        google_sheets_spreadsheet_id="test_spreadsheet_id_1234567890",
    )

    assert "test_spreadsheet_id_1234567890" not in repr(settings)
