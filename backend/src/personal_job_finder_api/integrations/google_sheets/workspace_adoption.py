"""Read-only classification of a spreadsheet before setup writes anything.

A target is accepted only when it is either an empty default spreadsheet or a
recognizable Personal JobHunter workspace. Everything else is refused so setup
never adds tabs to a personal spreadsheet. The inspector performs reads only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleSheetsClient,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    REQUIRED_TABS,
    TAB_HEADER_ROWS,
)
from personal_job_finder_api.integrations.google_sheets.workspace_repair import (
    WORKSPACE_SENTINEL,
    WORKSPACE_SENTINEL_CELL,
)

DEFAULT_TAB = "Sheet1"
EMPTY_PROBE_RANGE = f"{DEFAULT_TAB}!A1:Z1000"


class WorkspaceKind(StrEnum):
    EMPTY = "empty"
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class WorkspaceInspection:
    kind: WorkspaceKind
    reason: str | None = None
    legacy_header: bool = False
    needs_repair: bool = False


def inspect_workspace(client: GoogleSheetsClient, spreadsheet_id: str) -> WorkspaceInspection:
    """Classify a target without writing to it.

    The sentinel is preferred, but a spreadsheet whose full canonical header
    contract matches is accepted and repaired additively. A legacy row-1
    ``Opportunities`` header is accepted because the repair service migrates it
    once. Any other shape is incompatible.
    """

    metadata = client.get_metadata(spreadsheet_id)
    titles = set(metadata.titles)
    present = [tab for tab in REQUIRED_TABS if tab in titles]

    if not present:
        return _inspect_without_required_tabs(client, spreadsheet_id, metadata)

    headers = client.get_header_rows(
        spreadsheet_id,
        present,
        header_rows=TAB_HEADER_ROWS,
    )
    if _headers_match(present, headers):
        sentinel = _read_sentinel(client, spreadsheet_id)
        return WorkspaceInspection(
            kind=WorkspaceKind.COMPATIBLE,
            needs_repair=sentinel != WORKSPACE_SENTINEL
            or len(present) != len(REQUIRED_TABS)
            or any(not headers[tab] for tab in present),
        )

    if "Opportunities" in present and not headers.get("Opportunities"):
        legacy = client.get_header_rows(
            spreadsheet_id,
            ["Opportunities"],
            header_rows={"Opportunities": 1},
        )
        if tuple(legacy.get("Opportunities", ())) == OPPORTUNITY_HEADERS:
            job_sources = headers.get("Job_Sources", ())
            if "Job_Sources" in present and job_sources != JOB_SOURCE_HEADERS:
                return WorkspaceInspection(
                    kind=WorkspaceKind.INCOMPATIBLE,
                    reason="required_header_mismatch",
                )
            return WorkspaceInspection(
                kind=WorkspaceKind.COMPATIBLE,
                legacy_header=True,
                needs_repair=True,
            )

    return WorkspaceInspection(
        kind=WorkspaceKind.INCOMPATIBLE,
        reason="required_header_mismatch",
    )


def _headers_match(present: list[str], headers: dict[str, tuple[str, ...]]) -> bool:
    expected = {
        "Opportunities": OPPORTUNITY_HEADERS,
        "Job_Sources": JOB_SOURCE_HEADERS,
    }
    return all(tuple(headers.get(tab, ())) == expected[tab] for tab in present)


def _read_sentinel(client: GoogleSheetsClient, spreadsheet_id: str) -> str:
    values = client.get_values(
        spreadsheet_id,
        [f"Opportunities!{WORKSPACE_SENTINEL_CELL}"],
    )
    rows = values.get(f"Opportunities!{WORKSPACE_SENTINEL_CELL}", [])
    if not rows or not rows[0]:
        return ""
    return str(rows[0][0]).strip()


def _inspect_without_required_tabs(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    metadata: SpreadsheetMetadata,
) -> WorkspaceInspection:
    if set(metadata.titles) != {DEFAULT_TAB}:
        return WorkspaceInspection(
            kind=WorkspaceKind.INCOMPATIBLE,
            reason="unexpected_tabs_with_data",
        )

    values = client.get_values(spreadsheet_id, [EMPTY_PROBE_RANGE])
    rows = values.get(EMPTY_PROBE_RANGE, [])
    if any(str(cell).strip() for row in rows for cell in row):
        return WorkspaceInspection(
            kind=WorkspaceKind.INCOMPATIBLE,
            reason="unexpected_tabs_with_data",
        )
    return WorkspaceInspection(kind=WorkspaceKind.EMPTY)
