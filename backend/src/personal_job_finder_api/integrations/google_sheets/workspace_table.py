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
    OPPORTUNITY_HEADERS,
)

OPPORTUNITY_TAB: Final = "Opportunities"

TABLE_NAME: Final = "OpportunitiesTable"

#: The table rectangle starts at row 1 so the header is inside it. The row count is
#: generous: Sheets grows the range as rows are appended, and a table that stopped
#: at the current last row would silently exclude the next discovery.
TABLE_ROWS: Final = 5_000

#: Columns the user owns, with the vocabulary a dropdown offers. A column absent
#: from this mapping is plain text.
#:
#: The table's ``DROPDOWN`` column type supplies both the picker and its list, and
#: Sheets accepts any other value in such a cell: live verification stored the
#: free text ``Ghosted`` in ``application_status`` unchanged. A separate
#: ``setDataValidation`` request is therefore both unnecessary and rejected —
#: "This operation is not allowed on cells in typed columns."
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

#: Frozen so the identity stays visible while scrolling a wide table.
FROZEN_ROWS: Final = 1
FROZEN_COLUMNS: Final = 3


def column_type(name: str) -> str:
    """The Sheets column type for one canonical column.

    Only the user-owned pickers are typed. Everything else stays TEXT so Sheets
    never coerces an id, a timestamp, or a tracker date into a number.
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
    return {
        "condition": {
            "type": "ONE_OF_LIST",
            "values": [{"userEnteredValue": value} for value in values],
        }
    }


def build_table(sheet_id: int, rows: int | None = None) -> dict[str, object]:
    """The native Table definition, including its dropdown columns.

    ``rows`` is the sheet's real row count when known: a table cannot extend past
    the grid, and the provider clamps an oversized range instead of failing, which
    would leave the definition and the sheet disagreeing.
    """

    row_count = TABLE_ROWS if rows is None else min(TABLE_ROWS, max(rows, 2))

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
            "startRowIndex": 0,
            "endRowIndex": row_count,
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


def build_add_table_request(sheet_id: int, rows: int | None = None) -> dict[str, object]:
    return {"addTable": {"table": build_table(sheet_id, rows)}}


def build_update_table_request(
    sheet_id: int, table_id: str, rows: int | None = None
) -> dict[str, object]:
    """Replace the whole table definition, which is idempotent by construction.

    ``tableId`` is required: without it the provider rejects the update because it
    cannot tell which table is being replaced.
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
    "OPPORTUNITY_TAB",
    "TABLE_NAME",
    "TABLE_ROWS",
    "TEXT_DATE_COLUMNS",
    "TableState",
    "USER_DROPDOWNS",
    "build_add_table_request",
    "build_freeze_request",
    "build_table",
    "build_update_table_request",
    "column_index_of",
    "column_type",
    "state_from_metadata",
]
