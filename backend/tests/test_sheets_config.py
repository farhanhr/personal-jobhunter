"""Tests for Google Sheets configuration validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_job_finder_api.integrations.google_sheets.config import (
    GoogleSheetsConfig,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

VALID_SPREADSHEET_ID = "test_spreadsheet_id_1234567890"


def settings_stub(
    *,
    spreadsheet_id: str | None = VALID_SPREADSHEET_ID,
    timeout_seconds: float = 10.0,
    max_batch_ranges: int = 100,
    batch_size: int = 25,
) -> SimpleNamespace:
    return SimpleNamespace(
        google_sheets_spreadsheet_id=spreadsheet_id,
        google_sheets_timeout_seconds=timeout_seconds,
        google_sheets_max_batch_ranges=max_batch_ranges,
        google_sheets_batch_size=batch_size,
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
