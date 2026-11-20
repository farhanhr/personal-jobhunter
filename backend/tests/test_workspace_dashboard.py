"""Tests for the ``Dashboard`` cards initialization service.

The cards tab is optional and must never be overwritten blindly. These tests pin
the four states a tab can be in, and that a refusal writes nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import workspace_layout as layout
from personal_job_finder_api.integrations.google_sheets.client import GoogleSheetsClient
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.workspace import (
    DashboardInitState,
    WorkspaceDashboardService,
)

SPREADSHEET_ID = "spreadsheet_id_for_tests"


class Transport:
    """A stateful fake of the Sheets calls this service makes."""

    def __init__(
        self,
        *,
        tabs: dict[str, list[list[str]]] | None = None,
        time_zone: str | None = "Asia/Jakarta",
        locale: str | None = "en_US",
    ) -> None:
        self.tabs = dict(tabs or {})
        self.time_zone = time_zone
        self.locale = locale
        self.sheet_ids = {title: index for index, title in enumerate(self.tabs)}
        self.updates: list[list[dict[str, Any]]] = []
        self.calls: list[str] = []

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        if self.time_zone is not None:
            properties["timeZone"] = self.time_zone
        if self.locale is not None:
            properties["locale"] = self.locale
        return {
            "properties": properties,
            "sheets": [
                {"properties": {"sheetId": self.sheet_ids[title], "title": title}}
                for title in self.tabs
            ],
        }

    def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
        values = []
        for requested in ranges:
            tab = requested.split("!", 1)[0]
            rows = self.tabs.get(tab, [])
            values.append({"values": [list(row) for row in rows]} if rows else {})
        return {"valueRanges": values}

    def batch_get_values_with_render(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]:
        return self.batch_get_values(spreadsheet_id, ranges)

    def batch_update_values(
        self, spreadsheet_id: str, data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        raise AssertionError("initialization must not write values")

    def batch_update_spreadsheet(
        self, spreadsheet_id: str, requests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.updates.append(requests)
        for body in requests:
            if "addSheet" in body:
                title = body["addSheet"]["properties"]["title"]
                self.tabs.setdefault(title, [])
                self.sheet_ids[title] = 900
            elif "updateCells" in body:
                self._apply(body["updateCells"])
        return {}

    def _apply(self, body: dict[str, Any]) -> None:
        tab = next(
            title for title, value in self.sheet_ids.items() if value == body["start"]["sheetId"]
        )
        grid = self.tabs.setdefault(tab, [])
        for row_offset, row in enumerate(body["rows"]):
            index = body["start"]["rowIndex"] + row_offset
            while len(grid) <= index:
                grid.append([])
            cells = grid[index]
            for column_offset, cell in enumerate(row["values"]):
                column = body["start"]["columnIndex"] + column_offset
                while len(cells) <= column:
                    cells.append("")
                entered = cell.get("userEnteredValue") or {}
                cells[column] = entered.get("formulaValue", entered.get("stringValue", ""))


def _spreadsheet(dashboard: list[list[str]] | None = None) -> Transport:
    tabs: dict[str, list[list[str]]] = {
        "Opportunities": [list(OPPORTUNITY_HEADERS)],
        "Job_Sources": [list(JOB_SOURCE_HEADERS)],
    }
    if dashboard is not None:
        tabs[layout.DASHBOARD_TAB] = dashboard
    return Transport(tabs=tabs)


def _service(transport: Transport) -> WorkspaceDashboardService:
    return WorkspaceDashboardService(GoogleSheetsClient(transport), SPREADSHEET_ID)


def _cell(transport: Transport, address: str) -> str:
    row, column = layout.parse_address(address)
    rows = transport.tabs.get(layout.DASHBOARD_TAB, [])
    if row >= len(rows):
        return ""
    values = rows[row]
    return values[column] if column < len(values) else ""


# --------------------------------------------------------------------- states


def test_a_missing_cards_tab_is_created() -> None:
    transport = _spreadsheet()

    result = _service(transport).initialize()

    assert result.state is DashboardInitState.CREATED
    assert layout.DASHBOARD_TAB in transport.tabs
    assert _cell(transport, layout.SENTINEL_CELL) == layout.DASHBOARD_SENTINEL


def test_the_cards_are_written_as_formulas() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    for address in layout.CARD_CELLS:
        assert _cell(transport, address).startswith("="), address


def test_a_second_run_is_a_no_op() -> None:
    transport = _spreadsheet()
    _service(transport).initialize()
    before = [list(row) for row in transport.tabs[layout.DASHBOARD_TAB]]

    result = _service(transport).initialize()

    assert result.state is DashboardInitState.ALREADY_INITIALIZED
    assert transport.tabs[layout.DASHBOARD_TAB] == before


def test_a_no_op_preserves_a_user_edit_next_to_the_layout() -> None:
    transport = _spreadsheet()
    _service(transport).initialize()
    transport.tabs[layout.DASHBOARD_TAB][10] = ["my own note"]

    _service(transport).initialize()

    assert transport.tabs[layout.DASHBOARD_TAB][10] == ["my own note"]


def test_an_empty_tab_is_initialized_in_place() -> None:
    transport = _spreadsheet(dashboard=[])

    result = _service(transport).initialize()

    assert result.state is DashboardInitState.INITIALIZED
    assert _cell(transport, layout.SENTINEL_CELL) == layout.DASHBOARD_SENTINEL


def test_a_tab_of_blank_cells_counts_as_empty() -> None:
    transport = _spreadsheet(dashboard=[["", "", ""]])

    result = _service(transport).initialize()

    assert result.state is DashboardInitState.INITIALIZED


# -------------------------------------------------------------------- refusals


def test_a_foreign_non_empty_tab_is_refused_without_writing() -> None:
    transport = _spreadsheet(dashboard=[["my own dashboard"]])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []
    assert transport.tabs[layout.DASHBOARD_TAB] == [["my own dashboard"]]


def test_content_far_outside_the_layout_is_still_detected() -> None:
    rows = [[""]] * 60
    rows[55] = ["", "far away value"]
    transport = _spreadsheet(dashboard=rows)

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"


def test_an_unsupported_sentinel_is_refused() -> None:
    row = [""] * 12
    row.append("PJF_DASHBOARD_V9")
    transport = _spreadsheet(dashboard=[row])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_unsupported_version"
    assert transport.updates == []


def test_a_metric_board_sentinel_is_refused_on_the_cards_tab() -> None:
    """The two tabs have different layouts, so neither sentinel vouches for both."""

    row = [""] * 12
    row.append("PJF_DASHBOARD_V2")
    transport = _spreadsheet(dashboard=[row])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_unsupported_version"


def test_a_non_jakarta_time_zone_is_refused_before_any_write() -> None:
    transport = _spreadsheet()
    transport.time_zone = "Europe/Berlin"

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert "time zone" in str(captured.value)
    assert transport.updates == []


def test_an_unsupported_locale_is_refused_before_any_write() -> None:
    transport = _spreadsheet()
    transport.locale = "fr_FR"

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert "locale" in str(captured.value)
    assert transport.updates == []


# ------------------------------------------------------------------- atomicity


def test_the_sentinel_is_written_last_in_the_layout_batch() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    layout_batch = transport.updates[-1]
    assert "updateCells" in layout_batch[-1]
    sentinel = layout_batch[-1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]
    assert sentinel == {"stringValue": layout.DASHBOARD_SENTINEL}


def test_the_layout_and_sentinel_share_one_batch() -> None:
    """``addSheet`` is a separate call, so only the layout batch is claimed atomic."""

    transport = _spreadsheet()

    _service(transport).initialize()

    layout_batches = [
        batch for batch in transport.updates if not any("addSheet" in body for body in batch)
    ]
    assert len(layout_batches) == 1
