"""Tests for Google Sheets configuration validation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_job_finder_api.integrations.google_sheets.config import (
    GoogleSheetsConfig,
    load_oauth_client_config,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

VALID_SPREADSHEET_ID = "test_spreadsheet_id_1234567890"


def settings_stub(
    *,
    spreadsheet_id: str | None = VALID_SPREADSHEET_ID,
    timeout_seconds: float = 10.0,
    max_batch_ranges: int = 100,
) -> SimpleNamespace:
    return SimpleNamespace(
        google_sheets_spreadsheet_id=spreadsheet_id,
        google_sheets_timeout_seconds=timeout_seconds,
        google_sheets_max_batch_ranges=max_batch_ranges,
    )


def test_missing_spreadsheet_id_is_a_sanitized_configuration_error() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsConfig.from_settings(settings_stub(spreadsheet_id=None))

    assert captured.value.code == "spreadsheet_id_missing"
    assert "GOOGLE_SHEETS_SPREADSHEET_ID" in str(captured.value)


def test_placeholder_spreadsheet_id_is_rejected() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsConfig.from_settings(
            settings_stub(spreadsheet_id="replace_with_your_spreadsheet_id")
        )

    assert captured.value.code == "spreadsheet_id_invalid"
    assert "replace_with_your_spreadsheet_id" not in str(captured.value)


def test_spreadsheet_id_with_unsupported_characters_is_rejected() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsConfig.from_settings(settings_stub(spreadsheet_id="not a valid/id"))

    assert captured.value.code == "spreadsheet_id_invalid"


def test_valid_spreadsheet_id_is_not_exposed_in_repr() -> None:
    config = GoogleSheetsConfig.from_settings(settings_stub())

    assert config.spreadsheet_id == VALID_SPREADSHEET_ID
    assert VALID_SPREADSHEET_ID not in repr(config)
    assert config.timeout_seconds == 10.0
    assert config.max_batch_ranges == 100


def test_oauth_client_config_requires_an_installed_desktop_client(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"web": {"client_id": "example"}}), encoding="utf-8")

    with pytest.raises(GoogleSheetsError) as captured:
        load_oauth_client_config(path)

    assert captured.value.code == "oauth_configuration_invalid"
    assert "example" not in str(captured.value)


def test_oauth_client_config_reports_missing_file_without_a_path(tmp_path: Path) -> None:
    path = tmp_path / "missing-client.json"

    with pytest.raises(GoogleSheetsError) as captured:
        load_oauth_client_config(path)

    assert captured.value.code == "oauth_configuration_invalid"
    assert str(path) not in str(captured.value)


def test_oauth_client_config_accepts_installed_application(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "example.apps.googleusercontent.com",
                    "client_secret": "not-a-real-secret",
                    "redirect_uris": ["http://127.0.0.1"],
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_oauth_client_config(path)

    assert config["installed"]["client_id"].endswith(".apps.googleusercontent.com")


def test_oauth_client_config_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(GoogleSheetsError) as captured:
        load_oauth_client_config(path)

    assert captured.value.code == "oauth_configuration_invalid"
