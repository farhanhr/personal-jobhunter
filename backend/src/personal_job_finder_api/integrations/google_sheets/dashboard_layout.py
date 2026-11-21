"""Pure layout and formula definitions for the optional ``Metrics`` board.

The Metrics tab is a companion view, never a canonical store. This module
holds only text: labels, addresses, formula strings, and Google API request
dictionaries. It imports nothing from the transport, the database, or the
service layer, so the whole layout can be golden-tested without a network.

Formula parity with the backend is deliberate:

- ``Asia/Jakarta`` calendar dates come from ``TODAY()``, which requires the
  spreadsheet time zone to be ``Asia/Jakarta``.
- Applications and interviews count ``date_applied`` and ``interview_date``
  with the same day-first parsing rules as
  :mod:`personal_job_finder_api.domain.tracker_dates`.
- Applications, interviews, and the application-status breakdown include every
  tracker row, including rows whose ``location_status`` is ``excluded``.
- Totals, new opportunities, and source groups omit excluded rows.
- Nothing is derived from ``user_status``; that column is discovery triage.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_FIRST_DATA_ROW,
)

#: Internal placeholder for a function argument separator. Formulas are authored
#: once with this marker, then rendered for the spreadsheet's locale. Because a
#: literal comma never uses this marker, literal text and regex patterns can
#: never be rewritten by the locale conversion.
ARG: Final = "\x1f"


@dataclass(frozen=True, slots=True)
class FormulaDialect:
    """The locale-dependent parts of a Google Sheets formula.

    Only the argument separator changes between locales. Function names stay
    English in every locale, and string literals, regex patterns, and format
    codes are locale-independent.
    """

    locale: str
    argument_separator: str

    def render(self, template: str) -> str:
        """Replace every argument-separator placeholder with this dialect's separator."""

        return template.replace(ARG, self.argument_separator)


DEFAULT_DIALECT: Final = FormulaDialect(locale="en_US", argument_separator=",")

#: Locales this module can render. Adding one here is a deliberate act: an
#: unlisted spreadsheet locale is refused rather than guessed.
FORMULA_DIALECTS: Final[Mapping[str, FormulaDialect]] = {
    "en_US": DEFAULT_DIALECT,
    "id_ID": FormulaDialect(locale="id_ID", argument_separator=";"),
}

#: Alternative identifiers Google may return for a supported locale.
#:
#: Google Sheets has reported the Indonesian spreadsheet locale as ``in_ID``
#: (legacy ISO 639-1 ``in``) as well as ``id_ID`` (current ``id``). Both select
#: the same spreadsheet UI locale, so both must select the same formula dialect.
#: Aliases are explicit and point at a key in :data:`FORMULA_DIALECTS`; they are
#: never inferred by prefix matching, so an unknown locale is still refused.
LOCALE_ALIASES: Final[Mapping[str, str]] = {"in_ID": "id_ID"}


def resolve_formula_dialect(locale: str) -> FormulaDialect | None:
    """Return the dialect for a spreadsheet locale, following known aliases.

    ``None`` means the locale is unsupported. The caller keeps the raw metadata
    value for diagnostics; the returned dialect carries the canonical locale.
    """

    canonical = LOCALE_ALIASES.get(locale, locale)
    return FORMULA_DIALECTS.get(canonical)


METRICS_TAB: Final = "Metrics"
#: Bumped when the written layout changes incompatibly. V1 used a last-row helper
#: that evaluated to its fallback in Google Sheets, so every metric range was
#: pinned to row 2; V2 replaces it with a live-verified FILTER-based helper. An
#: existing V1 tab is refused rather than silently trusted with wrong numbers.
METRICS_SENTINEL: Final = "PJF_DASHBOARD_V2"
METRICS_SENTINEL_PREFIX: Final = "PJF_DASHBOARD_"
DASHBOARD_TIMEZONE: Final = "Asia/Jakarta"
SENTINEL_CELL: Final = "M1"

#: Whole-tab inspection target. A bare sheet name addresses the entire sheet, so
#: no fixed row or column boundary is assumed when deciding whether an existing
#: Metrics tab is empty.
INSPECT_RANGE: Final = METRICS_TAB

#: Mirror of ``personal_job_finder_api.db.tables.SOURCE_TYPE_VALUES``. Kept as a
#: literal so this module stays dependency-free; a test pins the parity.
SOURCE_TYPES: Final = (
    "greenhouse",
    "lever",
    "ashby",
    "permitted_career_page",
    "linkedin_post_manual",
)

