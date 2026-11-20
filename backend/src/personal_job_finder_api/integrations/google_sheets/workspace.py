"""Create and initialize the ``Dashboard`` summary-cards tab.

The cards tab is a companion view. It is never a canonical record, never part of
:data:`personal_job_finder_api.integrations.google_sheets.config.REQUIRED_TABS`,
and never read by preflight, refresh, or the sync dispatcher.

Safety rules enforced here, the same ones the metric board uses:

- The spreadsheet time zone must already be ``Asia/Jakarta``. It is never changed
  automatically, because that can reinterpret existing date cells.
- A non-empty tab without our version sentinel is refused, never overwritten.
- An unsupported sentinel version is refused, never upgraded in place.
- An already-initialized tab is a successful no-op, so user edits survive.
- Layout, formatting, and the sentinel share one atomic ``batchUpdate``, so a
  written sentinel always implies a complete layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from personal_job_finder_api.integrations.google_sheets import workspace_layout as layout
from personal_job_finder_api.integrations.google_sheets.client import (
    FORMULA_RENDER_OPTION,
    GoogleSheetsClient,
)
from personal_job_finder_api.integrations.google_sheets.dashboard import (
    _formula_dialect,
    _require_jakarta_timezone,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError


class DashboardInitState(StrEnum):
    """How the last initialization call left the cards tab."""

    CREATED = "created"
    INITIALIZED = "initialized"
    ALREADY_INITIALIZED = "already_initialized"


@dataclass(frozen=True, slots=True)
class DashboardInitResult:
    """A sanitized outcome that never carries the spreadsheet id."""

    state: DashboardInitState
    message: str


_MESSAGES = {
    DashboardInitState.CREATED: "Dashboard cards tab created and initialized.",
    DashboardInitState.INITIALIZED: "Dashboard cards tab initialized.",
    DashboardInitState.ALREADY_INITIALIZED: (
        "Dashboard cards tab is already initialized; nothing was changed."
    ),
}


class WorkspaceDashboardService:
    """Create and initialize the summary-cards tab exactly once."""

    def __init__(self, client: GoogleSheetsClient, spreadsheet_id: str) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id

    def initialize(self) -> DashboardInitResult:
        metadata = self._client.get_metadata(self._spreadsheet_id)
        _require_jakarta_timezone(metadata.time_zone)

        sheet_id = metadata.sheet_id_for(layout.DASHBOARD_TAB)
        created = False

        if sheet_id is None:
            if layout.DASHBOARD_TAB in metadata.titles:
                raise GoogleSheetsError(
                    "dashboard_metadata_incomplete",
                    "The Dashboard tab exists but its sheet id is unavailable, so "
                    "formatting cannot be applied safely. Nothing was changed.",
                )
            dialect = _formula_dialect(metadata.locale)
            self._client.add_tabs(self._spreadsheet_id, [layout.DASHBOARD_TAB])
            created = True
            metadata = self._client.get_metadata(self._spreadsheet_id)
            sheet_id = metadata.sheet_id_for(layout.DASHBOARD_TAB)
            if sheet_id is None:
                raise GoogleSheetsError(
                    "dashboard_metadata_incomplete",
                    "The Dashboard tab was created but its sheet id is unavailable. "
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

        state = DashboardInitState.CREATED if created else DashboardInitState.INITIALIZED
        return DashboardInitResult(state=state, message=_MESSAGES[state])

    def _inspect_existing_tab(self) -> DashboardInitResult | None:
        """Classify an existing tab by reading its entire content.

        The read uses the ``FORMULA`` render over the whole tab, so a formula whose
        displayed result is empty is still visible as user-entered content. No
        fixed row or column boundary is assumed: a foreign value far outside the
        layout cannot be mistaken for a blank tab.
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
                "The existing Dashboard tab could not be inspected, so it was not "
                "treated as empty. Nothing was changed; check the Google Sheets "
                "connection and retry.",
            ) from exc

        sentinel = _cell_text(grid, layout.parse_address(layout.SENTINEL_CELL))

        if sentinel == layout.DASHBOARD_SENTINEL:
            return DashboardInitResult(
                state=DashboardInitState.ALREADY_INITIALIZED,
                message=_MESSAGES[DashboardInitState.ALREADY_INITIALIZED],
            )
        if sentinel.startswith(layout.DASHBOARD_SENTINEL_PREFIX):
            raise GoogleSheetsError(
                "dashboard_unsupported_version",
                "The Dashboard tab was written by an unsupported version of this "
                "application. Nothing was changed; inspect the tab before retrying.",
            )
        if any(str(cell).strip() for row in grid for cell in row):
            raise GoogleSheetsError(
                "dashboard_tab_not_owned",
                "The Dashboard tab already contains data that this application did "
                "not write. Nothing was changed; rename or clear that tab manually "
                "if you want an initialized Dashboard.",
            )
        return None


def _cell_text(grid: Sequence[Sequence[str]], position: tuple[int, int]) -> str:
    """Read one cell from a whole-tab values read."""

    row, column = position
    if row >= len(grid):
        return ""
    values = grid[row]
    if column >= len(values):
        return ""
    return str(values[column])


__all__ = [
    "DashboardInitResult",
    "DashboardInitState",
    "WorkspaceDashboardService",
]
