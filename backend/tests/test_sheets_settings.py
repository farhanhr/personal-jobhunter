"""Tests for local Google Sheets settings and the single Service Account location."""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_job_finder_api.core.config import REPOSITORY_ROOT, Settings


def test_service_account_path_is_the_single_canonical_location(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.google_service_account_file == (
        REPOSITORY_ROOT / ".local" / "credentials" / "google-service-account.json"
    )


def test_data_dir_does_not_change_the_credential_location(tmp_path: Path) -> None:
    left = Settings(data_dir=tmp_path / "a")
    right = Settings(data_dir=tmp_path / "b")

    assert left.google_service_account_file == right.google_service_account_file


def test_local_dir_override_relocates_the_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-028 allows ``PJF_LOCAL_DIR`` so checks never touch the user's key."""

    monkeypatch.setenv("PJF_LOCAL_DIR", str(tmp_path / "isolated"))

    settings = Settings(data_dir=tmp_path / "data")

    assert settings.google_service_account_file == (
        tmp_path / "isolated" / "credentials" / "google-service-account.json"
    )
    assert settings.local_dir == (tmp_path / "isolated").resolve()


def test_canonical_credential_location_is_used_without_the_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PJF_LOCAL_DIR", raising=False)

    settings = Settings(data_dir=tmp_path / "data")

    assert settings.google_service_account_file == (
        REPOSITORY_ROOT / ".local" / "credentials" / "google-service-account.json"
    )


def test_google_settings_read_environment_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "test_spreadsheet_id_1234567890")
    monkeypatch.setenv("GOOGLE_SHEETS_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("GOOGLE_SHEETS_MAX_BATCH_RANGES", "25")

    settings = Settings(data_dir=tmp_path / "data")

    assert settings.google_sheets_spreadsheet_id == "test_spreadsheet_id_1234567890"
    assert settings.google_sheets_timeout_seconds == 4.5
    assert settings.google_sheets_max_batch_ranges == 25


@pytest.mark.parametrize(
    "removed",
    [
        "GOOGLE_OAUTH_CLIENT_FILE",
        "GOOGLE_TOKEN_FILE",
        "GOOGLE_KEYRING_SERVICE_NAME",
        "GOOGLE_OAUTH_TIMEOUT_SECONDS",
    ],
)
def test_removed_oauth_settings_have_no_effect(monkeypatch, tmp_path: Path, removed: str) -> None:
    monkeypatch.setenv(removed, str(tmp_path / "ignored"))

    settings = Settings(data_dir=tmp_path)

    assert not hasattr(settings, removed.lower())


def test_settings_repr_does_not_expose_the_spreadsheet_id(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        google_sheets_spreadsheet_id="test_spreadsheet_id_1234567890",
    )

    assert "test_spreadsheet_id_1234567890" not in repr(settings)