#: Mirror of the suggested values in P0/12 §2. ``application_status`` is free
#: text, so these are labels, never a validated enum.
APPLICATION_STATUS_VALUES: Final = (
    "Need to Apply",
    "Applied",
    "Assessment",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "No Feedback",
)

UNKNOWN_LABEL: Final = "Unknown"
BLANK_LABEL: Final = "(blank)"
OTHER_LABEL: Final = "(other)"

_TITLE_ROW = 1
_AS_OF_ROW = 2
_PIPELINE_HEADER_ROW = 5
_STATUS_HEADER_ROW = 12
_SOURCE_HEADER_ROW = 24
_QUALITY_HEADER_ROW = 32
_NOTES_HEADER_ROW = 36

_SECTION_HEADER_ROWS: Final = (
    _PIPELINE_HEADER_ROW,
    _STATUS_HEADER_ROW,
    _SOURCE_HEADER_ROW,
    _QUALITY_HEADER_ROW,
    _NOTES_HEADER_ROW,
)

_FOOTNOTE_ROWS: Final = (37, 38, 39, 40)

_HELPER_COLUMNS: Final = ("H", "I", "J", "M")

# Column letters in the canonical Opportunities tab.
_OPP_ID_COLUMN = "A"
_OPP_LOCATION_STATUS_COLUMN = "L"
_OPP_FIRST_SEEN_COLUMN = "W"
_OPP_DATE_APPLIED_COLUMN = "AB"
_OPP_APPLICATION_STATUS_COLUMN = "AC"
_OPP_INTERVIEW_DATE_COLUMN = "AE"

_SRC_ID_COLUMN = "A"
_SRC_OPPORTUNITY_COLUMN = "B"
_SRC_TYPE_COLUMN = "C"


@dataclass(frozen=True, slots=True)
class DashboardCell:
    """One written cell. Content starting with ``=`` is a formula."""

    address: str
    content: str

    @property
    def is_formula(self) -> bool:
        return self.content.startswith("=")


# --------------------------------------------------------------------- ranges


def _opportunities_range(column: str, bound: str = "$H$1") -> str:
    """A bounded Opportunities range that survives an interior blank row.

    The upper bound comes from a helper cell, which finds the last row that
    actually holds a key rather than counting non-blank cells. ``COUNTA`` would
    silently truncate every row after the first interior blank key cell.

    ``bound`` is configurable so a second sheet can carry its own helper instead
    of depending on this one's. The range starts at the first canonical data row,
    which is below the KPI strip on ``Opportunities``.
    """

    start = OPPORTUNITY_FIRST_DATA_ROW
    return f"Opportunities!${column}${start}:INDEX(Opportunities!${column}:${column}{ARG}{bound})"


def _sources_range(column: str) -> str:
    return f"Job_Sources!${column}$2:INDEX(Job_Sources!${column}:${column}{ARG}$I$1)"


_OPP_ID = _opportunities_range(_OPP_ID_COLUMN)
_OPP_LOCATION_STATUS = _opportunities_range(_OPP_LOCATION_STATUS_COLUMN)
_OPP_FIRST_SEEN = _opportunities_range(_OPP_FIRST_SEEN_COLUMN)
_OPP_DATE_APPLIED = _opportunities_range(_OPP_DATE_APPLIED_COLUMN)
_OPP_APPLICATION_STATUS = _opportunities_range(_OPP_APPLICATION_STATUS_COLUMN)
_OPP_INTERVIEW_DATE = _opportunities_range(_OPP_INTERVIEW_DATE_COLUMN)

_SRC_OPPORTUNITY = _sources_range(_SRC_OPPORTUNITY_COLUMN)
_SRC_TYPE = _sources_range(_SRC_TYPE_COLUMN)

_OPP_ID_AND_LOCATION_STATUS = (
    f"Opportunities!$A${OPPORTUNITY_FIRST_DATA_ROW}:INDEX(Opportunities!$L:$L{ARG}$H$1)"
)

_OPP_ROW_EXISTS = f'{_OPP_ID}<>""'
_OPP_NOT_EXCLUDED = f'{_OPP_LOCATION_STATUS}<>"excluded"'
_SRC_ROW_EXISTS = f'{_SRC_OPPORTUNITY}<>""'

