"""Manual Google Sheets synchronization API.

Three routes from the accepted contract (``docs/p0/13-api-contract-addendum.md``
§8). Each is a thin wrapper around the M4B2 dispatcher, outbox repository, and
reconciliation logic; no retry, projection, or backoff rule lives here.

Synchronization is manual and process-local. There is no startup hook, scheduler,
or background retry loop.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    effective_spreadsheet_id_or_none,
)
from personal_job_finder_api.sync.dispatcher import SyncAlreadyRunning
from personal_job_finder_api.sync.service import (
    SyncConfigurationError,
    SyncDispatchService,
    SyncOutboxNotFound,
    SyncRequeueInvalidState,
    SyncRequeueService,
    SyncStatusService,
    resolve_spreadsheet_id,
)

router = APIRouter(
    tags=["sync"],
    dependencies=[Depends(validate_local_request)],
)


class BlockedProjection(BaseModel):
    """Actionable metadata for one blocked projection. No payload is included."""

    outbox_id: str
    entity_type: str
    entity_id: str
    attempt_count: int
    max_attempts: int
    last_error_code: str | None
    next_attempt_at_utc: str | None
    updated_at_utc: str


class SyncStatusResponse(BaseModel):
    counts: dict[str, dict[str, int]]
    totals: dict[str, int]
    due_now: int
    action_required: bool
    blocked: list[BlockedProjection]
    blocked_truncated: bool
    last_synced_at_utc: str | None
    checked_at_utc: str


class SyncRetryResponse(BaseModel):
    outcome: str
    recovered: int
    synced: int
    failed: int
    blocked: int
    remaining: int


class SyncRequeueResponse(BaseModel):
    outbox_id: str
    status: str


def get_status_service(request: Request) -> SyncStatusService:
    return SyncStatusService(
        request.app.state.engine,
        clock=request.app.state.clock,
    )


def get_dispatch_service(request: Request) -> SyncDispatchService:
    state = request.app.state

    def spreadsheet_id() -> str:
        return resolve_spreadsheet_id(
            spreadsheet_id=state.sync_spreadsheet_id,
            fallback=lambda: effective_spreadsheet_id_or_none(state.engine, state.settings),
        )

    return SyncDispatchService(
        state.engine,
        transport_factory=lambda: state.sync_transport_factory(),
        spreadsheet_id=spreadsheet_id,
        clock=state.clock,
        batch_size=state.settings.google_sheets_batch_size,
        max_batch_ranges=state.settings.google_sheets_max_batch_ranges,
    )


def get_requeue_service(request: Request) -> SyncRequeueService:
    return SyncRequeueService(
        request.app.state.engine,
        clock=request.app.state.clock,
    )


@router.get("/sync/status", response_model=SyncStatusResponse)
def get_sync_status(
    response: Response,
    service: Annotated[SyncStatusService, Depends(get_status_service)],
) -> SyncStatusResponse:
    """Return aggregate outbox counts and anything that needs user action.

    This reads only local operational state: no Google request, no credential,
    and no payload is involved.
    """

    response.headers["Cache-Control"] = "no-store"
    return SyncStatusResponse(**service.snapshot())


@router.post(
    "/sync/retry",
    response_model=SyncRetryResponse,
    status_code=200,
)
def retry_sync(
    response: Response,
    request: Request,
    service: Annotated[SyncDispatchService, Depends(get_dispatch_service)],
) -> SyncRetryResponse:
    """Dispatch due ``pending``/``retry_wait`` projections once, synchronously.

    The dispatcher runs in this request rather than a background task, so the
    response reports the real outcome instead of an optimistic acceptance. A
    duplicate trigger while a dispatch is running receives ``409``.
    """

    try:
        result = service.dispatch()
    except SyncConfigurationError as exc:
        raise ApiError(
            422,
            "sync_configuration_invalid",
            "The Google Sheets spreadsheet is not configured for synchronization.",
            {"reason": exc.code},
        ) from None
    except SyncAlreadyRunning:
        raise ApiError(
            409,
            "sync_already_running",
            "A Google Sheets refresh or synchronization is already running. Wait for it to finish.",
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return SyncRetryResponse(**result.as_dict())


@router.post(
    "/sync/outbox/{outbox_id}/requeue",
    response_model=SyncRequeueResponse,
    status_code=200,
)
def requeue_projection(
    outbox_id: str,
    response: Response,
    service: Annotated[SyncRequeueService, Depends(get_requeue_service)],
) -> SyncRequeueResponse:
    """Re-enable exactly one ``blocked`` projection after its cause is fixed.

    There is deliberately no bulk retry: clearing every blocked error silently
    could resend work whose cause the user never resolved.
    """

    try:
        status = service.requeue(outbox_id)
    except SyncOutboxNotFound:
        raise ApiError(
            404,
            "sync_outbox_not_found",
            "The synchronization projection was not found.",
        ) from None
    except SyncRequeueInvalidState as exc:
        raise ApiError(
            409,
            "sync_requeue_invalid_state",
            "Only a blocked synchronization projection can be requeued.",
            {"status": exc.status},
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return SyncRequeueResponse(outbox_id=outbox_id, status=status)
