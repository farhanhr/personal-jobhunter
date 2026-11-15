"""Tests for the Google Sheets composition root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.config import REQUIRED_TABS
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_foundation,
)

SPREADSHEET_ID = "test_spreadsheet_id_1234567890"


class ReadyTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.calls.append(("get_spreadsheet", spreadsheet_id))
        return {"sheets": [{"properties": {"title": title}} for title in REQUIRED_TABS]}

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("batch_get_values", (spreadsheet_id, tuple(ranges))))
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
        raise AssertionError("Read-only preflight must not write values.")

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise AssertionError("Read-only preflight must not modify the spreadsheet.")


def test_foundation_uses_an_injected_transport_without_credentials(
    tmp_path: Path,
) -> None:
    transport = ReadyTransport()
    settings = Settings(
        data_dir=tmp_path,
        google_sheets_spreadsheet_id=SPREADSHEET_ID,
    )

    foundation = build_google_sheets_foundation(settings, transport=transport)

    assert foundation.check().is_ready
    assert transport.calls[0] == ("get_spreadsheet", SPREADSHEET_ID)