#: ``VLOOKUP`` into the bounded Opportunities block: column 12 is
#: ``location_status``. A source row whose opportunity is missing or excluded
#: contributes nothing to a source group, matching the backend join.
_SRC_OPPORTUNITY_IS_COUNTABLE = (
    f"MAP({_SRC_OPPORTUNITY}{ARG}LAMBDA(id{ARG}"
    f'IFERROR(IF(VLOOKUP(id{ARG}{_OPP_ID_AND_LOCATION_STATUS}{ARG}12{ARG}FALSE)="excluded"'
    f"{ARG}0{ARG}1){ARG}0)))"
)


# ----------------------------------------------------------------- date parse


def _parse_tracker_date(range_ref: str, result: str) -> str:
    """Build the day-first tracker date parser used by every date metric.

    ``result`` is evaluated inside the ``LET`` scope and may use ``t``, ``iso``,
    ``dmy``, ``p``, ``y``, ``m``, ``d``, ``s``, ``ok``, and ``raw``.

    The parser mirrors :func:`personal_job_finder_api.domain.tracker_dates.parse_tracker_date`:

    1. trim, then remove one trailing parenthetical annotation;
    2. accept ``YYYY-MM-DD`` first, then day-first ``D/MM/YYYY`` or ``DD/MM/YYYY``;
    3. build the date with ``DATE(y, m, d)`` and round-trip it through
       ``DAY``/``MONTH``/``YEAR`` so an impossible calendar date is rejected
       instead of rolling over;
    4. never use ``DATEVALUE``, ``VALUE`` on a whole date string, or any locale
       format token.
    """

    return (
        f"MAP({range_ref}{ARG}LAMBDA(raw{ARG}LET("
        f't{ARG}TRIM(REGEXREPLACE(TO_TEXT(raw){ARG}"\\s*\\([^()]*\\)\\s*$"{ARG}"")){ARG}'
        f'iso{ARG}REGEXMATCH(t{ARG}"^\\d{{4}}-\\d{{1,2}}-\\d{{1,2}}$"){ARG}'
        f'dmy{ARG}REGEXMATCH(t{ARG}"^\\d{{1,2}}/\\d{{1,2}}/\\d{{4}}$"){ARG}'
        f'p{ARG}SPLIT(IF(iso{ARG}SUBSTITUTE(t{ARG}"-"{ARG}"/"){ARG}t){ARG}"/"){ARG}'
        f"y{ARG}IFERROR(IF(iso{ARG}VALUE(INDEX(p{ARG}1{ARG}1)){ARG}VALUE(INDEX(p{ARG}1{ARG}3)))"
        f'{ARG}""){ARG}'
        f'm{ARG}IFERROR(VALUE(INDEX(p{ARG}1{ARG}2)){ARG}""){ARG}'
        f"d{ARG}IFERROR(IF(iso{ARG}VALUE(INDEX(p{ARG}1{ARG}3)){ARG}VALUE(INDEX(p{ARG}1{ARG}1)))"
        f'{ARG}""){ARG}'
        f's{ARG}IF(AND(ISNUMBER(y){ARG}ISNUMBER(m){ARG}ISNUMBER(d)){ARG}DATE(y{ARG}m{ARG}d){ARG}"")'
        f"{ARG}"
        f"ok{ARG}IF(ISNUMBER(s){ARG}AND(DAY(s)=d{ARG}MONTH(s)=m{ARG}YEAR(s)=y){ARG}FALSE){ARG}"
        f"{result})))"
    )


def _date_in_window(range_ref: str, start: str, end: str) -> str:
    return _parse_tracker_date(range_ref, f"IF(ok{ARG}AND(s>={start}{ARG}s<={end}){ARG}FALSE)")


def _unreadable_dates(range_ref: str) -> str:
    """Count non-blank values that the parser could not read.

    A blank value is "not recorded" and is never reported as invalid, matching
    the backend's ``is_blank`` handling.
    """

    return (
        "=IFERROR(SUM("
        + _parse_tracker_date(
            range_ref,
            f'IF(TRIM(TO_TEXT(raw))=""{ARG}0{ARG}IF(ok{ARG}0{ARG}1))',
        )
        + f"){ARG}0)"
    )


# -------------------------------------------------------------------- metrics


