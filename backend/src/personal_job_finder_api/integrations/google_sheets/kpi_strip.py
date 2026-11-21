"""The four-card KPI strip at the top of the canonical ``Opportunities`` tab.

The strip is presentation only: four labels, four formulas, and four captions in
rows 1-3, with a spacer in row 4 and the canonical header in row 5. Nothing here
is a canonical value, no API exposes it, and no read-model column is derived from
it. The application has its own dashboard; this strip exists so the owner can see
the pipeline without leaving the sheet they edit.

Formulas reuse :mod:`dashboard_layout` so the strip and the optional ``Metrics``
board parse tracker dates and count distinct identities the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as metrics
from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_FIRST_DATA_ROW,
)

OPPORTUNITY_TAB: Final = "Opportunities"

#: The helper cell that holds the last populated canonical row. It sits just
#: right of the 33 canonical columns so it can never collide with a data cell.
BOUND_CELL: Final = "AH1"
BOUND_COLUMN: Final = "AH"
#: The helper cell plus the presentation sentinel directly below it. One read
#: covers both, and the range carries its tab because a grid read needs one.
BOUND_RANGE: Final = f"{OPPORTUNITY_TAB}!AH1:AH2"

#: Card geometry: four columns per card, four cards. A card occupies the label
#: row, the value row, and the caption row.
CARD_COLUMN_SPANS: Final = ((0, 3), (4, 7), (8, 11), (12, 15))
LABEL_ROW: Final = 0
VALUE_ROW: Final = 1
CAPTION_ROW: Final = 2
SPACER_ROW: Final = 3

_CARD_FILL: Final = {"red": 0.965, "green": 0.973, "blue": 0.984}
_BORDER_COLOR: Final = {"red": 0.82, "green": 0.85, "blue": 0.89}
_LABEL_COLOR: Final = {"red": 0.29, "green": 0.33, "blue": 0.41}
_VALUE_COLOR: Final = {"red": 0.07, "green": 0.11, "blue": 0.17}
_CAPTION_COLOR: Final = {"red": 0.45, "green": 0.49, "blue": 0.55}

#: Canonical columns the cards read.
_ID_COLUMN: Final = "A"
_LOCATION_STATUS_COLUMN: Final = "L"
_USER_STATUS_COLUMN: Final = "V"
_DATE_APPLIED_COLUMN: Final = "AB"
_APPLICATION_STATUS_COLUMN: Final = "AC"
_INTERVIEW_DATE_COLUMN: Final = "AE"

ARCHIVED_USER_STATUS: Final = "ignored"
EXCLUDED_LOCATION_STATUS: Final = "excluded"

ARG: Final = metrics.ARG


@dataclass(frozen=True, slots=True)
class KpiCard:
    """One card: its visible text and the formula behind its value."""

    label: str
    caption: str
    formula: str
    start_column: int
    end_column: int

    @property
    def value_address(self) -> str:
        return f"{_column_letter(self.start_column)}{VALUE_ROW + 1}"


@dataclass(frozen=True, slots=True)
class KpiCell:
    """One written cell. Content starting with ``=`` is a formula."""

    address: str
    content: str

    @property
    def is_formula(self) -> bool:
        return self.content.startswith("=")


def _column_letter(index: int) -> str:
    """0-based column index to its A1 letter (supports up to ``ZZ``)."""

    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _column_index(letters: str) -> int:
    """A1 column letters to a 0-based index."""

    value = 0
    for character in letters.upper():
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value - 1


def _range(column: str) -> str:
    return metrics._opportunities_range(column, f"${BOUND_COLUMN}$1")


_ID = _range(_ID_COLUMN)
_LOCATION_STATUS = _range(_LOCATION_STATUS_COLUMN)
_USER_STATUS = _range(_USER_STATUS_COLUMN)
_DATE_APPLIED = _range(_DATE_APPLIED_COLUMN)
_APPLICATION_STATUS = _range(_APPLICATION_STATUS_COLUMN)
_INTERVIEW_DATE = _range(_INTERVIEW_DATE_COLUMN)

_ROW_EXISTS = f'{_ID}<>""'

#: Excluded and archived rows are both out of the active pipeline. Each term is a
#: 0/1 array, so multiplying them is an AND usable inside ``SUMPRODUCT``/``FILTER``.
_ACTIVE = (
    f'({_LOCATION_STATUS}<>"{EXCLUDED_LOCATION_STATUS}")*({_USER_STATUS}<>"{ARCHIVED_USER_STATUS}")'
)


def _status_is(label: str) -> str:
    return f'LOWER(TRIM(TO_TEXT({_APPLICATION_STATUS})))="{label.lower()}"'


def _active_distinct(*conditions: str) -> str:
    return metrics._distinct(_ID, _ROW_EXISTS, _ACTIVE, *conditions)


def _blank_status() -> str:
    return metrics._distinct(
        _ID,
        _ROW_EXISTS,
        _ACTIVE,
        f'TRIM(TO_TEXT({_APPLICATION_STATUS}))=""',
    )


def _sum_of(*formulas: str) -> str:
    head, *rest = formulas
    return head + "".join("+" + formula.removeprefix("=") for formula in rest)


#: A blank status and the literal "need to apply" both mean "not applied yet".
#: They are disjoint, so two distinct counts can simply be added.
_NEED_TO_APPLY = _sum_of(_blank_status(), _active_distinct(_status_is("Need to Apply")))

#: Everything past "need to apply" counts as applied: an assessment, an
#: interview, an offer, a rejection, or any free text the owner typed.
_TOTAL_APPLIED = _active_distinct(
    f'TRIM(TO_TEXT({_APPLICATION_STATUS}))<>""',
    f"NOT({_status_is('Need to Apply')})",
)

_APPLIED_7D = _active_distinct(
    metrics._date_in_window(_DATE_APPLIED, "TODAY()-6", "TODAY()"),
)

_INTERVIEWS_7D = _active_distinct(
    metrics._date_in_window(_INTERVIEW_DATE, "TODAY()", "TODAY()+7"),
)

_CARD_TEMPLATES: Final = (
    (
        "Total Applied",
        "Status past Need to Apply.",
        _TOTAL_APPLIED,
    ),
    (
        "Need to Apply",
        "Blank status or marked Need to Apply.",
        _NEED_TO_APPLY,
    ),
    (
        "Applied (last 7 days)",
        "date_applied in the last seven Jakarta days.",
        _APPLIED_7D,
    ),
    (
        "Interviews (next 7 days)",
        "interview_date from today through the next seven days.",
        _INTERVIEWS_7D,
    ),
)

#: Last populated canonical row, never above the first data row. ``MAX(FILTER(...))``
#: finds the last populated position instead of counting cells, so an interior
#: blank identity cannot truncate the range.
#:
#: The range starts at the first data row on purpose: a full-column reference would
#: include the KPI cells that read this helper, and Google reports that circular
#: dependency as ``#REF!``.
_BOUND_FORMULA = (
    f"=IFERROR(MAX({OPPORTUNITY_FIRST_DATA_ROW}{ARG}"
    f"MAX(FILTER(ROW(Opportunities!$A${OPPORTUNITY_FIRST_DATA_ROW}:$A)"
    f'{ARG}Opportunities!$A${OPPORTUNITY_FIRST_DATA_ROW}:$A<>"")))'
    f"{ARG}{OPPORTUNITY_FIRST_DATA_ROW})"
)


def cards(dialect: metrics.FormulaDialect = metrics.DEFAULT_DIALECT) -> tuple[KpiCard, ...]:
    """The four cards, with formulas rendered for the spreadsheet locale."""

    rendered: list[KpiCard] = []
    for (label, caption, formula), (start, end) in zip(
        _CARD_TEMPLATES, CARD_COLUMN_SPANS, strict=True
    ):
        rendered.append(
            KpiCard(
                label=label,
                caption=caption,
                formula=dialect.render(formula),
                start_column=start,
                end_column=end,
            )
        )
    return tuple(rendered)


def cells(dialect: metrics.FormulaDialect = metrics.DEFAULT_DIALECT) -> tuple[KpiCell, ...]:
    """Every cell the strip writes, in a stable order."""

    written: list[KpiCell] = []
    for card in cards(dialect):
        label_column = _column_letter(card.start_column)
        written.append(KpiCell(f"{label_column}{LABEL_ROW + 1}", card.label))
        written.append(KpiCell(card.value_address, card.formula))
        written.append(KpiCell(f"{label_column}{CAPTION_ROW + 1}", card.caption))
    written.append(KpiCell(BOUND_CELL, dialect.render(_BOUND_FORMULA)))
    return tuple(written)


def _cell_range(sheet_id: int, row: int, start: int, end: int) -> dict[str, object]:
    return {
        "sheetId": sheet_id,
        "startRowIndex": row,
        "endRowIndex": row + 1,
        "startColumnIndex": start,
        "endColumnIndex": end + 1,
    }


def build_requests(
    sheet_id: int,
    dialect: metrics.FormulaDialect = metrics.DEFAULT_DIALECT,
) -> list[dict[str, object]]:
    """The complete strip layout: values, fills, borders, and row heights.

    No cell is merged. Google rejects a merge that spans a frozen and a
    non-frozen column, and the first three canonical columns are frozen. Each
    card is therefore a four-column block whose label, value, and caption live in
    the block's first cell and overflow into the empty cells beside it.
    """

    requests: list[dict[str, object]] = []

    for cell in cells(dialect):
        row, column = _address_parts(cell.address)
        value: dict[str, object]
        if cell.is_formula:
            value = {"userEnteredValue": {"formulaValue": cell.content}}
        else:
            value = {"userEnteredValue": {"stringValue": cell.content}}
        requests.append(
            {
                "updateCells": {
                    "range": _cell_range(sheet_id, row, column, column),
                    "rows": [{"values": [value]}],
                    "fields": "userEnteredValue",
                }
            }
        )

    requests.extend(_formatting_requests(sheet_id))
    requests.extend(_row_height_requests(sheet_id))
    return requests


def _address_parts(address: str) -> tuple[int, int]:
    """Split an A1 address into 0-based ``(row, column)``."""

    digits = address.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    letters = address[: len(address) - len(digits)]
    return int(digits) - 1, _column_index(letters)


def _formatting_requests(sheet_id: int) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    # One continuous card background per block, then the text style on the cell
    # that owns the content.
    for start, end in CARD_COLUMN_SPANS:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": LABEL_ROW,
                        "endRowIndex": CAPTION_ROW + 1,
                        "startColumnIndex": start,
                        "endColumnIndex": end + 1,
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": _CARD_FILL}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        )

    styles = (
        (
            LABEL_ROW,
            {
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "MIDDLE",
                "padding": {"top": 6, "bottom": 0, "left": 12, "right": 8},
                "textFormat": {
                    "fontSize": 9,
                    "bold": True,
                    "foregroundColor": _LABEL_COLOR,
                },
            },
        ),
        (
            VALUE_ROW,
            {
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "MIDDLE",
                "padding": {"top": 0, "bottom": 4, "left": 12, "right": 8},
                "textFormat": {
                    "fontSize": 24,
                    "bold": True,
                    "foregroundColor": _VALUE_COLOR,
                },
            },
        ),
        (
            CAPTION_ROW,
            {
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "TOP",
                "padding": {"top": 0, "bottom": 6, "left": 12, "right": 8},
                "textFormat": {
                    "fontSize": 9,
                    "foregroundColor": _CAPTION_COLOR,
                },
            },
        ),
    )
    for row, style in styles:
        for start, _ in CARD_COLUMN_SPANS:
            requests.append(
                {
                    "repeatCell": {
                        "range": _cell_range(sheet_id, row, start, start),
                        "cell": {"userEnteredFormat": style},
                        "fields": "userEnteredFormat",
                    }
                }
            )

    for start, end in CARD_COLUMN_SPANS:
        requests.append(
            {
                "updateBorders": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": LABEL_ROW,
                        "endRowIndex": CAPTION_ROW + 1,
                        "startColumnIndex": start,
                        "endColumnIndex": end + 1,
                    },
                    "top": {"style": "SOLID", "color": _BORDER_COLOR},
                    "bottom": {"style": "SOLID", "color": _BORDER_COLOR},
                    "left": {"style": "SOLID", "color": _BORDER_COLOR},
                    "right": {"style": "SOLID", "color": _BORDER_COLOR},
                    "innerHorizontal": {"style": "SOLID", "color": _BORDER_COLOR},
                }
            }
        )
    return requests


def _row_height_requests(sheet_id: int) -> list[dict[str, object]]:
    heights = ((LABEL_ROW, 20), (VALUE_ROW, 42), (CAPTION_ROW, 24), (SPACER_ROW, 8))
    return [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row,
                    "endIndex": row + 1,
                },
                "properties": {"pixelSize": height},
                "fields": "pixelSize",
            }
        }
        for row, height in heights
    ]


__all__ = [
    "BOUND_CELL",
    "BOUND_RANGE",
    "CARD_COLUMN_SPANS",
    "KpiCard",
    "KpiCell",
    "OPPORTUNITY_TAB",
    "build_requests",
    "cards",
    "cells",
]
