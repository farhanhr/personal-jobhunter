"""Delete the retired ``Dashboard`` cards tab, and only that tab.

ADR-027 moved the summary into the KPI strip on ``Opportunities``. Two summary
surfaces were redundant, and the cards tab duplicated a dashboard the application
already has.

Deletion is destructive, so it is guarded: the tab is removed only when its ``M1``
sentinel is the exact version this project wrote. A tab the owner built by hand,
or a board from a different version, is refused and left untouched.
"""

from __future__ import annotations

from typing import Final

from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
    GoogleSheetsClient,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

DASHBOARD_TAB: Final = "Dashboard"

#: The retired cards layout wrote this into ``M1``; nothing else did.
DASHBOARD_SENTINEL: Final = "PJF_DASHBOARD_CARDS_V1"
DASHBOARD_SENTINEL_PREFIX: Final = "PJF_DASHBOARD_"
SENTINEL_CELL: Final = "M1"

_MISSING = "The Dashboard tab does not exist; nothing was changed."
_REMOVED = "Deleted the retired Dashboard cards tab."
_REFUSED = (
    "The Dashboard tab does not carry this project's cards sentinel, so it is not "
    "owned by this version and was left untouched."
)


class DashboardRemovalService:
    """Delete the cards tab when, and only when, this project owns it."""

    def __init__(self, client: GoogleSheetsClient, spreadsheet_id: str) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id

    def remove(self) -> str:
        metadata = self._client.get_metadata(self._spreadsheet_id)
        if DASHBOARD_TAB not in metadata.titles:
            return _MISSING

        sheet_id = metadata.sheet_id_for(DASHBOARD_TAB)
        if sheet_id is None:
            raise GoogleSheetsError(
                "dashboard_metadata_incomplete",
                "The Dashboard tab exists but its sheet id is unavailable, so it "
                "cannot be deleted safely. Nothing was changed.",
            )

        range_name = f"{DASHBOARD_TAB}!{SENTINEL_CELL}"
        values = self._client.get_values(
            self._spreadsheet_id,
            [range_name],
            value_render_option=UNFORMATTED_RENDER_OPTION,
        )
        rows = values.get(range_name) or []
        sentinel = str(rows[0][0]).strip() if rows and rows[0] else ""
        if sentinel != DASHBOARD_SENTINEL:
            if sentinel.startswith(DASHBOARD_SENTINEL_PREFIX):
                raise GoogleSheetsError(
                    "dashboard_version_unsupported",
                    "The Dashboard tab carries a different cards version. Inspect "
                    "and delete it manually. Nothing was changed.",
                )
            return _REFUSED

        self._client.batch_update(
            self._spreadsheet_id,
            [{"deleteSheet": {"sheetId": sheet_id}}],
        )
        return _REMOVED


__all__ = [
    "DASHBOARD_SENTINEL",
    "DASHBOARD_SENTINEL_PREFIX",
    "DASHBOARD_TAB",
    "SENTINEL_CELL",
    "DashboardRemovalService",
]
