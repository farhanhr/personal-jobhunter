"""Tests for workspace repair.

Repair exists because the spreadsheet is a human surface. These tests pin the
properties that make it safe to run before every sync: it changes structure only,
a second run changes nothing, and the one-time header move happens exactly once.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import kpi_strip
from personal_job_finder_api.integrations.google_sheets import workspace_repair as repair
from personal_job_finder_api.integrations.google_sheets import workspace_table as table
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_FIRST_DATA_ROW,
    OPPORTUNITY_HEADER_ROW,
    OPPORTUNITY_HEADERS,
)

SPREADSHEET_ID = "spreadsheet_id_for_tests"

_A1 = re.compile(r"^(?:'(?P<quoted>[^']+)'|(?P<plain>[^!]+))!(?P<body>.*)$")
_CELL = re.compile(r"^(?P<col>[A-Z]*)(?P<row>\d*)$")


def _column_index(letters: str) -> int:
    value = 0
    for character in letters:
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


def _parse_range(name: str) -> tuple[str, int, int, int, int]:
    """Return ``(title, start_row, end_row, start_col, end_col)``, 1-based."""

    match = _A1.match(name)
    if match is None:
        if "!" not in name:
            return name, 1, 10_000, 1, 40
        raise AssertionError(f"test fake cannot parse range {name!r}")
    title = match.group("quoted") or match.group("plain")
    body = match.group("body")
    if not body:
        return title, 1, 10_000, 1, 40
    start_text, _, end_text = body.partition(":")
    start = _CELL.match(start_text)
    if start is None or not (start.group("col") or start.group("row")):
        raise AssertionError(f"test fake cannot parse range {name!r}")
    start_col = _column_index(start.group("col")) if start.group("col") else 1
    start_row = int(start.group("row")) if start.group("row") else 1
    if not end_text:
        return title, start_row, start_row, start_col, start_col
    end = _CELL.match(end_text)
    if end is None:
        raise AssertionError(f"test fake cannot parse range {name!r}")
    end_col = _column_index(end.group("col")) if end.group("col") else 40
    end_row = int(end.group("row")) if end.group("row") else 10_000
    if not end.group("col") and start.group("col"):
        end_col = start_col
    if not end.group("row") and start.group("row"):
        end_row = 10_000
    return title, start_row, end_row, start_col, end_col


def _table_entry(table: dict[str, Any]) -> dict[str, Any]:
    """A metadata-shaped table entry with the range repair compares."""

    return {
        "tableId": "t1",
        "name": table.get("name"),
        "range": dict(table["range"]),
        "columnProperties": list(table.get("columnProperties", [])),
    }


class FakeTransport:
    """A small in-memory spreadsheet: rows, tables, panes, and grid reads."""

    def __init__(self, tabs: dict[str, list[list[str]]] | None = None) -> None:
        self.tabs = {title: [list(row) for row in rows] for title, rows in (tabs or {}).items()}
        self.sheet_ids = {title: index for index, title in enumerate(self.tabs)}
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.frozen = {title: (1, 3) for title in self.tabs}
        self.column_counts = {title: 34 for title in self.tabs}
        self.batches: list[list[dict[str, Any]]] = []
        self.value_updates: list[list[dict[str, Any]]] = []
        self.added_tabs: list[str] = []
        self.deleted: list[str] = []

    # ------------------------------------------------------------- metadata

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        return {
            "properties": {"timeZone": "Asia/Jakarta", "locale": "en_US"},
            "sheets": [
                {
                    "properties": {
                        "sheetId": self.sheet_ids[title],
                        "title": title,
                        "gridProperties": {
                            "frozenRowCount": self.frozen.get(title, (0, 0))[0],
                            "frozenColumnCount": self.frozen.get(title, (0, 0))[1],
                            "rowCount": 1000,
                            "columnCount": self.column_counts.get(title, 34),
                        },
                    },
                    "tables": self.tables.get(title, []),
                }
                for title in self.tabs
            ],
        }

    # ---------------------------------------------------------------- reads

    def _slice(
        self,
        title: str,
        start_row: int,
        end_row: int,
        start_col: int,
        end_col: int,
    ) -> list[list[str]]:
        rows = self.tabs.get(title, [])
        sliced: list[list[str]] = []
        for row_number in range(start_row, min(end_row, len(rows)) + 1):
            row = rows[row_number - 1]
            cells = [
                str(row[index]) if index < len(row) else ""
                for index in range(start_col - 1, end_col)
            ]
            while cells and cells[-1] == "":
                cells.pop()
            sliced.append(cells)
        while sliced and not any(sliced[-1]):
            sliced.pop()
        return sliced

    def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
        values = []
        for requested in ranges:
            title, start_row, end_row, start_col, end_col = _parse_range(requested)
            rows = self._slice(title, start_row, end_row, start_col, end_col)
            values.append({"values": rows} if rows else {})
        return {"valueRanges": values}

    def batch_get_values_with_render(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]:
        return self.batch_get_values(spreadsheet_id, ranges)

    def get_spreadsheet_grid(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        sheets: dict[str, list[dict[str, Any]]] = {}
        for requested in ranges:
            title, start_row, end_row, start_col, end_col = _parse_range(requested)
            block_rows: list[dict[str, Any]] = []
            for row in self._slice(title, start_row, end_row, start_col, end_col):
                cells = []
                for value in row:
                    if value == "":
                        cells.append({})
                        continue
                    entry: dict[str, Any] = {"userEnteredValue": {"stringValue": value}}
                    if isinstance(value, str) and value.startswith("="):
                        entry = {
                            "userEnteredValue": {"formulaValue": value},
                            "effectiveValue": {"numberValue": 0},
                        }
                    cells.append(entry)
                block_rows.append({"values": cells})
            sheets.setdefault(title, []).append(
                {
                    "startRow": start_row - 1,
                    "startColumn": start_col - 1,
                    "rowData": block_rows,
                }
            )
        return {
            "sheets": [
                {"properties": {"title": title}, "data": data} for title, data in sheets.items()
            ]
        }

    # --------------------------------------------------------------- writes

    def _write(self, tab: str, first: str, values: list[Any]) -> None:
        title, start_row, _, start_col, _ = _parse_range(f"{tab}!{first}")
        rows = self.tabs.setdefault(title, [])
        while len(rows) < start_row:
            rows.append([])
        target = rows[start_row - 1]
        for offset, value in enumerate(values):
            index = start_col - 1 + offset
            while len(target) <= index:
                target.append("")
            target[index] = value
        self.column_counts[title] = max(self.column_counts.get(title, 0), len(target))

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str | None = None,
    ) -> dict[str, Any]:
        self.value_updates.append(data)
        for entry in data:
            tab, _, a1 = entry["range"].partition("!")
            self._write(tab, a1.split(":")[0], entry["values"][0])
        return {}

    def batch_update_spreadsheet(
        self, spreadsheet_id: str, requests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.batches.append(requests)
        for body in requests:
            if "addSheet" in body:
                title = body["addSheet"]["properties"]["title"]
                self.tabs.setdefault(title, [])
                self.sheet_ids[title] = 900 + len(self.added_tabs)
                self.column_counts[title] = 26
                self.added_tabs.append(title)
            elif "deleteSheet" in body:
                sheet_id = body["deleteSheet"]["sheetId"]
                for title, candidate in list(self.sheet_ids.items()):
                    if candidate == sheet_id:
                        self.deleted.append(title)
                        self.tabs.pop(title, None)
                        self.tables.pop(title, None)
                        self.sheet_ids.pop(title, None)
            elif "addTable" in body:
                for title, sheet_id in self.sheet_ids.items():
                    if sheet_id == body["addTable"]["table"]["range"]["sheetId"]:
                        self.tables[title] = [_table_entry(body["addTable"]["table"])]
            elif "updateTable" in body:
                for title, sheet_id in self.sheet_ids.items():
                    if sheet_id == body["updateTable"]["table"]["range"]["sheetId"]:
                        self.tables[title] = [_table_entry(body["updateTable"]["table"])]
            elif "appendDimension" in body:
                dimension = body["appendDimension"]
                if dimension.get("dimension") == "COLUMNS":
                    title = self._title_of(dimension["sheetId"])
                    self.column_counts[title] = self.column_counts.get(title, 26) + int(
                        dimension["length"]
                    )
            elif "updateSheetProperties" in body:
                properties = body["updateSheetProperties"]["properties"]
                grid = properties.get("gridProperties")
                if grid:
                    for title, sheet_id in self.sheet_ids.items():
                        if sheet_id == properties["sheetId"]:
                            self.frozen[title] = (
                                grid.get("frozenRowCount", 0),
                                grid.get("frozenColumnCount", 0),
                            )
            elif "insertDimension" in body:
                dimension = body["insertDimension"]["range"]
                title = self._title_of(dimension["sheetId"])
                rows = self.tabs.setdefault(title, [])
                start = dimension["startIndex"]
                count = dimension["endIndex"] - start
                for _ in range(count):
                    rows.insert(start, [])
            elif "updateCells" in body:
                self._apply_cells(body["updateCells"])
            elif "setDataValidation" in body:
                pass
        return {}

    def _title_of(self, sheet_id: int) -> str:
        for title, candidate in self.sheet_ids.items():
            if candidate == sheet_id:
                return title
        raise AssertionError(f"unknown sheet id {sheet_id}")

    def _apply_cells(self, request: dict[str, Any]) -> None:
        title = self._title_of(request["range"]["sheetId"])
        start_row = request["range"]["startRowIndex"] + 1
        start_col = request["range"]["startColumnIndex"]
        rows = self.tabs.setdefault(title, [])
        while len(rows) < start_row:
            rows.append([])
        target = rows[start_row - 1]
        while len(target) <= start_col:
            target.append("")
        for offset, row in enumerate(request.get("rows", [])):
            for inner, cell in enumerate(row.get("values", [])):
                entered = cell.get("userEnteredValue", {})
                value = entered.get("formulaValue") or entered.get("stringValue") or ""
                index = start_col + inner
                while len(target) <= index:
                    target.append("")
                target[index] = value
            if offset:
                rows.insert(start_row + offset, [])
                target = rows[start_row + offset - 1]


def _client(transport: FakeTransport):
    from personal_job_finder_api.integrations.google_sheets.client import (
        GoogleSheetsClient,
    )

    return GoogleSheetsClient(transport)


def _legacy() -> FakeTransport:
    """A workbook in the pre-ADR-027 layout: header on row 1, no strip."""

    transport = FakeTransport(
        {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )
    transport.tables["Opportunities"] = [
        {
            "tableId": "t1",
            "range": {
                "sheetId": 0,
                "startRowIndex": 0,
                "endRowIndex": 1000,
                "startColumnIndex": 0,
                "endColumnIndex": len(OPPORTUNITY_HEADERS),
            },
        }
    ]
    return transport


def _repaired() -> FakeTransport:
    transport = _legacy()
    repair.WorkspaceRepairService(_client(transport), SPREADSHEET_ID).repair()
    return transport


def _repair(transport: FakeTransport) -> repair.RepairOutcome:
    return repair.WorkspaceRepairService(_client(transport), SPREADSHEET_ID).repair()


def _opportunities(transport: FakeTransport) -> list[list[str]]:
    return transport.tabs["Opportunities"]


def _header_row(transport: FakeTransport) -> list[str]:
    return _opportunities(transport)[OPPORTUNITY_HEADER_ROW - 1]


# ------------------------------------------------------------------ migration


def test_a_row_one_header_moves_below_the_strip_once() -> None:
    transport = _legacy()

    outcome = _repair(transport)

    assert f"moved the canonical header to row {OPPORTUNITY_HEADER_ROW}" in outcome.actions
    assert _header_row(transport)[: len(OPPORTUNITY_HEADERS)] == list(OPPORTUNITY_HEADERS)
    assert _opportunities(transport)[0][0] == "Total Applied"


def test_the_migration_keeps_every_data_row_in_order() -> None:
    transport = _legacy()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[0] = "opp_keep"
    row[1] = "Acme Example"
    transport.tabs["Opportunities"].append(row)

    _repair(transport)

    assert _opportunities(transport)[OPPORTUNITY_FIRST_DATA_ROW - 1][0] == "opp_keep"


def test_a_second_repair_does_not_move_the_header_again() -> None:
    transport = _repaired()

    second = _repair(transport)

    assert not any("moved the canonical header" in action for action in second.actions)
    assert _header_row(transport)[: len(OPPORTUNITY_HEADERS)] == list(OPPORTUNITY_HEADERS)


def test_an_unknown_row_one_layout_is_refused() -> None:
    transport = FakeTransport(
        {
            "Opportunities": [["something", "else"]],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )

    with pytest.raises(Exception) as error:
        _repair(transport)

    assert getattr(error.value, "code", "") == "workspace_header_row_unknown"
    assert transport.tabs["Opportunities"] == [["something", "else"]]


# ------------------------------------------------------------------ idempotency


def test_a_repaired_workbook_needs_no_structural_change() -> None:
    transport = _repaired()

    outcome = _repair(transport)

    assert outcome.actions == ()
    assert outcome.adopted_rows == ()
    assert outcome.message() == "Workspace already matches the expected structure."


def test_a_second_repair_writes_no_values() -> None:
    transport = _repaired()
    transport.value_updates.clear()

    _repair(transport)

    assert transport.value_updates == []


def test_a_second_repair_writes_no_structured_requests() -> None:
    transport = _repaired()
    transport.batches.clear()

    _repair(transport)

    assert transport.batches == []


# --------------------------------------------------------------------- headers


def test_a_cleared_header_row_is_restored() -> None:
    transport = _repaired()
    _opportunities(transport)[OPPORTUNITY_HEADER_ROW - 1] = [""] * len(OPPORTUNITY_HEADERS)

    outcome = _repair(transport)

    assert "restored the Opportunities header row" in outcome.actions
    assert _header_row(transport) == list(OPPORTUNITY_HEADERS)


def test_a_reordered_header_row_is_restored() -> None:
    transport = _repaired()
    header = list(OPPORTUNITY_HEADERS)
    header[0], header[1] = header[1], header[0]
    _opportunities(transport)[OPPORTUNITY_HEADER_ROW - 1] = header

    _repair(transport)

    assert _header_row(transport) == list(OPPORTUNITY_HEADERS)


def test_header_repair_never_touches_a_data_row() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[0] = "opp_keep"
    row[1] = "Acme Example"
    _opportunities(transport).append(row)
    _opportunities(transport)[OPPORTUNITY_HEADER_ROW - 1] = ["wrong"] * len(OPPORTUNITY_HEADERS)

    _repair(transport)

    data_row = _opportunities(transport)[OPPORTUNITY_FIRST_DATA_ROW - 1]
    assert data_row[0] == "opp_keep"
    assert data_row[1] == "Acme Example"


# ------------------------------------------------------------------ KPI strip


def test_the_strip_writes_four_cards_and_a_bound_helper() -> None:
    transport = _repaired()

    strip = kpi_strip.cells()
    written = {cell.address: cell.content for cell in strip}

    assert written["A1"] == "Total Applied"
    assert written["E1"] == "Need to Apply"
    assert written["I1"] == "Applied (last 7 days)"
    assert written["M1"] == "Interviews (next 7 days)"
    for address in ("A2", "E2", "I2", "M2"):
        assert written[address].startswith("=")
    assert written[kpi_strip.BOUND_CELL].startswith("=")
    for address, content in written.items():
        row_text = address.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        column_text = address[: len(address) - len(row_text)]
        assert (
            transport.tabs["Opportunities"][int(row_text) - 1][_column_index(column_text) - 1]
            == content
        )


def test_the_strip_is_rebuilt_when_a_card_is_cleared() -> None:
    transport = _repaired()
    transport.tabs["Opportunities"][0][0] = ""

    outcome = _repair(transport)

    assert "rebuilt the KPI strip" in outcome.actions
    assert transport.tabs["Opportunities"][0][0] == "Total Applied"


def test_the_strip_never_writes_into_a_data_row() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[0] = "opp_keep"
    row[1] = "Acme Example"
    _opportunities(transport).append(row)

    _repair(transport)

    assert _opportunities(transport)[OPPORTUNITY_FIRST_DATA_ROW - 1][0] == "opp_keep"


# ----------------------------------------------------------------------- table


def test_a_missing_table_is_created() -> None:
    transport = FakeTransport(
        {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )

    outcome = _repair(transport)

    assert "created the workspace table" in outcome.actions
    assert transport.tables["Opportunities"]


def test_a_new_table_gets_its_column_rules_from_the_update() -> None:
    """Live regression: ``addTable`` with names shifts the canonical header."""

    transport = FakeTransport(
        {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )

    _repair(transport)

    add_requests = [body for batch in transport.batches for body in batch if "addTable" in body]
    assert add_requests
    assert "columnProperties" not in add_requests[0]["addTable"]["table"]

    created = transport.tables["Opportunities"][0]
    assert len(created["columnProperties"]) == len(OPPORTUNITY_HEADERS)
    dropdowns = {
        entry["columnName"]
        for entry in created["columnProperties"]
        if entry.get("columnType") == "DROPDOWN"
    }
    assert dropdowns == set(table.USER_DROPDOWNS)


def test_the_grid_is_widened_before_a_new_table_is_created() -> None:
    """A fresh tab has 26 columns; the table needs the canonical 33."""

    transport = FakeTransport(
        {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )
    transport.column_counts["Opportunities"] = 26

    _repair(transport)

    bodies = [body for batch in transport.batches for body in batch]
    append_at = next(index for index, body in enumerate(bodies) if "appendDimension" in body)
    add_at = next(index for index, body in enumerate(bodies) if "addTable" in body)
    assert append_at < add_at
    assert transport.column_counts["Opportunities"] >= len(OPPORTUNITY_HEADERS)


def test_a_fresh_tab_is_widened_only_to_the_helper_column() -> None:
    """The header write already widens the grid; do not append eight more."""

    transport = FakeTransport(
        {
            "Opportunities": [],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )
    transport.column_counts["Opportunities"] = 26

    _repair(transport)

    assert transport.column_counts["Opportunities"] == len(OPPORTUNITY_HEADERS) + 1


def test_an_existing_table_is_never_duplicated() -> None:
    transport = _repaired()

    _repair(transport)

    assert not any("addTable" in body for batch in transport.batches for body in batch)


def test_drifted_panes_are_restored_without_rewriting_the_table() -> None:
    transport = _repaired()
    transport.frozen["Opportunities"] = (0, 0)
    transport.batches.clear()

    outcome = _repair(transport)

    assert "restored the frozen panes" in outcome.actions
    assert transport.frozen["Opportunities"] == (
        OPPORTUNITY_HEADER_ROW,
        3,
    )
    assert not any("updateTable" in body for batch in transport.batches for body in batch)


def test_force_reapplies_the_presentation_layer() -> None:
    transport = _repaired()

    outcome = repair.WorkspaceRepairService(_client(transport), SPREADSHEET_ID).repair(force=True)

    assert "updated the workspace table" in outcome.actions
    assert "rebuilt the KPI strip" in outcome.actions


def test_repair_never_deletes_or_clears_a_row() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[0] = "opp_1"
    _opportunities(transport).append(row)

    _repair(transport)

    for batch in transport.batches:
        for body in batch:
            assert "deleteRange" not in body
            assert "deleteDimension" not in body


def test_a_missing_canonical_tab_is_created() -> None:
    transport = FakeTransport({"Opportunities": [list(OPPORTUNITY_HEADERS)]})

    outcome = _repair(transport)

    assert "created the Job_Sources tab" in outcome.actions
    assert "Job_Sources" in transport.tabs


# --------------------------------------------------------------- manual rows


def test_a_manual_row_receives_a_deterministic_identity() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    row[2] = "Backend Engineer"
    _opportunities(transport).append(row)

    outcome = _repair(transport)

    assert outcome.adopted_rows == (OPPORTUNITY_FIRST_DATA_ROW,)
    identity = _opportunities(transport)[OPPORTUNITY_FIRST_DATA_ROW - 1][0]
    assert identity.startswith("opp_")
    assert len(identity) == len("opp_") + 32


def test_the_same_manual_row_gets_the_same_identity_every_time() -> None:
    first = repair.manual_identity(["", "Acme Example", "Backend Engineer"])
    second = repair.manual_identity(["", "Acme  Example ", "Backend   Engineer"])

    assert first == second


def test_a_manual_row_is_not_rewritten_after_adoption() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    row[2] = "Backend Engineer"
    _opportunities(transport).append(row)

    _repair(transport)
    second = _repair(transport)

    assert second.adopted_rows == ()


def test_only_the_identity_cell_is_written_for_a_manual_row() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    row[2] = "Backend Engineer"
    row[21] = "saved"
    row[32] = "my own note"
    _opportunities(transport).append(row)

    _repair(transport)

    entries = [entry for batch in transport.value_updates for entry in batch]
    assert len(entries) == 1
    assert entries[0]["range"] == f"Opportunities!A{OPPORTUNITY_FIRST_DATA_ROW}"
    assert len(entries[0]["values"][0]) == 1
    assert _opportunities(transport)[OPPORTUNITY_FIRST_DATA_ROW - 1][32] == "my own note"


def test_a_row_without_a_company_or_title_is_left_alone() -> None:
    transport = _repaired()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    _opportunities(transport).append(row)

    outcome = _repair(transport)

    assert outcome.adopted_rows == ()
    assert _opportunities(transport)[OPPORTUNITY_FIRST_DATA_ROW - 1][0] == ""


def test_a_manual_identity_never_collides_with_a_discovered_one() -> None:
    manual = repair.manual_identity(["", "Acme Example", "Backend Engineer"])

    from personal_job_finder_api.domain.opportunity_identity import opportunity_id_for

    discovered = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )
    assert manual != discovered


# -------------------------------------------------------------------- message


def test_the_message_lists_what_changed() -> None:
    transport = _repaired()
    _opportunities(transport)[OPPORTUNITY_HEADER_ROW - 1] = ["wrong"] * len(OPPORTUNITY_HEADERS)

    outcome = _repair(transport)

    assert "Repaired:" in outcome.message()
    assert "header row" in outcome.message()


@pytest.mark.parametrize(
    "row",
    [
        ["", "Acme", ""],
        ["", "", "Engineer"],
        ["", "", ""],
    ],
)
def test_incomplete_rows_are_never_adopted(row: list[str]) -> None:
    assert repair.manual_identity(row) is None
