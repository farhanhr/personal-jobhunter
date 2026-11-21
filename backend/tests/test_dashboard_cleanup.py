"""Tests for deleting the retired ``Dashboard`` cards tab.

Deletion is destructive, so the only thing worth testing is the guard: this
project may delete the tab it wrote, and nothing else.
"""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import dashboard_cleanup as cleanup
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

SPREADSHEET_ID = "spreadsheet_id_for_tests"


class FakeTransport:
    def __init__(self, tabs: dict[str, list[list[str]]] | None = None) -> None:
        self.tabs = dict(tabs or {})
        self.sheet_ids = {title: index for index, title in enumerate(self.tabs)}
        self.batches: list[list[dict[str, Any]]] = []

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        return {
            "properties": {"timeZone": "Asia/Jakarta", "locale": "en_US"},
            "sheets": [
                {
                    "properties": {
                        "sheetId": self.sheet_ids[title],
                        "title": title,
                        "gridProperties": {"rowCount": 100},
                    }
                }
                for title in self.tabs
            ],
        }

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        return self._read(ranges)

    def batch_get_values_with_render(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]:
        return self._read(ranges)

    def _read(self, ranges: list[str]) -> dict[str, Any]:
        value_ranges: list[dict[str, Any]] = []
        for requested in ranges:
            tab, _, cell = requested.partition("!")
            rows = self.tabs.get(tab, [])
            row_number = int("".join(ch for ch in cell if ch.isdigit()) or 1)
            column = 0
            for character in "".join(ch for ch in cell if ch.isalpha()):
                column = column * 26 + (ord(character) - 64)
            if row_number > len(rows) or column - 1 >= len(rows[row_number - 1]):
                value_ranges.append({})
                continue
            value_ranges.append({"values": [[rows[row_number - 1][column - 1]]]})
        return {"valueRanges": value_ranges}

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.batches.append(requests)
        for body in requests:
            if "deleteSheet" in body:
                sheet_id = body["deleteSheet"]["sheetId"]
                for title, candidate in list(self.sheet_ids.items()):
                    if candidate == sheet_id:
                        self.tabs.pop(title, None)
                        self.sheet_ids.pop(title, None)
        return {}


def _client(transport: FakeTransport):
    from personal_job_finder_api.integrations.google_sheets.client import (
        GoogleSheetsClient,
    )

    return GoogleSheetsClient(transport)


def _dashboard(sentinel: str = cleanup.DASHBOARD_SENTINEL) -> FakeTransport:
    grid = [[""] * 13 for _ in range(13)]
    grid[0][12] = sentinel
    return FakeTransport({"Opportunities": [["opportunity_id"]], "Dashboard": grid})


def _remove(transport: FakeTransport) -> str:
    return cleanup.DashboardRemovalService(_client(transport), SPREADSHEET_ID).remove()


def test_a_missing_tab_is_not_an_error() -> None:
    transport = FakeTransport({"Opportunities": [["opportunity_id"]]})

    assert _remove(transport) == "The Dashboard tab does not exist; nothing was changed."
    assert transport.batches == []


def test_our_cards_tab_is_deleted() -> None:
    transport = _dashboard()

    outcome = _remove(transport)

    assert outcome == "Deleted the retired Dashboard cards tab."
    assert "Dashboard" not in transport.tabs
    assert transport.batches == [[{"deleteSheet": {"sheetId": 1}}]]


def test_a_foreign_tab_is_left_untouched() -> None:
    transport = _dashboard(sentinel="My own summary")

    outcome = _remove(transport)

    assert "not owned by this version" in outcome
    assert "Dashboard" in transport.tabs
    assert transport.batches == []


def test_a_blank_sentinel_is_left_untouched() -> None:
    transport = _dashboard(sentinel="")

    outcome = _remove(transport)

    assert "not owned by this version" in outcome
    assert "Dashboard" in transport.tabs


def test_a_different_cards_version_is_refused() -> None:
    transport = _dashboard(sentinel="PJF_DASHBOARD_CARDS_V9")

    with pytest.raises(GoogleSheetsError) as captured:
        _remove(transport)

    assert captured.value.code == "dashboard_version_unsupported"
    assert "Dashboard" in transport.tabs
    assert transport.batches == []


def test_the_tab_is_never_deleted_without_a_sheet_id() -> None:
    """An unknown sheet id is a refusal, never a guess at another sheet."""

    class NoIdTransport(FakeTransport):
        def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
            response = super().get_spreadsheet(spreadsheet_id)
            for sheet in response["sheets"]:
                if sheet["properties"]["title"] == "Dashboard":
                    sheet["properties"].pop("sheetId")
            return response

    transport = NoIdTransport(_dashboard().tabs)

    with pytest.raises(GoogleSheetsError) as captured:
        _remove(transport)

    assert captured.value.code == "dashboard_metadata_incomplete"
    assert "Dashboard" in transport.tabs
    assert transport.batches == []
