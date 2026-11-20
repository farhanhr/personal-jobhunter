"""Tests for reading cells with formula, effective value, and error state.

A values read returns displayed text, so a correct number and a ``#ERROR!`` look
the same. These tests pin the distinction the live verification depends on, and
they mirror the provider's real response shape: grid data is grouped by sheet, in
no guaranteed request order, with one data block per requested range.
"""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleSheetsClient,
    GridCell,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

SPREADSHEET_ID = "spreadsheet_id_for_tests"


class GridTransport:
    """Minimal transport that returns a canned grid response."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[list[str]] = []

    def get_spreadsheet_grid(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
        self.calls.append(list(ranges))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _block(
    rows: list[list[Any]],
    *,
    start_row: int | None = 0,
    start_column: int | None = 0,
) -> dict:
    block: dict[str, Any] = {"rowData": [{"values": row} for row in rows]}
    if start_row is not None:
        block["startRow"] = start_row
    if start_column is not None:
        block["startColumn"] = start_column
    return block


def _sheet(title: str, *blocks: dict) -> dict:
    return {"properties": {"title": title}, "data": list(blocks)}


def _formula(text: str, value: Any = None) -> dict:
    cell: dict[str, Any] = {"userEnteredValue": {"formulaValue": text}}
    if isinstance(value, dict):
        cell["effectiveValue"] = value
    elif value is not None:
        cell["effectiveValue"] = {"numberValue": value}
    return cell


def test_a_computed_formula_exposes_its_value_and_no_error() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block([[_formula("=1+1", 2)]], start_row=5, start_column=1),
                )
            ]
        }
    )

    grid = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B6"])

    cell = grid["Dashboard!B6"][0][0]
    assert cell.formula == "=1+1"
    assert cell.value == "2"
    assert cell.error is None
    assert cell.has_error is False
    assert cell.address == "B6"


def test_a_failed_formula_exposes_the_error_type_and_no_value() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block(
                        [
                            [
                                _formula(
                                    '=TEXT(YEAR(TODAY()),"0000")',
                                    {
                                        "errorValue": {
                                            "type": "ERROR",
                                            "message": "parse",
                                        }
                                    },
                                )
                            ]
                        ],
                        start_row=1,
                        start_column=1,
                    ),
                )
            ]
        }
    )

    grid = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B2"])

    cell = grid["Dashboard!B2"][0][0]
    assert cell.formula is not None
    assert cell.error == "ERROR"
    assert cell.value is None
    assert cell.has_error is True


@pytest.mark.parametrize(
    "error_type",
    ["ERROR", "REF", "VALUE", "NAME", "N/A", "DIV/0!"],
)
def test_every_provider_error_type_is_preserved(error_type: str) -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block(
                        [[_formula("=X", {"errorValue": {"type": error_type}})]],
                        start_row=5,
                        start_column=1,
                    ),
                )
            ]
        }
    )

    grid = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B6"])

    assert grid["Dashboard!B6"][0][0].error == error_type


def test_a_literal_cell_has_no_formula() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block(
                        [[{"userEnteredValue": {"stringValue": "Total"}}]],
                        start_row=5,
                        start_column=0,
                    ),
                )
            ]
        }
    )

    cell = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!A6"])["Dashboard!A6"][
        0
    ][0]

    assert cell.formula is None
    assert cell.literal == "Total"


def test_a_missing_data_block_is_an_empty_range() -> None:
    transport = GridTransport({"sheets": [{"properties": {"title": "Dashboard"}}]})

    assert (
        GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!A1:A2"])[
            "Dashboard!A1:A2"
        ]
        == []
    )


def test_a_range_no_block_covers_is_empty() -> None:
    transport = GridTransport(
        {"sheets": [_sheet("Dashboard", _block([[{"userEnteredValue": {"stringValue": "x"}}]]))]}
    )

    assert (
        GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B6"])["Dashboard!B6"]
        == []
    )


def test_absent_positions_inside_a_covered_block_are_inert_cells() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block([[{"userEnteredValue": {"stringValue": "only"}}]]),
                )
            ]
        }
    )

    row = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!A1:C1"])[
        "Dashboard!A1:C1"
    ][0]

    assert [cell.literal for cell in row] == ["only", None, None]
    assert [cell.address for cell in row] == ["A1", "B1", "C1"]


def test_cell_addresses_follow_the_reported_start_offset() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block(
                        [[_formula("=1", 1), _formula("=2", 2)]],
                        start_row=5,
                        start_column=1,
                    ),
                )
            ]
        }
    )

    row = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B6:C6"])[
        "Dashboard!B6:C6"
    ][0]

    assert [cell.address for cell in row] == ["B6", "C6"]


def test_boolean_and_string_effective_values_are_rendered() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block(
                        [
                            [
                                _formula("=TRUE", {"boolValue": True}),
                                _formula('="x"', {"stringValue": "x"}),
                            ]
                        ],
                        start_row=0,
                        start_column=1,
                    ),
                )
            ]
        }
    )

    row = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B1:C1"])[
        "Dashboard!B1:C1"
    ][0]

    assert [cell.value for cell in row] == ["TRUE", "x"]


def test_several_blocks_in_one_sheet_are_matched_to_their_own_ranges() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Dashboard",
                    _block(
                        [[{"userEnteredValue": {"stringValue": "first"}}]],
                        start_row=1,
                        start_column=1,
                    ),
                    _block(
                        [[{"userEnteredValue": {"stringValue": "second"}}]],
                        start_row=5,
                        start_column=1,
                    ),
                )
            ]
        }
    )

    grid = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!B2", "Dashboard!B6"])

    assert grid["Dashboard!B2"][0][0].literal == "first"
    assert grid["Dashboard!B6"][0][0].literal == "second"


def test_sheets_come_back_in_a_different_order_than_requested() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "Opportunities",
                    _block([[{"userEnteredValue": {"stringValue": "second"}}]]),
                ),
                _sheet(
                    "Dashboard",
                    _block([[{"userEnteredValue": {"stringValue": "first"}}]]),
                ),
            ]
        }
    )

    grid = GoogleSheetsClient(transport).get_grid(
        SPREADSHEET_ID, ["Dashboard!A1", "Opportunities!A1"]
    )

    assert grid["Dashboard!A1"][0][0].literal == "first"
    assert grid["Opportunities!A1"][0][0].literal == "second"
    assert transport.calls == [["Dashboard!A1", "Opportunities!A1"]]


def test_one_sheet_with_many_blocks_maps_every_range() -> None:
    """Regression: a 25-cell read returns one sheet with 25 blocks, not 25 sheets."""

    addresses = [f"B{row}" for row in range(2, 27)]
    blocks = [
        _block([[{"userEnteredValue": {"numberValue": row}}]], start_row=row - 1, start_column=1)
        for row in range(2, 27)
    ]
    transport = GridTransport({"sheets": [_sheet("Dashboard", *blocks)]})

    grid = GoogleSheetsClient(transport).get_grid(
        SPREADSHEET_ID, [f"Dashboard!{address}" for address in addresses]
    )

    for row, address in enumerate(addresses, start=2):
        assert grid[f"Dashboard!{address}"][0][0].literal == str(row)


def test_a_quoted_sheet_title_is_matched() -> None:
    transport = GridTransport(
        {
            "sheets": [
                _sheet(
                    "My Sheet",
                    _block([[{"userEnteredValue": {"stringValue": "quoted"}}]]),
                )
            ]
        }
    )

    grid = GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["'My Sheet'!A1"])

    assert grid["'My Sheet'!A1"][0][0].literal == "quoted"


@pytest.mark.parametrize("range_name", ["Dashboard", "Dashboard!", "Dashboard!A", "Dashboard!0"])
def test_an_invalid_a1_range_is_refused(range_name: str) -> None:
    transport = GridTransport({"sheets": []})

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, [range_name])

    assert captured.value.code == "invalid_range"


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"sheets": "nope"},
        {"sheets": [{"properties": {"title": "Dashboard"}, "data": "nope"}]},
        {"sheets": [{"properties": {"title": ""}, "data": []}]},
        {"sheets": [{"data": []}]},
        {"sheets": [{"properties": {"title": "Dashboard"}, "data": [{"rowData": "nope"}]}]},
        {"sheets": [{"properties": {"title": "Dashboard"}, "data": [{"startRow": "x"}]}]},
        {
            "sheets": [
                {
                    "properties": {"title": "Dashboard"},
                    "data": [{"rowData": [{"values": [{"effectiveValue": {"errorValue": {}}}]}]}],
                }
            ]
        },
        {
            "sheets": [
                {
                    "properties": {"title": "Dashboard"},
                    "data": [{"rowData": [{"values": [{"formattedValue": 1}]}]}],
                }
            ]
        },
    ],
)
def test_a_malformed_grid_response_is_refused(response: Any) -> None:
    transport = GridTransport(response)

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, ["Dashboard!A1"])

    assert captured.value.code == "malformed_response"


def test_an_empty_range_list_makes_no_request() -> None:
    transport = GridTransport({"sheets": []})

    assert GoogleSheetsClient(transport).get_grid(SPREADSHEET_ID, []) == {}
    assert transport.calls == []


def test_a_grid_read_over_the_batch_limit_is_refused_before_transport() -> None:
    transport = GridTransport({"sheets": []})
    client = GoogleSheetsClient(transport, max_batch_ranges=2)

    with pytest.raises(GoogleSheetsError) as captured:
        client.get_grid(SPREADSHEET_ID, ["A1", "A2", "A3"])

    assert captured.value.code == "batch_too_large"
    assert transport.calls == []


def test_grid_cell_defaults_are_inert() -> None:
    cell = GridCell(address="A1")

    assert cell.formula is None
    assert cell.literal is None
    assert cell.value is None
    assert cell.error is None
    assert cell.has_error is False
