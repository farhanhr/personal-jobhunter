"""Offline tests for read-only workspace classification."""

from __future__ import annotations

from personal_job_finder_api.integrations.google_sheets.client import SpreadsheetMetadata
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.workspace_adoption import (
    WorkspaceKind,
    inspect_workspace,
)
from personal_job_finder_api.integrations.google_sheets.workspace_repair import (
    WORKSPACE_SENTINEL,
)

SPREADSHEET_ID = "1SyntheticSheetId0123456789abcdefXYZ"


class FakeClient:
    def __init__(
        self,
        *,
        titles: tuple[str, ...],
        headers: dict[str, tuple[str, ...]] | None = None,
        legacy_headers: tuple[str, ...] = (),
        sentinel: str = "",
        sheet1_rows: list[list[str]] | None = None,
    ) -> None:
        self._titles = titles
        self._headers = headers or {}
        self._legacy_headers = legacy_headers
        self._sentinel = sentinel
        self._sheet1_rows = sheet1_rows or []
        self.reads: list[str] = []
        self.writes: list[str] = []

    def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        self.reads.append("metadata")
        return SpreadsheetMetadata(titles=self._titles)

    def get_header_rows(
        self,
        spreadsheet_id: str,
        tabs: list[str] | tuple[str, ...],
        *,
        header_rows: dict[str, int] | None = None,
    ) -> dict[str, tuple[str, ...]]:
        self.reads.append(f"headers:{','.join(tabs)}")
        rows = header_rows or {}
        result: dict[str, tuple[str, ...]] = {}
        for tab in tabs:
            if rows.get(tab, 1) == 1 and tab == "Opportunities":
                result[tab] = self._legacy_headers
            else:
                result[tab] = self._headers.get(tab, ())
        return result

    def get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, list[list[str]]]:
        self.reads.append(f"values:{','.join(ranges)}")
        values: dict[str, list[list[str]]] = {}
        for range_name in ranges:
            if range_name.endswith("!AH2"):
                values[range_name] = [[self._sentinel]] if self._sentinel else []
            elif range_name == "Sheet1!A1:Z1000":
                values[range_name] = self._sheet1_rows
        return values


def test_empty_default_sheet_is_empty() -> None:
    client = FakeClient(titles=("Sheet1",))

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.EMPTY
    assert inspection.reason is None


def test_compatible_workspace_with_sentinel_needs_no_repair() -> None:
    client = FakeClient(
        titles=("Opportunities", "Job_Sources"),
        headers={
            "Opportunities": OPPORTUNITY_HEADERS,
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
        sentinel=WORKSPACE_SENTINEL,
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.COMPATIBLE
    assert inspection.needs_repair is False
    assert inspection.legacy_header is False


def test_compatible_workspace_without_sentinel_is_repaired() -> None:
    client = FakeClient(
        titles=("Opportunities", "Job_Sources"),
        headers={
            "Opportunities": OPPORTUNITY_HEADERS,
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.COMPATIBLE
    assert inspection.needs_repair is True


def test_legacy_row_one_header_is_compatible() -> None:
    client = FakeClient(
        titles=("Opportunities", "Job_Sources"),
        headers={
            "Opportunities": (),
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
        legacy_headers=OPPORTUNITY_HEADERS,
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.COMPATIBLE
    assert inspection.legacy_header is True
    assert inspection.needs_repair is True


def test_wrong_required_header_is_incompatible() -> None:
    client = FakeClient(
        titles=("Opportunities", "Job_Sources"),
        headers={
            "Opportunities": ("Something else",),
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.INCOMPATIBLE
    assert inspection.reason == "required_header_mismatch"


def test_personal_sheet_with_data_is_incompatible() -> None:
    client = FakeClient(
        titles=("Sheet1", "Notes"),
        sheet1_rows=[["My budget"]],
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.INCOMPATIBLE
    assert inspection.reason == "unexpected_tabs_with_data"


def test_single_sheet_with_data_is_incompatible() -> None:
    client = FakeClient(titles=("Sheet1",), sheet1_rows=[["2026-11-22", "Applied"]])

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.INCOMPATIBLE
    assert inspection.reason == "unexpected_tabs_with_data"


def test_extra_user_tabs_are_allowed_when_required_tabs_match() -> None:
    client = FakeClient(
        titles=("Opportunities", "Job_Sources", "Metrics", "Notes"),
        headers={
            "Opportunities": OPPORTUNITY_HEADERS,
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
        sentinel=WORKSPACE_SENTINEL,
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.COMPATIBLE


def test_partial_workspace_with_matching_headers_is_compatible() -> None:
    client = FakeClient(
        titles=("Opportunities",),
        headers={"Opportunities": OPPORTUNITY_HEADERS},
        sentinel=WORKSPACE_SENTINEL,
    )

    inspection = inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert inspection.kind is WorkspaceKind.COMPATIBLE
    assert inspection.needs_repair is True


def test_inspector_never_writes() -> None:
    client = FakeClient(
        titles=("Opportunities", "Job_Sources"),
        headers={
            "Opportunities": OPPORTUNITY_HEADERS,
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
        sentinel=WORKSPACE_SENTINEL,
    )

    inspect_workspace(client, SPREADSHEET_ID)  # type: ignore[arg-type]

    assert client.writes == []
    assert client.reads
