"""Layout for the ``Dashboard`` summary cards.

The cards are the visual companion to the editable workspace table: six numbers a
user can read without opening the application. They are formula-only, never
canonical, and never read by preflight, refresh, or the sync dispatcher.

Every card is a distinct-count formula over canonical rows, built from the same
primitives as the metric board in :mod:`dashboard_layout`:

- the row bound comes from ``MAX(FILTER(ROW(...)))``, because the Excel
  ``LOOKUP(2,1/(...),ROW(...))`` idiom does not array-expand in Google Sheets and
  silently pinned every range to row 2;
- a zero-match guard runs before ``FILTER`` so an empty sheet yields ``0`` instead
  of counting ``#N/A`` as one value;
- genuine errors are left uncaught, so a broken reference stays visible.

The primitives are imported, not copied: a second copy of the guard is a second
place for the row-2 bug to return.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as metrics
from personal_job_finder_api.integrations.google_sheets.dashboard_layout import (
    ARG,
    DEFAULT_DIALECT,
    FormulaDialect,
    column_index,
)

DASHBOARD_TAB: Final = "Dashboard"

#: Versioned independently of the metric board. The two tabs have different
#: layouts and are classified separately, so one sentinel cannot vouch for both.
DASHBOARD_SENTINEL: Final = "PJF_DASHBOARD_CARDS_V1"
DASHBOARD_SENTINEL_PREFIX: Final = "PJF_DASHBOARD_"

#: A bare sheet name addresses the whole sheet, so no fixed boundary is assumed
#: when deciding whether an existing tab is empty.
INSPECT_RANGE: Final = DASHBOARD_TAB

SENTINEL_CELL: Final = "M1"

#: Canonical columns the cards read.
OPPORTUNITY_ID_COLUMN: Final = "A"
LOCATION_STATUS_COLUMN: Final = "L"
USER_STATUS_COLUMN: Final = "V"
DATE_APPLIED_COLUMN: Final = "AB"
APPLICATION_STATUS_COLUMN: Final = "AC"
INTERVIEW_DATE_COLUMN: Final = "AE"

#: ``user_status`` value meaning "the user archived this row". It already exists in
#: the canonical contract (ADR-018), so archiving needs no new column.
ARCHIVED_USER_STATUS: Final = "ignored"

#: ``location_status`` value for a row outside the location scope.
EXCLUDED_LOCATION_STATUS: Final = "excluded"

CARD_LABELS: Final = (
    "Need to Apply",
    "Applied (last 7 days)",
    "Interviews (next 7 days)",
    "Offers",
    "Active opportunities",
    "Archived or excluded",
)

#: Card value cells, in the same order as :data:`CARD_LABELS`.
CARD_CELLS: Final = ("B3", "B4", "B5", "B6", "B7", "B8")

_CARD_CAPTIONS: Final = (
    "No application recorded yet, or marked Need to Apply.",
    "date_applied inside the last seven Jakarta dates.",
    "interview_date from today through the next seven days.",
    "Status reads Offer.",
    "Not excluded and not archived.",
    "location_status excluded, or user_status ignored.",
)


@dataclass(frozen=True, slots=True)
class DashboardCell:
    """One written cell. Content starting with ``=`` is a formula."""

    address: str
    content: str

    @property
    def is_formula(self) -> bool:
        return self.content.startswith("=")


def _cell(address: str, content: str) -> DashboardCell:
    return DashboardCell(address=address, content=content)


#: This tab's own row bound. The cards must not depend on the metric board,
#: which is optional and may not exist at all.
BOUND_CELL: Final = "H1"

_ID = metrics._opportunities_range(OPPORTUNITY_ID_COLUMN, "$H$1")
_LOCATION_STATUS = metrics._opportunities_range(LOCATION_STATUS_COLUMN, "$H$1")
_USER_STATUS = metrics._opportunities_range(USER_STATUS_COLUMN, "$H$1")
_DATE_APPLIED = metrics._opportunities_range(DATE_APPLIED_COLUMN, "$H$1")
_APPLICATION_STATUS = metrics._opportunities_range(APPLICATION_STATUS_COLUMN, "$H$1")
_INTERVIEW_DATE = metrics._opportunities_range(INTERVIEW_DATE_COLUMN, "$H$1")

_ROW_EXISTS = f'{_ID}<>""'

#: Excluded and archived rows are both out of the active pipeline. Each term is a
#: 0/1 array, so multiplying them is an AND that works inside ``SUMPRODUCT`` and
#: inside ``FILTER`` without array-formula syntax.
_ACTIVE = (
    f'({_LOCATION_STATUS}<>"{EXCLUDED_LOCATION_STATUS}")*({_USER_STATUS}<>"{ARCHIVED_USER_STATUS}")'
)

#: Either reason a row left the pipeline. ``>0`` keeps it a single 0/1 array.
_INACTIVE = (
    f'(({_LOCATION_STATUS}="{EXCLUDED_LOCATION_STATUS}")'
    f'+({_USER_STATUS}="{ARCHIVED_USER_STATUS}")>0)'
)


def _status_is(label: str) -> str:
    """Case-insensitive, whitespace-tolerant status comparison."""

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
    """Add whole metric formulas, keeping one leading ``=``."""

    head, *rest = formulas
    return head + "".join("+" + formula.removeprefix("=") for formula in rest)


#: A blank status and the literal "need to apply" both mean "not applied yet".
#: They are disjoint, so two distinct counts can simply be added.
_NEED_TO_APPLY = _sum_of(
    _blank_status(),
    _active_distinct(_status_is("Need to Apply")),
)

_APPLIED_7D = _active_distinct(
    metrics._date_in_window(_DATE_APPLIED, "TODAY()-6", "TODAY()"),
)

_INTERVIEWS_7D = _active_distinct(
    metrics._date_in_window(_INTERVIEW_DATE, "TODAY()", "TODAY()+7"),
)

_OFFERS = _active_distinct(_status_is("Offer"))

_ACTIVE_TOTAL = _active_distinct()

_ARCHIVED = metrics._distinct(_ID, _ROW_EXISTS, _INACTIVE)

_CARD_FORMULAS: Final = (
    _NEED_TO_APPLY,
    _APPLIED_7D,
    _INTERVIEWS_7D,
    _OFFERS,
    _ACTIVE_TOTAL,
    _ARCHIVED,
)

_AS_OF = (
    f'=TEXT(YEAR(TODAY()){ARG}"0000")&"-"&TEXT(MONTH(TODAY()){ARG}"00")'
    f'&"-"&TEXT(DAY(TODAY()){ARG}"00")'
)


def visible_cells(dialect: FormulaDialect = DEFAULT_DIALECT) -> tuple[DashboardCell, ...]:
    """The cards a user reads, rendered for ``dialect``."""

    cells: list[DashboardCell] = [
        _cell("A1", "Job Search Dashboard — read-only summary of Opportunities"),
        _cell("A2", "As of (Asia/Jakarta)"),
        _cell("B2", dialect.render(_AS_OF)),
    ]

    for offset, (label, formula, caption) in enumerate(
        zip(CARD_LABELS, _CARD_FORMULAS, _CARD_CAPTIONS, strict=True)
    ):
        row = 3 + offset
        cells.append(_cell(f"A{row}", label))
        cells.append(_cell(f"B{row}", dialect.render(formula)))
        cells.append(_cell(f"C{row}", caption))

    cells.extend(
        [
            _cell("A11", "Edit the Opportunities tab; this tab only reads it."),
            _cell("A12", "Counts use opportunity_id, so a repeated row counts once."),
            _cell(
                "A13",
                "Archived rows have user_status = ignored. Excluded rows are outside "
                "the location scope.",
            ),
        ]
    )
    return tuple(cells)


def helper_cells(dialect: FormulaDialect = DEFAULT_DIALECT) -> tuple[DashboardCell, ...]:
    """Hidden machinery: the last populated Opportunities row."""

    return (_cell(BOUND_CELL, dialect.render(metrics._LAST_OPPORTUNITY_ROW)),)


def sentinel_cell() -> DashboardCell:
    """The ownership marker, written last in the same batch as the layout."""

    return _cell(SENTINEL_CELL, DASHBOARD_SENTINEL)


def all_cells(dialect: FormulaDialect = DEFAULT_DIALECT) -> tuple[DashboardCell, ...]:
    return (*visible_cells(dialect), *helper_cells(dialect), sentinel_cell())


def metric_cells() -> tuple[str, ...]:
    """The cells that hold a card value, in a stable order."""

    return tuple(sorted(CARD_CELLS, key=lambda address: (address[0], int(address[1:]))))


def parse_address(address: str) -> tuple[int, int]:
    """Return the 0-based ``(row, column)`` of an A1 address."""

    return metrics.parse_address(address)


def build_layout_requests(
    sheet_id: int,
    dialect: FormulaDialect = DEFAULT_DIALECT,
) -> list[dict[str, object]]:
    """Write every cell in one atomic ``updateCells`` request."""

    cells = all_cells(dialect)
    rows = [parse_address(cell.address)[0] for cell in cells]
    columns = [parse_address(cell.address)[1] for cell in cells]
    minimum_row, maximum_row = min(rows), max(rows)
    minimum_column, maximum_column = min(columns), max(columns)

    grid: list[list[dict[str, object]]] = [
        [{"userEnteredValue": {}} for _ in range(maximum_column - minimum_column + 1)]
        for _ in range(maximum_row - minimum_row + 1)
    ]
    for cell in cells:
        row, column = parse_address(cell.address)
        target = grid[row - minimum_row][column - minimum_column]
        if cell.is_formula:
            target["userEnteredValue"] = {"formulaValue": cell.content}
        else:
            target["userEnteredValue"] = {"stringValue": cell.content}

    return [
        {
            "updateCells": {
                "start": {
                    "sheetId": sheet_id,
                    "rowIndex": minimum_row,
                    "columnIndex": minimum_column,
                },
                "rows": [{"values": row} for row in grid],
                "fields": "userEnteredValue",
            }
        }
    ]


def _range(
    sheet_id: int, start_row: int, end_row: int, start_column: int, end_column: int
) -> dict[str, object]:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_column,
        "endColumnIndex": end_column,
    }


def build_format_requests(sheet_id: int) -> list[dict[str, object]]:
    """Presentation only: card styling and column widths."""

    requests: list[dict[str, object]] = [
        {
            "repeatCell": {
                "range": _range(sheet_id, 0, 1, 0, 8),
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 13}}},
                "fields": "userEnteredFormat.textFormat",
            }
        }
    ]

    for row in range(2, 8):
        requests.append(
            {
                "repeatCell": {
                    "range": _range(sheet_id, row, row + 1, 0, 1),
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
        # The number is the point of the card, so it is large and centred.
        requests.append(
            {
                "repeatCell": {
                    "range": _range(sheet_id, row, row + 1, 1, 2),
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 16},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": (
                        "userEnteredFormat.textFormat,userEnteredFormat.horizontalAlignment"
                    ),
                }
            }
        )

    # The helper column is machinery, not content.
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": column_index("H"),
                    "endIndex": column_index("H") + 1,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }
    )
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": column_index("A"),
                    "endIndex": column_index("B"),
                },
                "properties": {"pixelSize": 220},
                "fields": "pixelSize",
            }
        }
    )
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": column_index("B"),
                    "endIndex": column_index("C"),
                },
                "properties": {"pixelSize": 80},
                "fields": "pixelSize",
            }
        }
    )
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": column_index("C"),
                    "endIndex": column_index("D"),
                },
                "properties": {"pixelSize": 360},
                "fields": "pixelSize",
            }
        }
    )
    return requests


def build_initialization_requests(
    sheet_id: int,
    dialect: FormulaDialect = DEFAULT_DIALECT,
) -> list[dict[str, object]]:
    """Layout, formatting, then the sentinel, in one atomic batch.

    The sentinel is last so a written sentinel always implies a complete layout.
    """

    row, column = parse_address(SENTINEL_CELL)
    return [
        *build_layout_requests(sheet_id, dialect),
        *build_format_requests(sheet_id),
        {
            "updateCells": {
                "start": {
                    "sheetId": sheet_id,
                    "rowIndex": row,
                    "columnIndex": column,
                },
                "rows": [{"values": [{"userEnteredValue": {"stringValue": DASHBOARD_SENTINEL}}]}],
                "fields": "userEnteredValue",
            }
        },
    ]
