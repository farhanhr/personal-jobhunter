"""Bounded adapter around the official Google Sheets API v4 client.

All requests use the official ``sheets.googleapis.com`` origin. Errors are
translated to stable, sanitized codes; provider response bodies never reach
callers or logs. The transport is injectable so tests never open a socket.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httplib2
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


class SheetsTransport(Protocol):
    """Low-level request boundary implemented by the official client adapter."""

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]: ...

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
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


@dataclass(frozen=True, slots=True)
class SpreadsheetMetadata:
    """Tab titles exposed by the spreadsheet metadata endpoint."""

    titles: tuple[str, ...]


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

    def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        response = self._transport.get_spreadsheet(spreadsheet_id)
        sheets = response.get("sheets") if isinstance(response, dict) else None
        if not isinstance(sheets, list):
            raise _malformed_response()

        titles: list[str] = []
        for sheet in sheets:
            if not isinstance(sheet, dict):
                raise _malformed_response()
            properties = sheet.get("properties")
            if not isinstance(properties, dict):
                raise _malformed_response()
            title = properties.get("title")
            if not isinstance(title, str):
                raise _malformed_response()
            titles.append(title)
        return SpreadsheetMetadata(titles=tuple(titles))

    def get_header_rows(
        self,
        spreadsheet_id: str,
        tabs: Sequence[str],
    ) -> dict[str, tuple[str, ...]]:
        """Read the first row of every requested tab in one bounded batch."""

        if not tabs:
            return {}
        if len(tabs) > self._max_batch_ranges:
            raise GoogleSheetsError(
                "batch_too_large",
                "The Google Sheets read batch exceeds the configured limit.",
            )
        ranges = [f"{tab}!1:1" for tab in tabs]
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

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str = RAW_INPUT_OPTION,
    ) -> dict[str, Any]:
        """Write multiple ranges in one request, always using ``RAW``."""

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

    def write_headers(
        self,
        spreadsheet_id: str,
        headers: Mapping[str, Sequence[str]],
    ) -> dict[str, Any]:
        data = [{"range": f"{tab}!A1", "values": [list(values)]} for tab, values in headers.items()]
        if not data:
            return {}
        return self.batch_update_values(spreadsheet_id, data)


def _malformed_response() -> GoogleSheetsError:
    return GoogleSheetsError(
        "malformed_response",
        "The Google Sheets response did not match the expected structure.",
    )


class GoogleApiSheetsTransport:
    """Official Google API client wrapped with sanitized error translation."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        request = self._service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            includeGridData=False,
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
            raise translate_http_status(status) from None
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
    """Build a Sheets v4 service pinned to the official API origin."""

    http = httplib2.Http(timeout=timeout_seconds)
    return build(
        "sheets",
        "v4",
        http=http,
        credentials=credentials,
        cache_discovery=False,
        static_discovery=True,
        client_options={"api_endpoint": SHEETS_API_ORIGIN},
    )
