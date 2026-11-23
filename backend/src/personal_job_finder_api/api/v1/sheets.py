"""Explicit Google Sheets refresh API.

One route from the accepted contract (``docs/p0/13-api-contract-addendum.md``
§8). It is a thin wrapper around :class:`SheetsRefreshService`; no projection,
validation, or transport rule lives here. Refresh never consumes search quota and
is never scheduled automatically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    effective_spreadsheet_id_or_none,
)
from personal_job_finder_api.sync.refresh import (
    RefreshAlreadyRunning,
    RefreshConfigurationError,
    RefreshError,
    RefreshSchemaError,
    RefreshSnapshotError,
    RefreshStorageError,
    RefreshUnavailableError,
    SheetsRefreshService,
)
from personal_job_finder_api.sync.service import resolve_spreadsheet_id

router = APIRouter(
    tags=["sheets"],
    dependencies=[Depends(validate_local_request)],
)


class SheetsRefreshResponse(BaseModel):
    outcome: str
    refreshed_at_utc: datetime
    last_full_refresh_at_utc: datetime
    counts: dict[str, int]


def get_refresh_service(request: Request) -> SheetsRefreshService:
    state = request.app.state

    def spreadsheet_id() -> str:
        return resolve_spreadsheet_id(
            spreadsheet_id=state.sync_spreadsheet_id,
            fallback=lambda: effective_spreadsheet_id_or_none(state.engine, state.settings),
        )

    return SheetsRefreshService(
        state.engine,
        transport_factory=lambda: state.sync_transport_factory(),
        spreadsheet_id=spreadsheet_id,
        clock=state.clock,
        max_batch_ranges=state.settings.google_sheets_max_batch_ranges,
    )


def refresh_api_error(exc: RefreshError) -> ApiError:
    """Map a refresh failure to one stable, sanitized API error."""

    if isinstance(exc, RefreshAlreadyRunning):
        return ApiError(409, "sheets_refresh_in_progress", str(exc))
    if isinstance(exc, RefreshConfigurationError):
        return ApiError(
            422,
            "sheets_refresh_configuration_invalid",
            "The Google Sheets spreadsheet is not configured for refresh.",
            {"reason": exc.code},
        )
    if isinstance(exc, RefreshSchemaError):
        return ApiError(
            422,
            "sheets_refresh_schema_invalid",
            "The spreadsheet does not match the canonical tab and header contract.",
            {"reason": exc.code},
        )
    if isinstance(exc, RefreshSnapshotError):
        return ApiError(
            422,
            "sheets_refresh_data_invalid",
            "The spreadsheet data was rejected before publication. The previous snapshot was kept.",
            {"reason": exc.code},
        )
    if isinstance(exc, RefreshUnavailableError):
        return ApiError(
            503,
            "sheets_refresh_unavailable",
            "Google Sheets could not be read. No snapshot was published.",
            {"reason": exc.code},
        )
    if isinstance(exc, RefreshStorageError):
        return ApiError(
            503,
            "sheets_refresh_failed",
            "The snapshot could not be committed. The previous snapshot was kept.",
        )
    # Defensive fallback: never expose an unexpected message or provider text.
    return ApiError(503, "sheets_refresh_failed", "The refresh could not be completed.")


@router.post(
    "/sheets/refresh",
    response_model=SheetsRefreshResponse,
    status_code=200,
)
def refresh_sheets(
    response: Response,
    service: Annotated[SheetsRefreshService, Depends(get_refresh_service)],
) -> SheetsRefreshResponse:
    """Read both canonical tabs and publish one validated snapshot."""

    try:
        result = service.refresh()
    except RefreshError as exc:
        raise refresh_api_error(exc) from None

    response.headers["Cache-Control"] = "no-store"
    assert result.last_full_refresh_at_utc is not None
    return SheetsRefreshResponse(
        outcome="succeeded",
        refreshed_at_utc=result.refreshed_at,
        last_full_refresh_at_utc=result.last_full_refresh_at_utc,
        counts=result.counts,
    )
