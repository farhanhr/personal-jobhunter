"""Tests for the editable workspace table on the canonical tab.

The table is presentation over the canonical rows, so these assertions are about
the contract: every canonical column is named, the table starts below the KPI
strip, every column stays TEXT, and the user-owned pickers are non-strict cell
validation so they appear on every data row.
"""

from __future__ import annotations

import pytest

from personal_job_finder_api.integrations.google_sheets import workspace_table as table
from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_HEADER_ROW,
    OPPORTUNITY_HEADERS,
)


def _table(sheet_id: int = 11) -> dict:
    return table.build_table(sheet_id)


def _column(properties: list[dict], name: str) -> dict:
    return next(entry for entry in properties if entry["columnName"] == name)


# --------------------------------------------------------------------- shape


def test_the_table_targets_the_canonical_tab() -> None:
    assert table.OPPORTUNITY_TAB == "Opportunities"


def test_the_table_starts_at_the_header_row() -> None:
    range_ = _table()["range"]

    assert range_["startRowIndex"] == OPPORTUNITY_HEADER_ROW - 1
    assert range_["startColumnIndex"] == 0
    assert range_["sheetId"] == 11


def test_the_table_has_room_to_grow() -> None:
    """A table that stopped at today's last row would exclude tomorrow's finds."""

    assert _table()["range"]["endRowIndex"] >= OPPORTUNITY_HEADER_ROW + 1_000


def test_the_helper_column_is_appended_only_when_missing() -> None:
    """The KPI strip's bound needs a column right of the last canonical column."""

    assert table.build_helper_column_request(11, 33) == {
        "appendDimension": {"sheetId": 11, "dimension": "COLUMNS", "length": 1}
    }
    assert table.build_helper_column_request(11, 34) is None


def test_the_helper_column_is_hidden() -> None:
    request = table.build_hide_helper_request(11)["updateDimensionProperties"]

    assert request["range"]["startIndex"] == len(OPPORTUNITY_HEADERS)
    assert request["properties"]["hiddenByUser"] is True


def test_every_canonical_column_is_named_in_order() -> None:
    properties = _table()["columnProperties"]

    assert len(properties) == len(OPPORTUNITY_HEADERS)
    for index, name in enumerate(OPPORTUNITY_HEADERS):
        entry = properties[index]
        assert entry["columnIndex"] == index
        assert entry["columnName"] == name


def test_the_table_covers_every_canonical_column() -> None:
    assert _table()["range"]["endColumnIndex"] == len(OPPORTUNITY_HEADERS)


# ---------------------------------------------------------------- column types


def test_tracker_dates_stay_text() -> None:
    """A DATE format coerces typed text into a serial and breaks date parsing."""

    properties = _table()["columnProperties"]

    for name in table.TEXT_DATE_COLUMNS:
        assert _column(properties, name)["columnType"] == "TEXT", name


def test_only_user_owned_columns_are_dropdowns() -> None:
    properties = _table()["columnProperties"]

    dropdowns = {entry["columnName"] for entry in properties if entry["columnType"] == "DROPDOWN"}
    assert dropdowns == set(table.USER_DROPDOWNS)


def test_discovery_columns_are_text_so_sheets_never_coerces_them() -> None:
    properties = _table()["columnProperties"]

    for name in ("opportunity_id", "first_seen_at_utc", "posted_at", "description"):
        assert _column(properties, name)["columnType"] == "TEXT", name


def test_the_user_status_dropdown_offers_the_contract_values() -> None:
    rule = _column(_table()["columnProperties"], "user_status")["dataValidationRule"]

    offered = [value["userEnteredValue"] for value in rule["condition"]["values"]]
    assert offered == ["new", "saved", "ignored"]


def test_the_status_dropdown_offers_the_suggested_values() -> None:
    rule = _column(_table()["columnProperties"], "application_status")["dataValidationRule"]

    offered = [value["userEnteredValue"] for value in rule["condition"]["values"]]
    assert "Need to Apply" in offered
    assert "Offer" in offered
    assert len(offered) == 8


def test_the_dropdown_never_rejects_free_text() -> None:
    """ADR-021 keeps ``application_status`` free text with suggested values.

    A table column rule carries only ``condition``; ``strict`` is a cell-rule
    field and is rejected here. A ``DROPDOWN`` column accepts free text anyway.
    """

    for name in table.USER_DROPDOWNS:
        rule = _column(_table()["columnProperties"], name)["dataValidationRule"]
        assert set(rule) == {"condition"}
        assert rule["condition"]["type"] == "ONE_OF_LIST"


