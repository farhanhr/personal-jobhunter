"""Optional, formula-only ``Metrics`` tab initialization.

The Metrics board is a companion view. It is never a canonical record, never part of
:data:`personal_job_finder_api.integrations.google_sheets.config.REQUIRED_TABS`,
and never read by preflight, refresh, or the sync dispatcher.

Safety rules enforced here:

- The spreadsheet time zone must already be ``Asia/Jakarta``. It is never
  changed automatically, because that can reinterpret existing date cells.
- A non-empty tab without our version sentinel is refused, never overwritten.
- An unsupported sentinel version is refused, never upgraded in place.
- An already-initialized tab is a successful no-op, so user edits survive.
- Layout, formatting, and the sentinel share one atomic ``batchUpdate``, so a
  written sentinel always implies a complete layout and a failed batch leaves
  nothing behind. ``addSheet`` is a separate call; atomicity is not claimed
  across it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout
from personal_job_finder_api.integrations.google_sheets.client import (
    FORMULA_RENDER_OPTION,
    GoogleSheetsClient,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError


class MetricsInitState(StrEnum):
    """How the last initialization call left the Metrics tab."""

    CREATED = "created"
    INITIALIZED = "initialized"
    ALREADY_INITIALIZED = "already_initialized"


@dataclass(frozen=True, slots=True)
class MetricsInitResult:
    """A sanitized outcome that never carries the spreadsheet id."""

    state: MetricsInitState
    message: str


_MESSAGES = {
    MetricsInitState.CREATED: "Metrics tab created and initialized.",
    MetricsInitState.INITIALIZED: "Metrics tab initialized.",
    MetricsInitState.ALREADY_INITIALIZED: (
        "Metrics tab is already initialized; nothing was changed."
    ),
}


class MetricsInitService:
    """Create and initialize the optional Metrics board exactly once."""

    def __init__(self, client: GoogleSheetsClient, spreadsheet_id: str) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id

    def initialize(self) -> MetricsInitResult:
        metadata = self._client.get_metadata(self._spreadsheet_id)
        _require_jakarta_timezone(metadata.time_zone)
        metadata = self._adopt_legacy_tab(metadata)

        sheet_id = metadata.sheet_id_for(layout.METRICS_TAB)
        created = False

        if sheet_id is None:
            if layout.METRICS_TAB in metadata.titles:
                raise GoogleSheetsError(
                    "dashboard_metadata_incomplete",
                    "The Metrics tab exists but its sheet id is unavailable, so "
                    "formatting cannot be applied safely. Nothing was changed.",
                )
            # Resolve the formula dialect before creating the tab, so an
            # unsupported locale never leaves a half-initialized Metrics board.
            dialect = _formula_dialect(metadata.locale)
            self._client.add_tabs(self._spreadsheet_id, [layout.METRICS_TAB])
            created = True
            metadata = self._client.get_metadata(self._spreadsheet_id)
            sheet_id = metadata.sheet_id_for(layout.METRICS_TAB)
            if sheet_id is None:
                raise GoogleSheetsError(
                    "dashboard_metadata_incomplete",
                    "The Metrics tab was created but its sheet id is unavailable. "
                    "Run the command again to finish initialization.",
                )
        else:
            existing = self._inspect_existing_tab()
            if existing is not None:
                return existing
            dialect = _formula_dialect(metadata.locale)

        self._client.batch_update(
            self._spreadsheet_id,
            layout.build_initialization_requests(sheet_id, dialect),
        )

        state = MetricsInitState.CREATED if created else MetricsInitState.INITIALIZED
        return MetricsInitResult(state=state, message=_MESSAGES[state])

    def _adopt_legacy_tab(self, metadata: SpreadsheetMetadata) -> SpreadsheetMetadata:
        """Rename the old ``Dashboard`` metric board to ``Metrics``.

        Before the workspace split, the metric board lived on ``Dashboard``. The
        name now belongs to the summary-cards tab, so an existing board is moved
        rather than deleted: renaming carries every formula and the sentinel with
        it. A ``Dashboard`` tab we do not own is left alone for the cards service
        to classify.
        """

        if layout.METRICS_TAB in metadata.titles or "Dashboard" not in metadata.titles:
            return metadata

        sheet_id = metadata.sheet_id_for("Dashboard")
        if sheet_id is None:
            return metadata

        sentinel_rows = self._client.get_values(
            self._spreadsheet_id,
            [f"Dashboard!{layout.SENTINEL_CELL}"],
            value_render_option=FORMULA_RENDER_OPTION,
        )
        rows = sentinel_rows.get(f"Dashboard!{layout.SENTINEL_CELL}") or []
        sentinel = rows[0][0] if rows and rows[0] else ""
        if not sentinel.startswith(layout.METRICS_SENTINEL_PREFIX):
            return metadata

        self._client.batch_update(
            self._spreadsheet_id,
            [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": layout.METRICS_TAB,
                        },
                        "fields": "title",
                    }
                }
            ],
        )
        return self._client.get_metadata(self._spreadsheet_id)

    def _inspect_existing_tab(self) -> MetricsInitResult | None:
        """Classify an existing tab by reading its entire content.

        The read uses the ``FORMULA`` render over the whole tab, so a formula
        whose displayed result is an empty string is still visible as
        user-entered content. No fixed row or column boundary is assumed: a
        foreign value far outside the layout cannot be mistaken for a blank tab.
        If the read cannot be completed, initialization is refused instead of
        assuming the tab is empty.
        """

        try:
            grid = self._client.get_values(
                self._spreadsheet_id,
                [layout.INSPECT_RANGE],
                value_render_option=FORMULA_RENDER_OPTION,
            ).get(layout.INSPECT_RANGE, [])
        except GoogleSheetsError as exc:
            raise GoogleSheetsError(
                "dashboard_inspection_failed",
                "The existing Metrics tab could not be inspected, so it was not "
                "treated as empty. Nothing was changed; check the Google Sheets "
                "connection and retry.",
            ) from exc

        sentinel = _cell_text(grid, layout.parse_address(layout.SENTINEL_CELL))

        if sentinel == layout.METRICS_SENTINEL:
            return MetricsInitResult(
                state=MetricsInitState.ALREADY_INITIALIZED,
                message=_MESSAGES[MetricsInitState.ALREADY_INITIALIZED],
            )
        if sentinel.startswith(layout.METRICS_SENTINEL_PREFIX):
            raise GoogleSheetsError(
                "dashboard_unsupported_version",
                "The Metrics tab was written by an unsupported version of this "
                "application. Nothing was changed; inspect the tab before retrying.",
            )
        if any(str(cell).strip() for row in grid for cell in row):
            raise GoogleSheetsError(
                "dashboard_tab_not_owned",
                "The Metrics tab already contains data that this application did "
                "not write. Nothing was changed; rename or clear that tab manually "
                "if you want an initialized Metrics board.",
            )
        return None


def _formula_dialect(locale: str | None) -> layout.FormulaDialect:
    """Return the formula dialect for the spreadsheet locale.

    An unknown or missing locale is refused instead of guessed: generating
    formulas with the wrong separator produces ``#ERROR!`` cells, which is worse
    than a clear refusal.
    """

    if locale is None:
        raise GoogleSheetsError(
            "dashboard_locale_missing",
            "The spreadsheet locale is unknown, so Metrics formulas cannot be "
            "generated safely. Nothing was changed.",
        )
    dialect = layout.resolve_formula_dialect(locale)
    if dialect is None:
        supported = ", ".join(sorted(layout.FORMULA_DIALECTS))
        raise GoogleSheetsError(
            "dashboard_locale_unsupported",
            f"The spreadsheet locale {locale} is not supported for Metrics "
            f"formulas (supported: {supported}). Nothing was changed.",
        )
    return dialect


def _require_jakarta_timezone(time_zone: str | None) -> None:
    if time_zone is None:
        raise GoogleSheetsError(
            "dashboard_timezone_missing",
            f"The spreadsheet time zone is unknown. Set it to {layout.DASHBOARD_TIMEZONE} "
            "in Google Sheets before initializing the Metrics board. Nothing was changed.",
        )
    if time_zone != layout.DASHBOARD_TIMEZONE:
        raise GoogleSheetsError(
            "dashboard_timezone_mismatch",
            f"The spreadsheet time zone is {time_zone}, but the Metrics board requires "
            f"{layout.DASHBOARD_TIMEZONE}. Set it manually in Google Sheets "
            "(File > Settings > Time zone) and run the command again. "
            "Nothing was changed.",
        )


def _cell_text(grid: Sequence[Sequence[str]], position: tuple[int, int]) -> str:
    row, column = position
    if row >= len(grid):
        return ""
    cells = grid[row]
    if column >= len(cells):
        return ""
    value = cells[column]
    return "" if value is None else str(value)
