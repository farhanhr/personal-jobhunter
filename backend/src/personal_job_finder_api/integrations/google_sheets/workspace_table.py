"""The editable workspace table on the canonical ``Opportunities`` tab.

A mirror table cannot be edited: Google Sheets has no linked editable table, so
``QUERY``/``FILTER``/``ARRAYFORMULA`` results are formula cells that a user's typing
replaces. An editable table therefore has to live on the tab that owns the data.

That is why this module decorates ``Opportunities`` instead of copying it. The
canonical tab stays the single source of truth, and the table is presentation:
banded rows, a frozen header, and dropdowns on the columns the user owns.

The dropdown on ``application_status`` is deliberately **non-strict**. ADR-021
keeps that column free text with suggested values, so a strict list would reject a
legitimate value the user typed. The dropdown exists to make the common values easy
to pick, not to constrain them.

Nothing here changes a data value. Repair reconciles structure only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_HEADER_ROW,
    OPPORTUNITY_HEADERS,
)

OPPORTUNITY_TAB: Final = "Opportunities"

TABLE_NAME: Final = "OpportunitiesTable"

#: The table rectangle starts at the canonical header row, below the KPI strip.
#: The row count is generous: Sheets grows the range as rows are appended, and a
#: table that stopped at the current last row would silently exclude the next
#: discovery.
TABLE_ROWS: Final = 5_000

#: Columns the user owns, with the vocabulary a dropdown offers. A column absent
#: from this mapping is plain text.
#:
#: These must be the table's ``DROPDOWN`` column type. Google rejects
#: ``setDataValidation`` on **any** sheet that carries a native table — verified
#: live: "This operation is not allowed on cells in typed columns", even on a
#: ``TEXT`` column and even on a row outside the table. The picker therefore comes
#: from the table column, and the table must span every grid row so the picker is
#: available on every data row (ADR-027).
USER_DROPDOWNS: Final = {
    "user_status": ("new", "saved", "ignored"),
    "application_status": (
        "Need to Apply",
        "Applied",
        "Assessment",
        "Interview",
        "Offer",
        "Rejected",
        "Withdrawn",
        "No Feedback",
    ),
}

#: Tracker date columns are deliberately **not** formatted as dates. A DATE number
#: format makes Sheets coerce a typed ``2026-11-20`` into a serial number, and the
#: tracker parser reads text: live verification showed the stored cell became
#: ``46344`` and every date window silently counted zero. The contract is plain
#: text (P0/12, AGENTS.md "never rewrite what you typed"), so these stay TEXT.
TEXT_DATE_COLUMNS: Final = ("date_applied", "follow_up_date", "interview_date")

#: Freeze the KPI strip, the spacer, and the header, plus the identity columns.
FROZEN_ROWS: Final = OPPORTUNITY_HEADER_ROW
FROZEN_COLUMNS: Final = 3

#: The 0-based index of the hidden helper column (``AH``), just right of the
#: canonical ``AG``. The KPI strip keeps its row bound and its sentinel there.
HELPER_COLUMN_INDEX: Final = len(OPPORTUNITY_HEADERS)


def column_type(name: str) -> str:
    """The Sheets column type for one canonical column.

    Only the two user-owned pickers are typed, because a sheet with a native table
    has no other validation surface. Everything else stays TEXT so Sheets never
    coerces an id, a timestamp, or a tracker date into a number.
    """

    if name in USER_DROPDOWNS:
        return "DROPDOWN"
    return "TEXT"


def column_index_of(name: str) -> int:
    """The 0-based position of a canonical column, or ``-1`` when absent."""

    try:
        return OPPORTUNITY_HEADERS.index(name)
    except ValueError:
        return -1


def _dropdown_rule(values: tuple[str, ...]) -> dict[str, object]:
    """The table column's picker.

    A table column's rule accepts only ``condition``. ``strict`` and
    ``showCustomUi`` belong to cell validation and are rejected here, and a
    ``DROPDOWN`` table column already accepts free text: live verification stored
    ``Ghosted`` unchanged.
    """

    return {
        "condition": {
            "type": "ONE_OF_LIST",
            "values": [{"userEnteredValue": value} for value in values],
        }
    }


def table_bounds(rows: int | None = None) -> tuple[int, int]:
    """The native table's 0-based ``[start, end)`` rows on the sheet grid.

    ``rows`` is the sheet's real row count when known: a table cannot extend past
    the grid, and the provider clamps an oversized range instead of failing, which
    would leave the definition and the sheet disagreeing. The table always reaches
    the last grid row, so a row the grid grew later still shows the picker.
    """

    start = OPPORTUNITY_HEADER_ROW - 1
    end = start + TABLE_ROWS if rows is None else max(rows, start + 2)
    return start, min(end, start + TABLE_ROWS)


def build_table(sheet_id: int, rows: int | None = None) -> dict[str, object]:
    """The native Table definition.

    The table starts at the canonical header row and spans the whole grid, because
    the dropdown picker only exists inside a table. Its columns are TEXT except
    the two user-owned pickers, which are ``DROPDOWN``.
    """

    start, end = table_bounds(rows)

    properties: list[dict[str, object]] = []
    for index, name in enumerate(OPPORTUNITY_HEADERS):
        entry: dict[str, object] = {
            "columnIndex": index,
            "columnName": name,
            "columnType": column_type(name),
        }
        if name in USER_DROPDOWNS:
            entry["dataValidationRule"] = _dropdown_rule(USER_DROPDOWNS[name])
        properties.append(entry)

    return {
        "name": TABLE_NAME,
        "range": {
            "sheetId": sheet_id,
            "startRowIndex": start,
            "endRowIndex": end,
            "startColumnIndex": 0,
            "endColumnIndex": len(OPPORTUNITY_HEADERS),
        },
        "rowsProperties": {
            "headerColorStyle": {"rgbColor": {"red": 0.11, "green": 0.16, "blue": 0.24}},
            "firstBandColorStyle": {"rgbColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
            "secondBandColorStyle": {"rgbColor": {"red": 0.96, "green": 0.97, "blue": 0.98}},
        },
        "columnProperties": properties,
    }


def build_helper_column_request(
    sheet_id: int,
    column_count: int,
) -> dict[str, object] | None:
    """Append and hide one column for the KPI strip's helper and sentinel.

    The canonical grid ends at the last header column, so the helper needs a
    column of its own. Hiding it keeps the workbook visually unchanged.
    """

    if column_count > HELPER_COLUMN_INDEX:
        return None
    return {
        "appendDimension": {
            "sheetId": sheet_id,
            "dimension": "COLUMNS",
            "length": HELPER_COLUMN_INDEX + 1 - column_count,
        }
    }


def build_hide_helper_request(sheet_id: int) -> dict[str, object]:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": HELPER_COLUMN_INDEX,
                "endIndex": HELPER_COLUMN_INDEX + 1,
            },
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }
    }


def build_add_table_request(sheet_id: int, rows: int | None = None) -> dict[str, object]:
    """Create the table without column properties.

    Live verification: ``addTable`` with ``columnProperties`` writes the supplied
    names into the header row and shifts them by the table's start row, corrupting
    the canonical header. The provider also rejects more than 29 properties on
    creation. Creating the table first and applying the properties with
    ``updateTable`` keeps the header intact and accepts the full canonical set.
    """

    body = build_table(sheet_id, rows)
    body.pop("columnProperties", None)
    return {"addTable": {"table": body}}


def build_update_table_request(
    sheet_id: int, table_id: str, rows: int | None = None
) -> dict[str, object]:
    """Replace the whole table definition, which is idempotent by construction.

    ``tableId`` is required: without it the provider rejects the update because it
    cannot tell which table is being replaced. This is also the only safe place to
    apply column names and dropdown rules, because ``addTable`` rewrites the
    header row when it receives them.
    """

    body = build_table(sheet_id, rows)
    body["tableId"] = table_id
    return {"updateTable": {"table": body, "fields": "*"}}


def build_freeze_request(sheet_id: int) -> dict[str, object]:
    """Freeze the header row and the identity columns."""

    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": FROZEN_ROWS,
                    "frozenColumnCount": FROZEN_COLUMNS,
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    }


@dataclass(frozen=True, slots=True)
class TableState:
    """What the live spreadsheet currently reports about the table."""

    table_ids: tuple[str, ...]
    header_row: tuple[str, ...]

    @property
    def exists(self) -> bool:
        return bool(self.table_ids)

    @property
    def header_matches(self) -> bool:
        return self.header_row == OPPORTUNITY_HEADERS


def state_from_metadata(raw: Mapping[str, object]) -> TableState:
    """Extract the table ids and header row for the Opportunities tab."""

    for sheet in raw.get("sheets", []) or []:
        properties = sheet.get("properties", {}) if isinstance(sheet, dict) else {}
        if properties.get("title") != OPPORTUNITY_TAB:
            continue
        tables = sheet.get("tables") or []
        table_ids = tuple(
            str(table.get("tableId"))
            for table in tables
            if isinstance(table, dict) and table.get("tableId")
        )
        return TableState(table_ids=table_ids, header_row=())
    return TableState(table_ids=(), header_row=())


__all__ = [
    "FROZEN_COLUMNS",
    "FROZEN_ROWS",
    "HELPER_COLUMN_INDEX",
    "OPPORTUNITY_TAB",
    "TABLE_NAME",
    "TABLE_ROWS",
    "TEXT_DATE_COLUMNS",
    "TableState",
    "USER_DROPDOWNS",
    "build_add_table_request",
    "build_freeze_request",
    "build_helper_column_request",
    "build_hide_helper_request",
    "build_table",
    "build_update_table_request",
    "column_index_of",
    "column_type",
    "state_from_metadata",
    "table_bounds",
]