def test_the_table_is_the_only_validation_surface() -> None:
    """Google rejects ``setDataValidation`` on any sheet that carries a table.

    Verified live: even a ``TEXT`` column on a row outside the table is refused
    with "This operation is not allowed on cells in typed columns". The picker
    must therefore come from the table column, and the table must cover every
    grid row.
    """

    assert not hasattr(table, "build_validation_requests")


def test_the_table_covers_every_grid_row() -> None:
    """A table that stopped at the old grid size left later rows without a picker."""

    assert table.table_bounds(rows=1_004) == (
        OPPORTUNITY_HEADER_ROW - 1,
        1_004,
    )


def test_the_table_never_extends_past_a_short_grid() -> None:
    start, end = table.table_bounds(rows=OPPORTUNITY_HEADER_ROW + 10)

    assert start == OPPORTUNITY_HEADER_ROW - 1
    assert end == OPPORTUNITY_HEADER_ROW + 10


def test_the_table_is_capped_by_its_own_row_budget() -> None:
    _, end = table.table_bounds(rows=100_000)

    assert end == OPPORTUNITY_HEADER_ROW - 1 + table.TABLE_ROWS


# ------------------------------------------------------------------- framing


def test_the_strip_and_header_are_frozen() -> None:
    properties = table.build_freeze_request(11)["updateSheetProperties"]

    grid = properties["properties"]["gridProperties"]
    assert grid["frozenRowCount"] == OPPORTUNITY_HEADER_ROW
    assert grid["frozenColumnCount"] == 3
    assert "gridProperties.frozenRowCount" in properties["fields"]


def test_no_request_applies_a_number_format() -> None:
    """Nothing may reformat a tracker cell, because that rewrites user data."""

    for body in (
        table.build_add_table_request(11),
        table.build_update_table_request(11, "t1"),
        table.build_freeze_request(11),
    ):
        assert "repeatCell" not in body


def test_every_text_date_column_exists_in_the_contract() -> None:
    for name in table.TEXT_DATE_COLUMNS:
        assert table.column_index_of(name) >= 0


# ------------------------------------------------------------------- requests


def test_adding_and_updating_use_the_documented_request_names() -> None:
    assert "addTable" in table.build_add_table_request(11)
    assert "updateTable" in table.build_update_table_request(11, "t1")


def test_adding_defers_column_properties_until_the_update() -> None:
    """Live regression: ``addTable`` rewrites the header when given properties."""

    add = table.build_add_table_request(11)["addTable"]["table"]
    update = table.build_update_table_request(11, "t1")["updateTable"]["table"]

    assert "columnProperties" not in add
    assert len(update["columnProperties"]) == len(OPPORTUNITY_HEADERS)
    assert [entry["columnName"] for entry in update["columnProperties"]] == list(
        OPPORTUNITY_HEADERS
    )


def test_updating_replaces_every_field() -> None:
    body = table.build_update_table_request(11, "t1")

    assert body["updateTable"]["fields"] == "*"


def test_updating_names_the_table_it_replaces() -> None:
    """Without ``tableId`` the provider rejects the update outright."""

    body = table.build_update_table_request(11, "t1")

    assert body["updateTable"]["table"]["tableId"] == "t1"


@pytest.mark.parametrize(
    "body",
    [
        table.build_add_table_request(11),
        table.build_update_table_request(11, "t1"),
        table.build_freeze_request(11),
    ],
)
def test_no_request_writes_a_cell_value(body: dict) -> None:
    assert "updateCells" not in body
    assert "pasteData" not in body


# ---------------------------------------------------------------------- state


def test_a_missing_table_is_reported_as_absent() -> None:
    state = table.state_from_metadata({"sheets": [{"properties": {"title": "Opportunities"}}]})

    assert state.exists is False


def test_an_existing_table_is_detected() -> None:
    state = table.state_from_metadata(
        {
            "sheets": [
                {
                    "properties": {"title": "Opportunities"},
                    "tables": [{"tableId": "t1"}],
                }
            ]
        }
    )

    assert state.exists is True
    assert state.table_ids == ("t1",)


def test_metadata_without_the_tab_reports_absent() -> None:
    assert table.state_from_metadata({"sheets": []}).exists is False
