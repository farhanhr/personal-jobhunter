"""Bounded adapter around the official Google Sheets API v4 client.

All requests use the official ``sheets.googleapis.com`` origin. Errors are
translated to stable, sanitized codes; provider response bodies never reach
callers or logs. The transport is injectable so tests never open a socket.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httplib2
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from personal_job_finder_api.integrations.google_sheets.config import (
    DEFAULT_MAX_BATCH_RANGES,
    SHEETS_API_ORIGIN,
)
from personal_job_finder_api.integrations.google_sheets.errors import (
    GoogleSheetsError,
    translate_http_status,
    translate_transport_exception,
)

RAW_INPUT_OPTION = "RAW"

#: Render option that returns the formula text for a formula cell, so an
#: empty-string result is still visible as user-entered content.
FORMULA_RENDER_OPTION = "FORMULA"

#: Read numbers and dates as the provider stores them, not as the locale would
#: display them. A tracker date column must never be re-read through a locale
#: format, because ``11/20/2026`` and ``20/11/2026`` are the same text to the API
#: but different dates to a reader.
UNFORMATTED_RENDER_OPTION = "UNFORMATTED_VALUE"


def column_letter(index: int) -> str:
    """Convert a 1-based column index to its spreadsheet letter."""

    if index < 1:
        raise ValueError("column index must be 1 or greater")
    letters: list[str] = []
    remaining = index
    while remaining:
        remaining, remainder = divmod(remaining - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


class SheetsTransport(Protocol):
    """Low-level request boundary implemented by the official client adapter."""

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]: ...

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]: ...

    def batch_get_values_with_render(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]: ...

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]: ...

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def get_spreadsheet_grid(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SheetProperties:
    """The parts of one sheet's properties this adapter exposes."""

    title: str
    sheet_id: int | None = None
    #: Native table ids on this sheet. Empty when the sheet has no table, which is
    #: also what a minimal provider response or a test fake reports.
    table_ids: tuple[str, ...] = ()
    #: The 0-based ``endRowIndex`` of each table. Repair compares it with the grid
    #: so a grid that grew after the table was created gets a wider table.
    table_end_rows: tuple[int, ...] = ()
    #: Frozen pane counts. Repair compares these so a healthy sheet is left alone.
    frozen_row_count: int = 0
    frozen_column_count: int = 0
    #: Total rows and columns in the grid. A native table cannot exceed them.
    row_count: int = 0
    column_count: int = 0


@dataclass(frozen=True, slots=True)
class SpreadsheetMetadata:
    """Tab titles, spreadsheet properties, and per-tab sheet ids.

    ``locale`` decides the formula argument separator used by the optional
    Dashboard tab, and ``time_zone`` decides what ``TODAY()`` means. ``sheet_id``
    and both properties are optional so a minimal provider response (or a test
    fake) still satisfies read-only preflight. A caller that needs a sheet id
    must handle ``None`` instead of guessing a number.
    """

    titles: tuple[str, ...]
    time_zone: str | None = None
    locale: str | None = None
    sheets: tuple[SheetProperties, ...] = ()

    def sheet_id_for(self, title: str) -> int | None:
        for sheet in self.sheets:
            if sheet.title == title:
                return sheet.sheet_id
        return None

    def table_ids_for(self, title: str) -> tuple[str, ...]:
        for sheet in self.sheets:
            if sheet.title == title:
                return sheet.table_ids
        return ()

    def sheet_properties_for(self, title: str) -> SheetProperties | None:
        for sheet in self.sheets:
            if sheet.title == title:
                return sheet
        return None


@dataclass(frozen=True, slots=True)
class GridCell:
    """One cell as the provider computed it, not merely as it was written.

    ``formula`` is the user-entered formula text and ``literal`` the user-entered
    constant. ``value`` is the effective value the provider computed and
    ``error`` is the provider's error type when evaluation failed. A cell with a
    formula and an ``error`` is a formula that was written successfully but did
    not execute successfully, which is the distinction a plain values read
    cannot make.
    """

    address: str
    formula: str | None = None
    literal: str | None = None
    value: str | None = None
    error: str | None = None
    formatted: str | None = None

    @property
    def has_error(self) -> bool:
        return self.error is not None