def _distinct(id_range: str, *conditions: str) -> str:
    """Count distinct IDs whose row satisfies every condition.

    Google Sheets ``FILTER`` returns ``#N/A`` when no row matches, and
    ``COUNTUNIQUE`` counts that error as one value, so an outer ``IFERROR``
    cannot tell "zero rows" from "one row". ``SUMPRODUCT`` counts rows where all
    conditions are true and returns 0 before ``FILTER`` is evaluated. Genuine
    errors are deliberately not caught, so they remain visible instead of
    silently becoming zero.
    """

    filter_arguments = f"{ARG}".join((id_range, *conditions))
    guard = f"{ARG}".join(f"--({condition})" for condition in conditions)
    return f"=IF(SUMPRODUCT({guard})=0{ARG}0{ARG}COUNTUNIQUE(FILTER({filter_arguments})))"


_TOTAL_FORMULA = _distinct(_OPP_ID, _OPP_ROW_EXISTS, _OPP_NOT_EXCLUDED)
_EXCLUDED_FORMULA = _distinct(
    _OPP_ID,
    _OPP_ROW_EXISTS,
    f'{_OPP_LOCATION_STATUS}="excluded"',
)
_NEW_FORMULA = _distinct(
    _OPP_ID,
    _OPP_ROW_EXISTS,
    _OPP_NOT_EXCLUDED,
    f"REGEXMATCH({_OPP_FIRST_SEEN}{ARG}"
    r'"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")',
    f"{_OPP_FIRST_SEEN}>=$H$2",
    f"{_OPP_FIRST_SEEN}<$H$3",
)
_APPLICATIONS_FORMULA = _distinct(
    _OPP_ID,
    _OPP_ROW_EXISTS,
    _date_in_window(_OPP_DATE_APPLIED, "TODAY()-6", "TODAY()"),
)
_INTERVIEWS_FORMULA = _distinct(
    _OPP_ID,
    _OPP_ROW_EXISTS,
    _date_in_window(_OPP_INTERVIEW_DATE, "TODAY()", "TODAY()+7"),
)

_UNREADABLE_APPLIED_FORMULA = _unreadable_dates(_OPP_DATE_APPLIED)
_UNREADABLE_INTERVIEW_FORMULA = _unreadable_dates(_OPP_INTERVIEW_DATE)

#: Rows whose ``application_status`` is recorded (non-blank after ``TRIM``).
#: ``J1`` is the input to the ``(other)`` remainder, so it must exclude blanks:
#: blank rows already have their own ``(blank)`` category.
_RECORDED_STATUS_COUNT = _distinct(
    _OPP_ID,
    _OPP_ROW_EXISTS,
    f'MAP({_OPP_APPLICATION_STATUS}{ARG}LAMBDA(v{ARG}TRIM(TO_TEXT(v))<>""))',
)
_BLANK_STATUS_FORMULA = _distinct(
    _OPP_ID,
    _OPP_ROW_EXISTS,
    f'MAP({_OPP_APPLICATION_STATUS}{ARG}LAMBDA(v{ARG}TRIM(TO_TEXT(v))=""))',
)


def _status_formula(label: str) -> str:
    """Match one suggested label ignoring case and surrounding spaces.

    The normalization is exactly ``LOWER(TRIM(TO_TEXT(value)))``. Google Sheets
    ``TRIM`` strips leading and trailing spaces and collapses runs of regular
    spaces between words, so ``"Need  to Apply"`` matches ``Need to Apply``. It
    does **not** remove non-breaking spaces, so a value made only of ``CHAR(160)``
    stays non-blank, counts as recorded, and lands in ``(other)``.

    The canonical cell value is never rewritten; this only classifies it.
    """

    return _distinct(
        _OPP_ID,
        _OPP_ROW_EXISTS,
        f"MAP({_OPP_APPLICATION_STATUS}{ARG}"
        f'LAMBDA(v{ARG}LOWER(TRIM(TO_TEXT(v)))="{label.lower()}"))',
    )


def _source_formula(source_type: str) -> str:
    return _distinct(
        _SRC_OPPORTUNITY,
        _SRC_ROW_EXISTS,
        f'{_SRC_TYPE}="{source_type}"',
        _SRC_OPPORTUNITY_IS_COUNTABLE,
    )


