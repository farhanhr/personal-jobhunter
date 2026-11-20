"""Repair the workspace workbook structure, never its data.

A spreadsheet is a user surface, so a human will eventually type in the wrong
place, clear a header, or delete the table. The application should repair that on
the next explicit sync rather than refuse to work.

Two hard rules keep repair safe:

1. **Structure only.** Repair rewrites headers, table definitions, validation
   rules, frozen panes, and number formats. It never changes a data value the user
   typed, except for one case it must: a manual row with no ``opportunity_id``
   needs an identity before the row can be synchronised.
2. **Idempotent.** A second run against a healthy workbook issues no structural
   change, so repair can run before every sync without churn.

Repair never creates or deletes rows, never clears a range it did not measure, and
never touches a tab it does not own.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from personal_job_finder_api.domain.opportunity_identity import opportunity_id_for
from personal_job_finder_api.integrations.google_sheets import workspace_table as table
from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
    GoogleSheetsClient,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

#: A manual row is identified by these two columns being present while the
#: identity column is empty. Anything less is an incomplete row, not an entry.
MANUAL_SOURCE_TYPE: Final = "manual"
MANUAL_COMPANY_ID: Final = "manual"

_IDENTITY_COLUMN: Final = "opportunity_id"
_COMPANY_COLUMN: Final = "company_name"
_TITLE_COLUMN: Final = "job_title_original"

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """What one repair pass changed, in sanitized terms."""

    actions: tuple[str, ...] = ()
    adopted_rows: tuple[int, ...] = ()
    skipped: tuple[str, ...] = field(default=())

    @property
    def changed(self) -> bool:
        return bool(self.actions or self.adopted_rows)

    def message(self) -> str:
        if not self.changed:
            return "Workspace already matches the expected structure."
        parts = [*self.actions]
        if self.adopted_rows:
            rows = ", ".join(str(row) for row in self.adopted_rows)
            parts.append(f"adopted manual row(s) at {rows}")
        return "Repaired: " + "; ".join(parts)


def _cell(row: Sequence[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index])


def _column(name: str) -> int:
    return table.column_index_of(name)


def manual_identity(row: Sequence[str]) -> str | None:
    """Return the identity a manual row should receive, or ``None``.

    The id is derived from the same helper discovery uses, so a manual entry can
    never collide with a discovered one and stays stable across runs.
    """

    if _cell(row, _column(_IDENTITY_COLUMN)).strip():
        return None
    company = _WHITESPACE.sub(" ", _cell(row, _column(_COMPANY_COLUMN)).strip())
    title = _WHITESPACE.sub(" ", _cell(row, _column(_TITLE_COLUMN)).strip())
    if not company or not title:
        return None
    return opportunity_id_for(
        source_type=MANUAL_SOURCE_TYPE,
        company_id=MANUAL_COMPANY_ID,
        external_source_id=f"{company}|{title}",
    )


class WorkspaceRepairService:
    """Reconcile workbook structure against the expected layout."""

    def __init__(self, client: GoogleSheetsClient, spreadsheet_id: str) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id

    def repair(self, *, force: bool = False) -> RepairOutcome:
        """Reconcile structure.

        ``force`` reapplies the presentation layer even when the structure looks
        healthy. That is the escape hatch for a user who deleted only a dropdown:
        the rule is otherwise invisible to metadata, so it cannot be detected
        without an extra grid read on every sync.
        """

        metadata = self._client.get_metadata(self._spreadsheet_id)
        actions: list[str] = []

        for title in (table.OPPORTUNITY_TAB, "Job_Sources"):
            if title not in metadata.titles:
                self._client.add_tabs(self._spreadsheet_id, [title])
                actions.append(f"created the {title} tab")
        if actions:
            metadata = self._client.get_metadata(self._spreadsheet_id)

        header_actions = self._repair_headers(metadata)
        actions.extend(header_actions)

        adopted = self._adopt_manual_rows()

        sheet_id = metadata.sheet_id_for(table.OPPORTUNITY_TAB)
        if sheet_id is None:
            raise GoogleSheetsError(
                "workspace_metadata_incomplete",
                "The Opportunities tab exists but its sheet id is unavailable, so "
                "the table cannot be repaired. Nothing was changed.",
            )

        table_actions = self._repair_table(sheet_id, metadata, force=force)
        actions.extend(table_actions)

        return RepairOutcome(actions=tuple(actions), adopted_rows=tuple(adopted))

    def _repair_headers(self, metadata: SpreadsheetMetadata) -> list[str]:
        """Rewrite a drifted header row, without touching any data row."""

        actions: list[str] = []
        ranges = [f"{table.OPPORTUNITY_TAB}!1:1", "Job_Sources!1:1"]
        values = self._client.get_values(
            self._spreadsheet_id,
            ranges,
            value_render_option=UNFORMATTED_RENDER_OPTION,
        )
        for title, headers in (
            (table.OPPORTUNITY_TAB, OPPORTUNITY_HEADERS),
            ("Job_Sources", JOB_SOURCE_HEADERS),
        ):
            if title not in metadata.titles:
                continue
            current = values.get(f"{title}!1:1") or [[]]
            row = [str(cell).strip() for cell in current[0]] if current else []
            if row[: len(headers)] == list(headers):
                continue
            self._client.batch_update_values(
                self._spreadsheet_id,
                [{"range": f"{title}!A1", "values": [list(headers)]}],
            )
            actions.append(f"restored the {title} header row")
        return actions

    def _adopt_manual_rows(self) -> list[int]:
        """Give a hand-typed row its identity, one cell at a time.

        Only the identity cell is written. Every other value stays exactly as the
        user typed it.
        """

        rows = self._client.get_values(
            self._spreadsheet_id,
            [table.OPPORTUNITY_TAB],
            value_render_option=UNFORMATTED_RENDER_OPTION,
        ).get(table.OPPORTUNITY_TAB, [])
        if not rows:
            return []

        adopted: list[int] = []
        updates: list[dict[str, object]] = []
        for offset, row in enumerate(rows[1:], start=2):
            identity = manual_identity(row)
            if identity is None:
                continue
            updates.append(
                {
                    "range": f"{table.OPPORTUNITY_TAB}!A{offset}",
                    "values": [[identity]],
                }
            )
            adopted.append(offset)
        if updates:
            self._client.batch_update_values(self._spreadsheet_id, updates)
        return adopted

    def _repair_table(
        self,
        sheet_id: int,
        metadata: SpreadsheetMetadata,
        *,
        force: bool,
    ) -> list[str]:
        """Ensure the native table, its dropdowns, and the frozen panes exist.

        A sheet that already carries a table and the expected frozen panes is left
        untouched, so a healthy workbook costs no writes.
        """

        table_ids = metadata.table_ids_for(table.OPPORTUNITY_TAB)
        exists = bool(table_ids)
        properties = metadata.sheet_properties_for(table.OPPORTUNITY_TAB)
        frozen_ok = properties is not None and (
            properties.frozen_row_count == table.FROZEN_ROWS
            and properties.frozen_column_count == table.FROZEN_COLUMNS
        )
        if exists and frozen_ok and not force:
            return []

        rows = properties.row_count if properties is not None else None
        requests: list[dict[str, object]] = [table.build_freeze_request(sheet_id)]
        actions: list[str] = []

        if exists:
            requests.append(table.build_update_table_request(sheet_id, table_ids[0], rows))
            actions.append("updated the workspace table")
        else:
            requests.append(table.build_add_table_request(sheet_id, rows))
            actions.append("created the workspace table")

        self._client.batch_update(self._spreadsheet_id, requests)
        return actions


__all__ = [
    "MANUAL_COMPANY_ID",
    "MANUAL_SOURCE_TYPE",
    "RepairOutcome",
    "WorkspaceRepairService",
    "manual_identity",
]
