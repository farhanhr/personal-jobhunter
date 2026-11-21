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
from personal_job_finder_api.integrations.google_sheets import dashboard_layout as metrics
from personal_job_finder_api.integrations.google_sheets import kpi_strip
from personal_job_finder_api.integrations.google_sheets import workspace_table as table
from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
    GoogleSheetsClient,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_FIRST_DATA_ROW,
    OPPORTUNITY_HEADER_ROW,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.dashboard_layout import (
    FormulaDialect,
    resolve_formula_dialect,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

#: A manual row is identified by these two columns being present while the
#: identity column is empty. Anything less is an incomplete row, not an entry.
MANUAL_SOURCE_TYPE: Final = "manual"
MANUAL_COMPANY_ID: Final = "manual"

#: Marks the presentation layer (KPI strip plus cell validation) as applied. The
#: layout itself cannot be detected from metadata, so this literal cell is the
#: only cheap way to keep a second repair write-free. ``--force`` reapplies it.
WORKSPACE_SENTINEL_CELL: Final = "AH2"
WORKSPACE_SENTINEL: Final = "PJF_WORKSPACE_V1"

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


def _formula_dialect(locale: str | None) -> FormulaDialect:
    """Return the formula dialect for the spreadsheet locale.

    An unknown or missing locale is refused instead of guessed: a strip built with
    the wrong argument separator renders ``#ERROR!`` in every value cell.
    """

    if locale is None:
        raise GoogleSheetsError(
            "workspace_locale_missing",
            "The spreadsheet locale is unknown, so the KPI strip cannot be built "
            "safely. Nothing was changed.",
        )
    dialect = resolve_formula_dialect(locale)
    if dialect is None:
        supported = ", ".join(sorted(metrics.FORMULA_DIALECTS))
        raise GoogleSheetsError(
            "workspace_locale_unsupported",
            f"The spreadsheet locale {locale} is not supported for the KPI strip "
            f"(supported: {supported}). Nothing was changed.",
        )
    return dialect


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

        migration = self._migrate_header_row(metadata)
        if migration:
            actions.append(migration)
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

        presentation = force or not self._presentation_is_current(metadata)
        table_actions = self._repair_table(
            sheet_id,
            metadata,
            force=force,
            refresh_table=presentation,
        )
        actions.extend(table_actions)

        helper = self._ensure_helper_column(sheet_id, metadata)
        if helper:
            actions.append(helper)
            metadata = self._client.get_metadata(self._spreadsheet_id)

        strip_actions = self._repair_presentation(sheet_id, metadata, force=force)
        actions.extend(strip_actions)

        return RepairOutcome(actions=tuple(actions), adopted_rows=tuple(adopted))

    def _ensure_helper_column(
        self,
        sheet_id: int,
        metadata: SpreadsheetMetadata,
    ) -> str | None:
        """Append and hide the helper column the KPI strip reads from."""

        properties = metadata.sheet_properties_for(table.OPPORTUNITY_TAB)
        if properties is None:
            return None
        request = table.build_helper_column_request(sheet_id, properties.column_count)
        if request is None:
            return None
        # Two batches: the hide request names a column that does not exist until
        # the append is applied, and Google validates the whole batch first.
        self._client.batch_update(self._spreadsheet_id, [request])
        self._client.batch_update(
            self._spreadsheet_id,
            [table.build_hide_helper_request(sheet_id)],
        )
        return "added the hidden helper column"

    def _migrate_header_row(self, metadata: SpreadsheetMetadata) -> str | None:
        """Move a legacy row-1 header below the KPI strip, exactly once.

        Four rows are inserted above the header, which shifts every canonical row
        down by four without rewriting a single value. A header anywhere other
        than row 1 or the canonical row is refused: moving an unknown layout
        blindly could bury a data row under the strip.
        """

        first_row_range = f"{table.OPPORTUNITY_TAB}!A1:AO1"
        canonical_range = (
            f"{table.OPPORTUNITY_TAB}!A{OPPORTUNITY_HEADER_ROW}:AO{OPPORTUNITY_HEADER_ROW}"
        )
        values = self._client.get_values(
            self._spreadsheet_id,
            [first_row_range, canonical_range],
            value_render_option=UNFORMATTED_RENDER_OPTION,
        )
        first = [str(cell).strip() for cell in (values.get(first_row_range) or [[]])[0]]
        canonical = [str(cell).strip() for cell in (values.get(canonical_range) or [[]])[0]]

        if tuple(first[: len(OPPORTUNITY_HEADERS)]) == OPPORTUNITY_HEADERS:
            sheet_id = metadata.sheet_id_for(table.OPPORTUNITY_TAB)
            if sheet_id is None:
                raise GoogleSheetsError(
                    "workspace_metadata_incomplete",
                    "The Opportunities tab exists but its sheet id is unavailable, so "
                    "the header cannot be moved. Nothing was changed.",
                )
            self._client.batch_update(
                self._spreadsheet_id,
                [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": 0,
                                "endIndex": OPPORTUNITY_HEADER_ROW - 1,
                            },
                            "inheritFromBefore": False,
                        }
                    }
                ],
            )
            return f"moved the canonical header to row {OPPORTUNITY_HEADER_ROW}"

        if tuple(canonical[: len(OPPORTUNITY_HEADERS)]) == OPPORTUNITY_HEADERS:
            return None

        if not any(first) or first[0] == kpi_strip.cards()[0].label:
            # An empty tab or an already-migrated strip: the header repair below
            # writes the canonical row in place.
            return None

        raise GoogleSheetsError(
            "workspace_header_row_unknown",
            "The Opportunities tab has content in row 1 that is neither the "
            f"canonical header nor the KPI strip, and row {OPPORTUNITY_HEADER_ROW} "
            "is not the canonical header. Nothing was changed; move the header "
            f"to row {OPPORTUNITY_HEADER_ROW} manually and run the repair again.",
        )

    def _repair_headers(self, metadata: SpreadsheetMetadata) -> list[str]:
        """Rewrite a drifted header row, without touching any data row."""

        actions: list[str] = []
        targets = (
            (table.OPPORTUNITY_TAB, OPPORTUNITY_HEADERS, OPPORTUNITY_HEADER_ROW),
            ("Job_Sources", JOB_SOURCE_HEADERS, 1),
        )
        ranges = [f"{title}!{row}:{row}" for title, _, row in targets]
        values = self._client.get_values(
            self._spreadsheet_id,
            ranges,
            value_render_option=UNFORMATTED_RENDER_OPTION,
        )
        for (title, headers, row_number), range_name in zip(targets, ranges, strict=True):
            if title not in metadata.titles:
                continue
            current = values.get(range_name) or [[]]
            row = [str(cell).strip() for cell in current[0]] if current else []
            if row[: len(headers)] == list(headers):
                continue
            self._client.batch_update_values(
                self._spreadsheet_id,
                [{"range": f"{title}!A{row_number}", "values": [list(headers)]}],
            )
            actions.append(f"restored the {title} header row")
        return actions

    def _adopt_manual_rows(self) -> list[int]:
        """Give a hand-typed row its identity, one cell at a time.

        Only the identity cell is written. Every other value stays exactly as the
        user typed it. Rows above the canonical header are the KPI strip, never
        candidates.
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
        for offset, row in enumerate(
            rows[OPPORTUNITY_FIRST_DATA_ROW - 1 :],
            start=OPPORTUNITY_FIRST_DATA_ROW,
        ):
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

    def _presentation_is_current(self, metadata: SpreadsheetMetadata) -> bool:
        """True when the strip cells match and the sentinel is current.

        One grid read answers both questions, so a healthy workbook costs a
        single extra read instead of a write. The expected formula text is
        rendered with the spreadsheet's own argument separator: the provider
        returns ``;`` for an Indonesian locale, and comparing against ``en_US``
        would rewrite the strip on every run.
        """

        values = self._read_grid([f"{table.OPPORTUNITY_TAB}!A1:P3", kpi_strip.BOUND_RANGE])
        if values.get(WORKSPACE_SENTINEL_CELL) != WORKSPACE_SENTINEL:
            return False
        dialect = _formula_dialect(metadata.locale)
        return all(values.get(cell.address) == cell.content for cell in kpi_strip.cells(dialect))

    def _read_grid(self, ranges: Sequence[str]) -> dict[str, str]:
        grid = self._client.get_grid(self._spreadsheet_id, list(ranges))
        values: dict[str, str] = {}
        for cells in grid.values():
            for row in cells:
                for cell in row:
                    values[cell.address] = cell.formula or cell.literal or ""
        return values

    def _repair_presentation(
        self,
        sheet_id: int,
        metadata: SpreadsheetMetadata,
        *,
        force: bool,
    ) -> list[str]:
        """Write the KPI strip and its sentinel when missing or drifted."""

        if not force and self._presentation_is_current(metadata):
            return []
        dialect = _formula_dialect(metadata.locale)
        requests = kpi_strip.build_requests(sheet_id, dialect)
        requests.append(
            {
                "updateCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 2,
                        "startColumnIndex": 33,
                        "endColumnIndex": 34,
                    },
                    "rows": [
                        {
                            "values": [
                                {
                                    "userEnteredValue": {
                                        "stringValue": WORKSPACE_SENTINEL,
                                    }
                                }
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            }
        )
        self._client.batch_update(self._spreadsheet_id, requests)
        return ["rebuilt the KPI strip"]

    def _repair_table(
        self,
        sheet_id: int,
        metadata: SpreadsheetMetadata,
        *,
        force: bool,
        refresh_table: bool,
    ) -> list[str]:
        """Ensure the native table, its dropdowns, and the frozen panes exist.

        A sheet that already carries a table covering the whole grid, the expected
        frozen panes, and a current presentation layer is left untouched, so a
        healthy workbook costs no writes.

        The table is rewritten when the grid grew past it. That is the fix for a
        picker that only appears on a few rows: the dropdown lives on the table
        column, so a table that stopped at the old grid size left every later row
        without one.
        """

        table_ids = metadata.table_ids_for(table.OPPORTUNITY_TAB)
        exists = bool(table_ids)
        properties = metadata.sheet_properties_for(table.OPPORTUNITY_TAB)
        frozen_ok = properties is not None and (
            properties.frozen_row_count == table.FROZEN_ROWS
            and properties.frozen_column_count == table.FROZEN_COLUMNS
        )
        covers_grid = properties is not None and properties.table_end_rows == (
            properties.row_count,
        )
        if exists and frozen_ok and covers_grid and not force and not refresh_table:
            return []

        rows = properties.row_count if properties is not None else None
        requests: list[dict[str, object]] = [table.build_freeze_request(sheet_id)]
        actions: list[str] = []

        if exists and not force and not refresh_table and covers_grid:
            # The table itself is healthy; only the frozen panes drifted.
            actions.append("restored the frozen panes")
        elif exists:
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