#: Opportunities with no source row at all, plus source rows whose type is
#: blank. The backend adds these to the same ``Unknown`` bucket without
#: double-counting, because a job with only a blank-type source still has a row.
_UNKNOWN_SOURCE_FORMULA = (
    _distinct(
        _OPP_ID,
        _OPP_ROW_EXISTS,
        _OPP_NOT_EXCLUDED,
        f"MAP({_OPP_ID}{ARG}LAMBDA(id{ARG}"
        f"IFERROR(IF(MATCH(id{ARG}{_SRC_OPPORTUNITY}{ARG}0)>0{ARG}0{ARG}1){ARG}1)))",
    )
    + "+"
    + _distinct(
        _SRC_OPPORTUNITY,
        _SRC_ROW_EXISTS,
        f'{_SRC_TYPE}=""',
        _SRC_OPPORTUNITY_IS_COUNTABLE,
    )[1:]
)

_AS_OF_FORMULA = (
    f'=TEXT(YEAR(TODAY()){ARG}"0000")&"-"&TEXT(MONTH(TODAY()){ARG}"00")'
    f'&"-"&TEXT(DAY(TODAY()){ARG}"00")'
)

#: Last row that actually holds a key. ``MAX(FILTER(ROW(column), column<>""))``
#: finds the last populated position instead of counting populated cells, so an
#: interior blank key cell cannot truncate the data that follows it. The older
#: Excel ``LOOKUP(2, 1/(...), ROW(...))`` idiom does **not** array-expand in
#: Google Sheets: it returns the fallback on every sheet, which silently pinned
#: every metric range to row 2. Verified live against the scratch spreadsheet.
_LAST_OPPORTUNITY_ROW = (
    f"=IFERROR(MAX({OPPORTUNITY_FIRST_DATA_ROW}{ARG}"
    f"MAX(FILTER(ROW(Opportunities!$A${OPPORTUNITY_FIRST_DATA_ROW}:$A)"
    f'{ARG}Opportunities!$A${OPPORTUNITY_FIRST_DATA_ROW}:$A<>"")))'
    f"{ARG}{OPPORTUNITY_FIRST_DATA_ROW})"
)
_LAST_SOURCE_ROW = (
    f'=IFERROR(MAX(2{ARG}MAX(FILTER(ROW(Job_Sources!$A:$A){ARG}Job_Sources!$A:$A<>""))){ARG}2)'
)


def _jakarta_midnight_utc(day_offset: str) -> str:
    """Render ``T00:00:00+07:00`` for a Jakarta day as a fixed-width UTC string.

    ``first_seen_at_utc`` is canonical ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` text, so a
    fixed-width text comparison is chronological. ``Asia/Jakarta`` is a fixed
    UTC+7 offset with no daylight saving, which makes this conversion exact.
    """

    date = f"TODAY(){day_offset}" if day_offset else "TODAY()"
    return (
        f'=TEXT(YEAR({date}){ARG}"0000")&"-"&TEXT(MONTH({date}){ARG}"00")'
        f'&"-"&TEXT(DAY({date}){ARG}"00")&"T17:00:00.000000Z"'
    )


#: Inclusive start of the seven Jakarta dates ``today-6 .. today``. Midnight in
#: Jakarta on ``today-6`` is 17:00 UTC on ``today-7``.
_WINDOW_START_UTC = _jakarta_midnight_utc("-7")

#: Exclusive end of that window: midnight in Jakarta on ``today+1``.
_WINDOW_END_UTC = _jakarta_midnight_utc("")


def _cell(address: str, content: str) -> DashboardCell:
    return DashboardCell(address=address, content=content)


