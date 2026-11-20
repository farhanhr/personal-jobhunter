"""Tests for workspace repair.

Repair exists because the spreadsheet is a human surface. These tests pin the two
properties that make it safe to run before every sync: it changes structure only,
and a second run changes nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import workspace_repair as repair
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)

SPREADSHEET_ID = "spreadsheet_id_for_tests"


class FakeTransport:
    """A stateful fake of the parts of Sheets repair touches."""

    def __init__(self, tabs: dict[str, list[list[str]]] | None = None) -> None:
        self.tabs = dict(tabs or {})
        self.sheet_ids = {title: index for index, title in enumerate(self.tabs)}
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.frozen = {title: (1, 3) for title in self.tabs}
        self.batches: list[list[dict[str, Any]]] = []
        self.value_updates: list[list[dict[str, Any]]] = []
        self.added_tabs: list[str] = []

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
                        },
                    },
                    "tables": self.tables.get(title, []),
                }
                for title in self.tabs
            ],
        }

    def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
        values = []
        for requested in ranges:
            tab = requested.split("!", 1)[0]
            rows = self.tabs.get(tab, [])
            if requested.endswith("!1:1"):
                rows = rows[:1]
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
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str | None = None,
    ) -> dict[str, Any]:
        self.value_updates.append(data)
        for entry in data:
            tab, _, a1 = entry["range"].partition("!")
            rows = self.tabs.setdefault(tab, [])
            first = a1.split(":")[0]
            row_number = int("".join(ch for ch in first if ch.isdigit()) or 1)
            column = "".join(ch for ch in first if ch.isalpha()) or "A"
            start = 0
            for character in column.upper():
                start = start * 26 + (ord(character) - ord("A") + 1)
            while len(rows) < row_number:
                rows.append([])
            target = rows[row_number - 1]
            for offset, value in enumerate(entry["values"][0]):
                index = start - 1 + offset
                while len(target) <= index:
                    target.append("")
                target[index] = value
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
                self.added_tabs.append(title)
            elif "addTable" in body:
                for title, sheet_id in self.sheet_ids.items():
                    if sheet_id == body["addTable"]["table"]["range"]["sheetId"]:
                        self.tables[title] = [{"tableId": "t1"}]
            elif "updateSheetProperties" in body:
                grid = body["updateSheetProperties"]["properties"].get("gridProperties")
                if grid:
                    for title, sheet_id in self.sheet_ids.items():
                        if sheet_id == body["updateSheetProperties"]["properties"]["sheetId"]:
                            self.frozen[title] = (
                                grid.get("frozenRowCount", 0),
                                grid.get("frozenColumnCount", 0),
                            )
            elif "updateTable" in body:
                for title, sheet_id in self.sheet_ids.items():
                    if sheet_id == body["updateTable"]["table"]["range"]["sheetId"]:
                        self.tables[title] = [{"tableId": "t1"}]
        return {}


def _client(transport: FakeTransport):
    from personal_job_finder_api.integrations.google_sheets.client import (
        GoogleSheetsClient,
    )

    return GoogleSheetsClient(transport)


def _healthy() -> FakeTransport:
    transport = FakeTransport(
        {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
    )
    transport.tables["Opportunities"] = [{"tableId": "t1"}]
    return transport


def _repair(transport: FakeTransport) -> repair.RepairOutcome:
    return repair.WorkspaceRepairService(_client(transport), SPREADSHEET_ID).repair()


# ------------------------------------------------------------------ idempotency


def test_a_healthy_workbook_needs_no_structural_change() -> None:
    transport = _healthy()

    outcome = _repair(transport)

    assert outcome.actions == ()
    assert outcome.adopted_rows == ()
    assert outcome.message() == "Workspace already matches the expected structure."


def test_repair_does_not_write_any_value_on_a_healthy_workbook() -> None:
    transport = _healthy()

    _repair(transport)

    assert transport.value_updates == []


def test_a_second_run_still_reports_no_change() -> None:
    transport = _healthy()

    _repair(transport)
    second = _repair(transport)

    assert second.actions == ()
    assert second.adopted_rows == ()


# --------------------------------------------------------------------- headers


def test_a_cleared_header_row_is_restored() -> None:
    transport = _healthy()
    transport.tabs["Opportunities"][0] = [""] * len(OPPORTUNITY_HEADERS)

    outcome = _repair(transport)

    assert "restored the Opportunities header row" in outcome.actions
    assert transport.tabs["Opportunities"][0] == list(OPPORTUNITY_HEADERS)


def test_a_reordered_header_row_is_restored() -> None:
    transport = _healthy()
    header = list(OPPORTUNITY_HEADERS)
    header[0], header[1] = header[1], header[0]
    transport.tabs["Opportunities"][0] = header

    _repair(transport)

    assert transport.tabs["Opportunities"][0] == list(OPPORTUNITY_HEADERS)


def test_header_repair_never_touches_a_data_row() -> None:
    transport = _healthy()
    transport.tabs["Opportunities"][0] = ["wrong"] * len(OPPORTUNITY_HEADERS)
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[0] = "opp_keep"
    row[1] = "Acme Example"
    transport.tabs["Opportunities"].append(row)

    _repair(transport)

    assert transport.tabs["Opportunities"][1][0] == "opp_keep"
    assert transport.tabs["Opportunities"][1][1] == "Acme Example"


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


def test_an_existing_table_is_never_duplicated() -> None:
    transport = _healthy()

    _repair(transport)

    assert not any("addTable" in body for batch in transport.batches for body in batch)


def test_an_existing_table_is_updated_when_the_panes_drifted() -> None:
    transport = _healthy()
    transport.frozen["Opportunities"] = (0, 0)

    outcome = _repair(transport)

    assert "updated the workspace table" in outcome.actions
    assert transport.frozen["Opportunities"] == (1, 3)


def test_force_reapplies_the_presentation_layer() -> None:
    transport = _healthy()

    outcome = repair.WorkspaceRepairService(_client(transport), SPREADSHEET_ID).repair(force=True)

    assert "updated the workspace table" in outcome.actions


def test_repair_never_deletes_or_clears_a_row() -> None:
    transport = _healthy()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[0] = "opp_1"
    transport.tabs["Opportunities"].append(row)

    _repair(transport)

    for batch in transport.batches:
        for body in batch:
            assert "deleteRange" not in body
            assert "deleteDimension" not in body
            assert "updateCells" not in body


def test_a_missing_canonical_tab_is_created() -> None:
    transport = FakeTransport({"Opportunities": [list(OPPORTUNITY_HEADERS)]})

    outcome = _repair(transport)

    assert "created the Job_Sources tab" in outcome.actions
    assert "Job_Sources" in transport.tabs


# --------------------------------------------------------------- manual rows


def test_a_manual_row_receives_a_deterministic_identity() -> None:
    transport = _healthy()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    row[2] = "Backend Engineer"
    transport.tabs["Opportunities"].append(row)

    outcome = _repair(transport)

    assert outcome.adopted_rows == (2,)
    identity = transport.tabs["Opportunities"][1][0]
    assert identity.startswith("opp_")
    assert len(identity) == len("opp_") + 32


def test_the_same_manual_row_gets_the_same_identity_every_time() -> None:
    first = repair.manual_identity(["", "Acme Example", "Backend Engineer"])
    second = repair.manual_identity(["", "Acme  Example ", "Backend   Engineer"])

    assert first == second


def test_a_manual_row_is_not_rewritten_after_adoption() -> None:
    transport = _healthy()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    row[2] = "Backend Engineer"
    transport.tabs["Opportunities"].append(row)

    _repair(transport)
    second = _repair(transport)

    assert second.adopted_rows == ()


def test_only_the_identity_cell_is_written_for_a_manual_row() -> None:
    transport = _healthy()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    row[2] = "Backend Engineer"
    row[21] = "saved"
    row[32] = "my own note"
    transport.tabs["Opportunities"].append(row)

    _repair(transport)

    entries = [entry for batch in transport.value_updates for entry in batch]
    assert len(entries) == 1
    assert entries[0]["range"] == "Opportunities!A2"
    assert len(entries[0]["values"][0]) == 1
    assert transport.tabs["Opportunities"][1][32] == "my own note"


def test_a_row_without_a_company_or_title_is_left_alone() -> None:
    transport = _healthy()
    row = [""] * len(OPPORTUNITY_HEADERS)
    row[1] = "Acme Example"
    transport.tabs["Opportunities"].append(row)

    outcome = _repair(transport)

    assert outcome.adopted_rows == ()
    assert transport.tabs["Opportunities"][1][0] == ""


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
    transport = _healthy()
    transport.tabs["Opportunities"][0] = ["wrong"] * len(OPPORTUNITY_HEADERS)

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