class GoogleSheetsClient:
    """Validated, bounded operations used by preflight and future sync work."""

    def __init__(
        self,
        transport: SheetsTransport,
        *,
        max_batch_ranges: int = DEFAULT_MAX_BATCH_RANGES,
    ) -> None:
        if max_batch_ranges <= 0:
            raise GoogleSheetsError(
                "batch_limit_invalid",
                "The Google Sheets batch limit must be greater than zero.",
            )
        self._transport = transport
        self._max_batch_ranges = max_batch_ranges

    @property
    def max_batch_ranges(self) -> int:
        """The configured ceiling for ranges in one batch request."""

        return self._max_batch_ranges

    def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        response = self._transport.get_spreadsheet(spreadsheet_id)
        if not isinstance(response, dict):
            raise _malformed_response()
        sheets = response.get("sheets")
        if not isinstance(sheets, list):
            raise _malformed_response()

        properties = response.get("properties")
        time_zone: str | None = None
        locale: str | None = None
        if properties is not None:
            if not isinstance(properties, dict):
                raise _malformed_response()
            raw_time_zone = properties.get("timeZone")
            if raw_time_zone is not None:
                if not isinstance(raw_time_zone, str):
                    raise _malformed_response()
                time_zone = raw_time_zone
            raw_locale = properties.get("locale")
            if raw_locale is not None:
                if not isinstance(raw_locale, str):
                    raise _malformed_response()
                locale = raw_locale

        parsed: list[SheetProperties] = []
        for sheet in sheets:
            if not isinstance(sheet, dict):
                raise _malformed_response()
            sheet_properties = sheet.get("properties")
            if not isinstance(sheet_properties, dict):
                raise _malformed_response()
            title = sheet_properties.get("title")
            if not isinstance(title, str):
                raise _malformed_response()
            raw_sheet_id = sheet_properties.get("sheetId")
            sheet_id: int | None = None
            if raw_sheet_id is not None:
                if isinstance(raw_sheet_id, bool) or not isinstance(raw_sheet_id, int):
                    raise _malformed_response()
                sheet_id = raw_sheet_id
            grid_properties = sheet_properties.get("gridProperties") or {}
            if not isinstance(grid_properties, dict):
                raise _malformed_response()
            frozen_row_count = grid_properties.get("frozenRowCount") or 0
            frozen_column_count = grid_properties.get("frozenColumnCount") or 0
            if isinstance(frozen_row_count, bool) or not isinstance(frozen_row_count, int):
                raise _malformed_response()
            if isinstance(frozen_column_count, bool) or not isinstance(frozen_column_count, int):
                raise _malformed_response()
            row_count = grid_properties.get("rowCount") or 0
            if isinstance(row_count, bool) or not isinstance(row_count, int):
                raise _malformed_response()
            column_count = grid_properties.get("columnCount") or 0
            if isinstance(column_count, bool) or not isinstance(column_count, int):
                raise _malformed_response()

            table_ids: list[str] = []
            table_end_rows: list[int] = []
            raw_tables = sheet.get("tables")
            if raw_tables is not None:
                if not isinstance(raw_tables, list):
                    raise _malformed_response()
                for table in raw_tables:
                    if not isinstance(table, dict):
                        raise _malformed_response()
                    table_id = table.get("tableId")
                    if table_id is None:
                        continue
                    if not isinstance(table_id, str):
                        raise _malformed_response()
                    table_ids.append(table_id)
                    raw_range = table.get("range")
                    end_row = raw_range.get("endRowIndex") if isinstance(raw_range, dict) else None
                    if isinstance(end_row, int) and not isinstance(end_row, bool):
                        table_end_rows.append(end_row)
            parsed.append(
                SheetProperties(
                    title=title,
                    sheet_id=sheet_id,
                    table_ids=tuple(table_ids),
                    table_end_rows=tuple(table_end_rows),
                    frozen_row_count=frozen_row_count,
                    frozen_column_count=frozen_column_count,
                    row_count=row_count,
                    column_count=column_count,
                )
            )

        return SpreadsheetMetadata(
            titles=tuple(sheet.title for sheet in parsed),
            time_zone=time_zone,
            locale=locale,
            sheets=tuple(parsed),
        )

    def get_header_rows(
        self,
        spreadsheet_id: str,
        tabs: Sequence[str],
        *,
        header_rows: Mapping[str, int] | None = None,
    ) -> dict[str, tuple[str, ...]]:
        """Read the header row of every requested tab in one bounded batch.

        ``header_rows`` maps a tab to its 1-based header row. A tab absent from
        the mapping keeps the row-1 default, which is correct for
        ``Job_Sources`` and for callers that only know the legacy layout.
        """

        if not tabs:
            return {}
        if len(tabs) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets read batch exceeds the configured limit.",
            )
        rows = header_rows or {}
        ranges = [f"{tab}!{rows.get(tab, 1)}:{rows.get(tab, 1)}" for tab in tabs]
        response = self._transport.batch_get_values(spreadsheet_id, ranges)
        value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
        if not isinstance(value_ranges, list) or len(value_ranges) != len(tabs):
            raise _malformed_response()

        headers: dict[str, tuple[str, ...]] = {}
        for tab, value_range in zip(tabs, value_ranges, strict=True):
            if not isinstance(value_range, dict):
                raise _malformed_response()
            values = value_range.get("values")
            if values is None:
                headers[tab] = ()
                continue
            if not isinstance(values, list):
                raise _malformed_response()
            if not values:
                headers[tab] = ()
                continue
            first_row = values[0]
            if not isinstance(first_row, list):
                raise _malformed_response()
            cells = ["" if cell is None else str(cell) for cell in first_row]
            while cells and cells[-1] == "":
                cells.pop()
            headers[tab] = tuple(cells)
        return headers

    def get_values(
        self,
        spreadsheet_id: str,
        ranges: Sequence[str],
        *,
        value_render_option: str | None = None,
    ) -> dict[str, list[list[str]]]:
        """Read whole-column or row ranges in one bounded batch.

        Each returned value list starts at the first row of its range, so a
        caller can derive the spreadsheet row number from the list index.

        The default render is the API default (``FORMATTED_VALUE``). Passing
        ``FORMULA_RENDER_OPTION`` returns formula text instead of the displayed
        value, which is how an empty-string formula stays visible as content.
        """

        if not ranges:
            return {}
        if len(ranges) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets read batch exceeds the configured limit.",
            )
        if value_render_option is None:
            response = self._transport.batch_get_values(spreadsheet_id, list(ranges))
        else:
            response = self._transport.batch_get_values_with_render(
                spreadsheet_id,
                list(ranges),
                value_render_option=value_render_option,
            )
        value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
        if not isinstance(value_ranges, list) or len(value_ranges) != len(ranges):
            raise _malformed_response()

        result: dict[str, list[list[str]]] = {}
        for requested, value_range in zip(ranges, value_ranges, strict=True):
            if not isinstance(value_range, dict):
                raise _malformed_response()
            values = value_range.get("values")
            if values is None:
                result[requested] = []
                continue
            if not isinstance(values, list):
                raise _malformed_response()
            rows: list[list[str]] = []
            for row in values:
                if not isinstance(row, list):
                    raise _malformed_response()
                rows.append(["" if cell is None else str(cell) for cell in row])
            result[requested] = rows
        return result

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Write multiple ranges in one request.

        ``RAW`` is not configurable: job text is untrusted and must never be
        interpreted as a formula.
        """

        if not data:
            raise GoogleSheetsError(
                "invalid_request",
                "At least one Google Sheets value range is required.",
            )
        if len(data) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets value batch exceeds the configured limit.",
            )
        return self._transport.batch_update_values(
            spreadsheet_id,
            data,
            value_input_option=RAW_INPUT_OPTION,
        )

    def get_grid(
        self,
        spreadsheet_id: str,
        ranges: Sequence[str],
    ) -> dict[str, list[list[GridCell]]]:
        """Read cells with their formula, effective value, and error state.

        This is the only read that can tell "the formula was written" apart from
        "the formula computed". ``values.get`` returns displayed text, so a
        ``#ERROR!`` and a correct number look equally like strings.
        """

        if not ranges:
            return {}
        if len(ranges) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets read batch exceeds the configured limit.",
            )
        response = self._transport.get_spreadsheet_grid(spreadsheet_id, list(ranges))
        return _parse_grid_response(response, ranges)

    def add_tabs(
        self,
        spreadsheet_id: str,
        titles: Sequence[str],
    ) -> dict[str, Any]:
        requests = [{"addSheet": {"properties": {"title": title}}} for title in titles]
        if not requests:
            return {}
        if len(requests) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets tab batch exceeds the configured limit.",
            )
        return self._transport.batch_update_spreadsheet(
            spreadsheet_id,
            requests,
        )

    def batch_update(
        self,
        spreadsheet_id: str,
        requests: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Send structured ``spreadsheets.batchUpdate`` requests.

        This is the only path that can carry a ``formulaValue`` or a formatting
        request. It is deliberately separate from :meth:`batch_update_values`,
        which stays locked to ``RAW`` so canonical job text is never parsed as a
        formula.
        """

        if not requests:
            raise GoogleSheetsError(
                "invalid_request",
                "At least one Google Sheets batch update request is required.",
            )
        if len(requests) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets batch update exceeds the configured limit.",
            )
        return self._transport.batch_update_spreadsheet(
            spreadsheet_id,
            list(requests),
        )

    def write_headers(
        self,
        spreadsheet_id: str,
        headers: Mapping[str, Sequence[str]],
        *,
        header_rows: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        rows = header_rows or {}
        data = [
            {
                "range": f"{tab}!A{rows.get(tab, 1)}",
                "values": [list(values)],
            }
            for tab, values in headers.items()
        ]
        if not data:
            return {}
        return self.batch_update_values(spreadsheet_id, data)


MAX_RETRY_AFTER_SECONDS = 300


def _retry_after_seconds(exc: HttpError) -> int | None:
    """Read a bounded ``Retry-After`` hint without exposing the response body."""

    headers = getattr(getattr(exc, "resp", None), "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 1:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _malformed_response() -> GoogleSheetsError:
    return GoogleSheetsError(
        "malformed_response",
        "The Google Sheets response did not match the expected structure.",
    )


def _grid_effective_value(value: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(effective_value, error_type)`` for one ``effectiveValue``."""

    if not isinstance(value, dict):
        raise _malformed_response()
    error = value.get("errorValue")
    if error is not None:
        if not isinstance(error, dict):
            raise _malformed_response()
        error_type = error.get("type")
        if not isinstance(error_type, str):
            raise _malformed_response()
        return None, error_type
    for key in ("numberValue", "stringValue", "boolValue"):
        if key in value:
            raw = value[key]
            if isinstance(raw, bool):
                return ("TRUE" if raw else "FALSE"), None
            if raw is None:
                return "", None
            return str(raw), None
    return None, None


def _parse_grid_cell(raw: Any, row_index: int, column_index: int) -> GridCell:
    if not isinstance(raw, dict):
        raise _malformed_response()
    entered = raw.get("userEnteredValue")
    formula: str | None = None
    literal: str | None = None
    if entered is not None:
        if not isinstance(entered, dict):
            raise _malformed_response()
        raw_formula = entered.get("formulaValue")
        if raw_formula is not None:
            if not isinstance(raw_formula, str):
                raise _malformed_response()
            formula = raw_formula
        for key in ("stringValue", "numberValue", "boolValue"):
            if key in entered:
                raw_literal = entered[key]
                literal = (
                    ("TRUE" if raw_literal else "FALSE")
                    if isinstance(raw_literal, bool)
                    else ("" if raw_literal is None else str(raw_literal))
                )
                break
    effective = raw.get("effectiveValue")
    value, error = (None, None) if effective is None else _grid_effective_value(effective)
    formatted = raw.get("formattedValue")
    if formatted is not None and not isinstance(formatted, str):
        raise _malformed_response()
    return GridCell(
        address=f"{column_letter(column_index + 1)}{row_index + 1}",
        formula=formula,
        literal=literal,
        value=value,
        error=error,
        formatted=formatted,
    )


_A1_RANGE = re.compile(
    r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^!]+))!"
    r"\$?(?P<start_column>[A-Za-z]+)\$?(?P<start_row>\d+)"
    r"(?::\$?(?P<end_column>[A-Za-z]+)\$?(?P<end_row>\d+))?$"
)


@dataclass(frozen=True, slots=True)
class _RangeBounds:
    """One requested A1 range, resolved to a 1-based rectangle."""

    title: str
    start_row: int
    start_column: int
    end_row: int
    end_column: int


@dataclass(frozen=True, slots=True)
class _GridBlock:
    """One provider ``GridData`` block and its absolute position."""

    start_row: int
    start_column: int
    rows: tuple[Any, ...]
    width: int

    @property
    def end_row(self) -> int:
        return self.start_row + len(self.rows) - 1

    @property
    def end_column(self) -> int:
        return self.start_column + self.width - 1


def _column_index(letters: str) -> int:
    index = 0
    for character in letters.upper():
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index


def _parse_range(range_name: str) -> _RangeBounds:
    match = _A1_RANGE.match(range_name.strip())
    if match is None:
        raise GoogleSheetsError(
            "invalid_range",
            "A requested grid range is not valid A1 notation.",
        )
    title = (match.group("quoted") or match.group("plain")).replace("''", "'")
    start_row = int(match.group("start_row"))
    end_row = int(match.group("end_row") or start_row)
    start_column = _column_index(match.group("start_column"))
    end_column = _column_index(match.group("end_column") or match.group("start_column"))
    if start_row < 1 or end_row < start_row or end_column < start_column:
        raise GoogleSheetsError(
            "invalid_range",
            "A requested grid range is not valid A1 notation.",
        )
    return _RangeBounds(
        title=title,
        start_row=start_row,
        start_column=start_column,
        end_row=end_row,
        end_column=end_column,
    )


def _grid_block(raw: Any) -> _GridBlock:
    if not isinstance(raw, dict):
        raise _malformed_response()
    start_row = raw.get("startRow")
    start_column = raw.get("startColumn")
    start_row = 0 if start_row is None else start_row
    start_column = 0 if start_column is None else start_column
    if (
        not isinstance(start_row, int)
        or not isinstance(start_column, int)
        or start_row < 0
        or start_column < 0
    ):
        raise _malformed_response()
    row_data = raw.get("rowData")
    if row_data is None:
        row_data = []
    if not isinstance(row_data, list):
        raise _malformed_response()
    rows: list[Any] = []
    width = 0
    for row in row_data:
        if not isinstance(row, dict):
            raise _malformed_response()
        values = row.get("values")
        if values is None:
            values = []
        if not isinstance(values, list):
            raise _malformed_response()
        rows.append(values)
        width = max(width, len(values))
    return _GridBlock(
        start_row=start_row,
        start_column=start_column,
        rows=tuple(rows),
        width=width,
    )


def _block_contains(block: _GridBlock, bounds: _RangeBounds) -> bool:
    """Compare a 0-based provider block with a 1-based A1 rectangle."""

    return (
        block.start_row + 1 <= bounds.start_row
        and block.start_column + 1 <= bounds.start_column
        and block.end_row + 1 >= bounds.end_row
        and block.end_column + 1 >= bounds.end_column
    )


def _slice_block(block: _GridBlock, bounds: _RangeBounds) -> list[list[GridCell]]:
    """Return the requested rectangle, with inert cells for absent positions."""

    sliced: list[list[GridCell]] = []
    for row_index in range(bounds.start_row, bounds.end_row + 1):
        offset = (row_index - 1) - block.start_row
        values = block.rows[offset] if 0 <= offset < len(block.rows) else ()
        row: list[GridCell] = []
        for column_index in range(bounds.start_column, bounds.end_column + 1):
            position = (column_index - 1) - block.start_column
            if 0 <= position < len(values):
                row.append(_parse_grid_cell(values[position], row_index - 1, column_index - 1))
            else:
                row.append(
                    GridCell(
                        address=f"{column_letter(column_index)}{row_index}",
                    )
                )
        sliced.append(row)
    return sliced


def _parse_grid_response(
    response: Any,
    ranges: Sequence[str],
) -> dict[str, list[list[GridCell]]]:
    """Map one provider grid response onto the requested ranges.

    The provider groups data by sheet, in no guaranteed request order, and emits
    one ``data`` block per requested range that intersects a sheet. Mapping by
    request position is therefore wrong: a 25-cell read of one tab comes back as
    one sheet with 25 blocks, and a two-sheet read can come back reversed. Ranges
    are matched by sheet title and by the block whose rectangle contains the
    requested rectangle; a range that no block covers is simply empty.
    """

    if not isinstance(response, dict):
        raise _malformed_response()
    sheets = response.get("sheets")
    if not isinstance(sheets, list):
        raise _malformed_response()

    blocks_by_title: dict[str, list[_GridBlock]] = {}
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise _malformed_response()
        properties = sheet.get("properties")
        if not isinstance(properties, dict):
            raise _malformed_response()
        title = properties.get("title")
        if not isinstance(title, str) or not title:
            raise _malformed_response()
        data = sheet.get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            raise _malformed_response()
        blocks_by_title.setdefault(title, []).extend(_grid_block(entry) for entry in data)

    result: dict[str, list[list[GridCell]]] = {}
    for requested in ranges:
        bounds = _parse_range(requested)
        blocks = blocks_by_title.get(bounds.title, [])
        # The provider emits one block per requested range, starting exactly at
        # that range's first cell. Trailing empty cells are trimmed from
        # ``rowData``, so the observed width is a lower bound, never the extent:
        # an exact start match is therefore authoritative, and a containing
        # block is only a fallback for providers that merge adjacent ranges.
        exact = [
            block
            for block in blocks
            if block.start_row + 1 == bounds.start_row
            and block.start_column + 1 == bounds.start_column
        ]
        covering = [block for block in blocks if _block_contains(block, bounds)]
        candidates = exact or covering
        if not candidates:
            result[requested] = []
            continue
        best = min(candidates, key=lambda block: (len(block.rows), block.width))
        result[requested] = _slice_block(best, bounds)
    return result


class GoogleApiSheetsTransport:
    """Official Google API client wrapped with sanitized error translation."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        # ``tables`` is a sibling of ``properties`` on a Sheet, and the provider
        # omits it unless the field mask asks for it. Without it, repair cannot
        # tell an existing native table from a missing one.
        request = self._service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            includeGridData=False,
            fields=(
                "properties(timeZone,locale),"
                "sheets(properties(sheetId,title,gridProperties,index,sheetType),tables)"
            ),
        )
        return self._execute(request)

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        request = (
            self._service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
            )
        )
        return self._execute(request)

    def batch_get_values_with_render(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]:
        request = (
            self._service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                valueRenderOption=value_render_option,
            )
        )
        return self._execute(request)

    def get_spreadsheet_grid(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        """Read grids including formula text, effective values, and errors."""

        request = self._service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            ranges=ranges,
            includeGridData=True,
            fields=(
                "sheets(properties,data(startRow,startColumn,rowData(values("
                "userEnteredValue,effectiveValue,formattedValue))))"
            ),
        )
        return self._execute(request)

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        request = (
            self._service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "valueInputOption": value_input_option,
                    "data": data,
                },
            )
        )
        return self._execute(request)

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        request = self._service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        )
        return self._execute(request)

    def _execute(self, request: Any) -> dict[str, Any]:
        try:
            result = request.execute()
        except GoogleSheetsError:
            raise
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
            raise translate_http_status(
                status,
                retry_after_seconds=_retry_after_seconds(exc),
            ) from None
        except Exception as exc:
            raise translate_transport_exception(exc) from None
        if not isinstance(result, dict):
            raise _malformed_response()
        return result


def build_google_sheets_service(
    *,
    credentials: Any,
    timeout_seconds: float,
) -> Any:
    """Build a Sheets v4 service pinned to the official API origin.

    ``googleapiclient.discovery.build`` rejects ``http`` and ``credentials``
    together ("Arguments http and credentials are mutually exclusive"), so the
    credentials are composed into the HTTP layer with ``AuthorizedHttp``
    instead. The configured timeout therefore lives on the wrapped
    ``httplib2.Http`` instance, and the service still authenticates with the
    Service Account credentials.
    """

    http = httplib2.Http(timeout=timeout_seconds)
    authorized_http = AuthorizedHttp(credentials, http=http)
    return build(
        "sheets",
        "v4",
        http=authorized_http,
        cache_discovery=False,
        static_discovery=True,
        client_options={"api_endpoint": SHEETS_API_ORIGIN},
    )