def visible_cells(dialect: FormulaDialect = DEFAULT_DIALECT) -> tuple[DashboardCell, ...]:
    """The A:B block a user reads, in row order, rendered for ``dialect``."""

    cells: list[DashboardCell] = [
        _cell(
            "A1",
            "Job Search Metrics — formula-only view, not a source of truth",
        ),
        _cell("A2", "As of (Asia/Jakarta)"),
        _cell("B2", _AS_OF_FORMULA),
        _cell("A5", "Pipeline"),
        _cell("A6", "Total opportunities"),
        _cell("B6", _TOTAL_FORMULA),
        _cell("A7", "Excluded opportunities"),
        _cell("B7", _EXCLUDED_FORMULA),
        _cell("A8", "New opportunities (last 7 days)"),
        _cell("B8", _NEW_FORMULA),
        _cell("A9", "Applications (last 7 days)"),
        _cell("B9", _APPLICATIONS_FORMULA),
        _cell("A10", "Upcoming interviews (next 7 days)"),
        _cell("B10", _INTERVIEWS_FORMULA),
        _cell("A12", "Application status (all tracker rows, sheet-only)"),
    ]

    status_rows = 13
    for offset, label in enumerate(APPLICATION_STATUS_VALUES):
        row = status_rows + offset
        cells.append(_cell(f"A{row}", label))
        cells.append(_cell(f"B{row}", _status_formula(label)))
    cells.append(_cell(f"A{status_rows + len(APPLICATION_STATUS_VALUES)}", BLANK_LABEL))
    cells.append(_cell(f"B{status_rows + len(APPLICATION_STATUS_VALUES)}", _BLANK_STATUS_FORMULA))
    other_row = status_rows + len(APPLICATION_STATUS_VALUES) + 1
    cells.append(_cell(f"A{other_row}", OTHER_LABEL))
    cells.append(_cell(f"B{other_row}", f"=MAX(0{ARG}$J$1-SUM($B$13:$B$20))"))

    cells.append(_cell("A24", "Opportunities by source type (a job may appear more than once)"))
    source_rows = 25
    for offset, source_type in enumerate(SOURCE_TYPES):
        row = source_rows + offset
        cells.append(_cell(f"A{row}", source_type))
        cells.append(_cell(f"B{row}", _source_formula(source_type)))
    unknown_row = source_rows + len(SOURCE_TYPES)
    cells.append(_cell(f"A{unknown_row}", UNKNOWN_LABEL))
    cells.append(_cell(f"B{unknown_row}", _UNKNOWN_SOURCE_FORMULA))

    cells.extend(
        [
            _cell("A32", "Data quality"),
            _cell("A33", "Unreadable date_applied"),
            _cell("B33", _UNREADABLE_APPLIED_FORMULA),
            _cell("A34", "Unreadable interview_date"),
            _cell("B34", _UNREADABLE_INTERVIEW_FORMULA),
            _cell("A36", "Notes"),
            _cell("A37", "This tab is optional and formula-only; it is not a source of truth."),
            _cell("A38", "Applications count date_applied; interviews count interview_date."),
            _cell("A39", "Excluded rows are omitted from total, new, and source counts."),
            _cell(
                "A40",
                "Application status is sheet-only free text. Matching ignores case and "
                "surrounding spaces; (blank) is its own category and (other) is any "
                "recorded value outside the eight labels.",
            ),
        ]
    )
    return tuple(_render_cell(cell, dialect) for cell in cells)


def helper_cells(dialect: FormulaDialect = DEFAULT_DIALECT) -> tuple[DashboardCell, ...]:
    """The hidden calculation block. Columns H:M are hidden by formatting."""

    cells = (
        _cell("H1", _LAST_OPPORTUNITY_ROW),
        _cell("I1", _LAST_SOURCE_ROW),
        _cell("H2", _WINDOW_START_UTC),
        _cell("H3", _WINDOW_END_UTC),
        _cell("J1", _RECORDED_STATUS_COUNT),
    )
    return tuple(_render_cell(cell, dialect) for cell in cells)


def _render_cell(cell: DashboardCell, dialect: FormulaDialect) -> DashboardCell:
    return DashboardCell(address=cell.address, content=dialect.render(cell.content))


def all_cells(dialect: FormulaDialect = DEFAULT_DIALECT) -> tuple[DashboardCell, ...]:
    """Every cell written before the sentinel, in a deterministic order."""

    return (*visible_cells(dialect), *helper_cells(dialect))


def sentinel_cell() -> DashboardCell:
    """The ownership marker, written last so partial work is not mistaken for done."""

    return _cell(SENTINEL_CELL, METRICS_SENTINEL)


def build_initialization_requests(
    sheet_id: int,
    dialect: FormulaDialect = DEFAULT_DIALECT,
) -> list[dict[str, object]]:
    """One atomic ``spreadsheets.batchUpdate`` body for a Dashboard initialization.

    Google applies a batch atomically: either every request succeeds or none is
    applied. Keeping the layout, the formatting, and the sentinel in a single
    batch therefore means a written sentinel always implies a complete layout,
    and a failed batch always leaves the tab untouched and retryable.

    ``addSheet`` is deliberately *not* part of this batch. Tab creation is a
    separate call, and atomicity is not claimed across the two.
    """

    return [
        build_cell_update_request(sheet_id, all_cells(dialect)),
        *build_format_requests(sheet_id),
        build_cell_update_request(sheet_id, [sentinel_cell()]),
    ]


