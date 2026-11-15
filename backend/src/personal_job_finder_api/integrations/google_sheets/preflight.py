"""Read-only spreadsheet preflight and explicit, safe initialization.

Preflight never writes. Initialization creates only missing tabs and empty
header rows; it refuses to overwrite a non-empty header that does not match the
accepted schema. This protects user-entered spreadsheet data from an accidental
schema migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleSheetsClient,
    SheetsTransport,
)
from personal_job_finder_api.integrations.google_sheets.config import REQUIRED_TABS
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError


@dataclass(frozen=True, slots=True)
class TabSchema:
    """Inspection result for one required business tab."""

    title: str
    exists: bool
    headers: tuple[str, ...]
    expected_headers: tuple[str, ...]

    @property
    def header_matches(self) -> bool:
        if not self.exists:
            return False
        return self.headers[: len(self.expected_headers)] == self.expected_headers

    @property
    def missing_headers(self) -> tuple[str, ...]:
        if not self.exists:
            return self.expected_headers
        return tuple(
            expected
            for index, expected in enumerate(self.expected_headers)
            if index >= len(self.headers) or self.headers[index] != expected
        )

    @property
    def empty_header_row(self) -> bool:
        return self.exists and not self.headers

    @property
    def unexpected_headers(self) -> tuple[str, ...]:
        if not self.header_matches:
            return ()
        return self.headers[len(self.expected_headers) :]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Sanitized readiness report that never carries the spreadsheet ID."""

    tabs: Mapping[str, TabSchema]
    missing_tabs: tuple[str, ...]
    schema_errors: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return (
            not self.missing_tabs
            and not self.schema_errors
            and all(tab.header_matches for tab in self.tabs.values())
        )

    def tab(self, title: str) -> TabSchema:
        return self.tabs[title]


class SpreadsheetPreflightService:
    """Inspect and, on explicit request, initialize the accepted schema."""

    def __init__(
        self,
        transport: SheetsTransport,
        *,
        client: GoogleSheetsClient | None = None,
    ) -> None:
        self._client = client or GoogleSheetsClient(transport)

    def check(self, spreadsheet_id: str) -> PreflightResult:
        metadata = self._client.get_metadata(spreadsheet_id)
        existing_titles = set(metadata.titles)
        existing_tabs = [title for title in REQUIRED_TABS if title in existing_titles]
        header_rows = self._client.get_header_rows(
            spreadsheet_id,
            existing_tabs,
        )

        tabs: dict[str, TabSchema] = {}
        for title, expected_headers in REQUIRED_TABS.items():
            exists = title in existing_titles
            tabs[title] = TabSchema(
                title=title,
                exists=exists,
                headers=header_rows.get(title, ()) if exists else (),
                expected_headers=expected_headers,
            )

        missing_tabs = tuple(title for title in REQUIRED_TABS if not tabs[title].exists)
        schema_errors = tuple(
            title
            for title, tab in tabs.items()
            if tab.exists and tab.headers and not tab.header_matches
        )
        return PreflightResult(
            tabs=tabs,
            missing_tabs=missing_tabs,
            schema_errors=schema_errors,
        )

    def initialize(self, spreadsheet_id: str) -> PreflightResult:
        """Create missing tabs and empty headers without touching user data."""

        current = self.check(spreadsheet_id)
        if current.schema_errors:
            details = "; ".join(
                f"{title} (missing: {', '.join(current.tabs[title].missing_headers)})"
                for title in current.schema_errors
            )
            raise GoogleSheetsError(
                "schema_mismatch",
                f"The spreadsheet has incompatible headers: {details}. "
                "No data was changed; fix the header row manually before retrying.",
            )

        tabs_to_create = list(current.missing_tabs)
        tabs_to_fill = [
            tab.title for tab in current.tabs.values() if tab.exists and tab.empty_header_row
        ]

        if tabs_to_create:
            self._client.add_tabs(spreadsheet_id, tabs_to_create)
        headers_to_write = {
            title: REQUIRED_TABS[title] for title in [*tabs_to_create, *tabs_to_fill]
        }
        if headers_to_write:
            self._client.write_headers(spreadsheet_id, headers_to_write)

        final = self.check(spreadsheet_id)
        if not final.is_ready:
            raise GoogleSheetsError(
                "schema_initialization_failed",
                "The spreadsheet schema could not be initialized safely. "
                "Inspect the tabs and headers before retrying.",
            )
        return final