# ------------------------------------------------------------------- requests

_ADDRESS_PATTERN = re.compile(r"^([A-Z]+)(\d+)$")


def column_index(letters: str) -> int:
    """Convert a column letter to its zero-based index."""

    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def parse_address(address: str) -> tuple[int, int]:
    """Return ``(row_index, column_index)``, both zero-based."""

    match = _ADDRESS_PATTERN.fullmatch(address)
    if match is None:
        raise ValueError(f"invalid cell address: {address}")
    return int(match.group(2)) - 1, column_index(match.group(1))


def build_cell_update_request(
    sheet_id: int,
    cells: tuple[DashboardCell, ...] | list[DashboardCell],
) -> dict[str, object]:
    """Build one ``UpdateCellsRequest`` covering the bounding box of ``cells``.

    Gaps inside the box are sent as empty cells so a single request can write a
    scattered layout without clearing anything outside the box.
    """

    if not cells:
        raise ValueError("at least one dashboard cell is required")

    positions = [parse_address(cell.address) for cell in cells]
    min_row = min(row for row, _ in positions)
    max_row = max(row for row, _ in positions)
    min_column = min(column for _, column in positions)
    max_column = max(column for _, column in positions)

    grid: list[list[dict[str, object]]] = [
        [{} for _ in range(max_column - min_column + 1)] for _ in range(max_row - min_row + 1)
    ]
    for cell, (row, column) in zip(cells, positions, strict=True):
        value: dict[str, str] = (
            {"formulaValue": cell.content} if cell.is_formula else {"stringValue": cell.content}
        )
        grid[row - min_row][column - min_column] = {"userEnteredValue": value}

    return {
        "updateCells": {
            "start": {
                "sheetId": sheet_id,
                "rowIndex": min_row,
                "columnIndex": min_column,
            },
            "rows": [{"values": row} for row in grid],
            "fields": "userEnteredValue",
        }
    }


def _range(sheet_id: int, start_row: int, end_row: int, start_column: int, end_column: int) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_column,
        "endColumnIndex": end_column,
    }


def _bold_rows(sheet_id: int, rows: tuple[int, ...]) -> list[dict[str, object]]:
    return [
        {
            "repeatCell": {
                "range": _range(sheet_id, row - 1, row, 0, 2),
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }
        for row in rows
    ]


def build_format_requests(sheet_id: int) -> list[dict[str, object]]:
    """Presentation-only requests for the Dashboard sheet.

    Nothing here changes the spreadsheet time zone, creates tabs, or touches a
    canonical tab: every request carries the Dashboard ``sheetId``.
    """

    helper_start = column_index(_HELPER_COLUMNS[0])
    helper_end = column_index(_HELPER_COLUMNS[-1]) + 1

    requests: list[dict[str, object]] = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": helper_start,
                    "endIndex": helper_end,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": column_index("A"),
                    "endIndex": column_index("A") + 1,
                },
                "properties": {"pixelSize": 340},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": column_index("B"),
                    "endIndex": column_index("B") + 1,
                },
                "properties": {"pixelSize": 120},
                "fields": "pixelSize",
            }
        },
        {
            "repeatCell": {
                "range": _range(sheet_id, _TITLE_ROW - 1, _TITLE_ROW, 0, 2),
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 14},
                    }
                },
                "fields": (
                    "userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize"
                ),
            }
        },
        {
            "repeatCell": {
                "range": _range(sheet_id, _AS_OF_ROW - 1, _AS_OF_ROW, 0, 2),
                "cell": {"userEnteredFormat": {"textFormat": {"italic": True}}},
                "fields": "userEnteredFormat.textFormat.italic",
            }
        },
    ]
    requests.extend(_bold_rows(sheet_id, _SECTION_HEADER_ROWS))
    for row in _FOOTNOTE_ROWS:
        requests.append(
            {
                "repeatCell": {
                    "range": _range(sheet_id, row - 1, row, 0, 1),
                    "cell": {
                        "userEnteredFormat": {
                            "wrapStrategy": "WRAP",
                            "textFormat": {"fontSize": 9},
                        }
                    },
                    "fields": (
                        "userEnteredFormat.wrapStrategy,userEnteredFormat.textFormat.fontSize"
                    ),
                }
            }
        )
    return requests
