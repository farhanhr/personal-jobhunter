"""Google Sheets setup API for the Settings UI.

Credential and setup mutations require a local setup session created by exchanging
the one-time bootstrap token (see ``api/setup_session.py``). Responses never include
private key material, raw provider bodies, or the full spreadsheet URL/ID.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.orchestration import run_setup
from personal_job_finder_api.integrations.google_sheets.preflight import (
    SpreadsheetPreflightService,
)
from personal_job_finder_api.integrations.google_sheets.service_account import (
    parse_service_account_key,
)
from personal_job_finder_api.integrations.google_sheets.service_account_store import (
    delete_service_account_key,
    load_service_account_text,
    save_service_account_key,
)
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    EffectiveSpreadsheet,
    GoogleSheetsSettingsStore,
    effective_spreadsheet,
    environment_spreadsheet_id,
    readiness_for_state,
)
from personal_job_finder_api.sync.refresh import RefreshError, SheetsRefreshService

router = APIRouter(
    prefix="/integrations/google-sheets",
    tags=["integrations"],
    dependencies=[Depends(validate_local_request)],
)

MAX_KEY_BYTES = 64 * 1024
SESSION_HEADER = "X-PJF-Setup-Token"
SESSION_COOKIE = "pjf_setup_session"
# Failures that mean the installed credential cannot use the spreadsheet. The
# orchestration reports the same set, so both paths give the same guidance.
ACCESS_ERROR_CODES = frozenset({"permission_denied", "spreadsheet_not_found", "reconnect_required"})


class BootstrapExchange(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class SetupRequest(BaseModel):
    spreadsheet_url: str | None = Field(default=None, min_length=1, max_length=2048)


class IntegrationStatus(BaseModel):
    state: str
    message: str
    service_account_email: str | None
    key_installed: bool
    spreadsheet_configured: bool
    spreadsheet_source: str
    last_setup_stage: str | None = None
    workspace_ready: bool = False
    onboarding_required: bool = True
    configured_via: str | None = None
    setup_completed_at: str | None = None
    configuration_error: str | None = None


def _sessions(request: Request):
    return request.app.state.setup_sessions


def _require_session(request: Request) -> str:
    """Return the CSRF digest for a live session or raise 401."""

    cookie = request.cookies.get(SESSION_COOKIE)
    csrf_header = request.headers.get(SESSION_HEADER)
    now = request.app.state.clock()
    if not _sessions(request).verify_csrf(cookie, csrf_header, now=now):
        raise ApiError(
            401,
            "setup_session_required",
            "Enter the local setup token shown in the server's data folder to continue.",
        )
    return cookie or ""


def _status(request: Request) -> IntegrationStatus:
    settings = request.app.state.settings
    engine = request.app.state.engine
    raw = load_service_account_text(settings)
    saved = GoogleSheetsSettingsStore(engine).load_state()
    ready = readiness_for_state(
        saved,
        environment_id=environment_spreadsheet_id(settings),
    )
    configuration_error: str | None = None
    try:
        effective = effective_spreadsheet(engine, settings)
    except GoogleSheetsError as error:
        # A malformed `.env` spreadsheet URL is invalid configuration, not a
        # server fault: report it structurally instead of failing the status.
        effective = EffectiveSpreadsheet()
        configuration_error = error.code
        ready = False
    email: str | None = None
    credentials_invalid = False
    if raw is not None:
        try:
            email = parse_service_account_key(raw.encode("utf-8")).client_email
        except GoogleSheetsError:
            credentials_invalid = True

    if ready:
        state = "ready"
        if raw is None:
            message = (
                "The Service Account key is missing. Restore it in Settings to resume "
                "synchronization; cached data remains readable."
            )
        elif credentials_invalid:
            message = (
                "The stored key is no longer valid. Upload it again in Settings to resume "
                "synchronization; cached data remains readable."
            )
        else:
            message = "Google Sheets is connected and ready."
    elif credentials_invalid:
        state = "credentials_invalid"
        message = "The stored key is no longer valid. Upload it again."
    elif raw is None:
        state = "not_configured"
        message = "Upload the Service Account JSON key to begin."
    else:
        state = "awaiting_spreadsheet"
        message = "Share the spreadsheet with this email as Editor, then click Save & Setup."
        if effective.spreadsheet_id is not None:
            state = "configured"
            message = "A spreadsheet is configured. Run Save & Setup to validate it."

    return IntegrationStatus(
        state=state,
        message=message,
        service_account_email=email,
        key_installed=raw is not None,
        spreadsheet_configured=effective.spreadsheet_id is not None,
        spreadsheet_source=effective.source,
        workspace_ready=ready,
        onboarding_required=not ready,
        configured_via=None if saved is None else saved.configured_via,
        setup_completed_at=(
            None
            if saved is None or saved.workspace_ready_at is None
            else format_utc(saved.workspace_ready_at)
        ),
        configuration_error=configuration_error,
    )


@router.get("/status", response_model=IntegrationStatus)
def get_status(request: Request, response: Response) -> IntegrationStatus:
    response.headers["Cache-Control"] = "no-store"
    return _status(request)


@router.post("/session", response_model=dict[str, Any])
def exchange_bootstrap(
    payload: BootstrapExchange, request: Request, response: Response
) -> dict[str, Any]:
    """Exchange the one-time local token for a session cookie and CSRF token."""

    data_dir = request.app.state.settings.data_dir
    issued = _sessions(request).exchange_bootstrap(
        payload.token,
        now=request.app.state.clock(),
        data_dir=data_dir,
    )
    if issued is None:
        raise ApiError(401, "bootstrap_invalid", "The setup token is invalid or has expired.")
    response.set_cookie(
        SESSION_COOKIE,
        issued.cookie_value,
        httponly=True,
        samesite="strict",
        path="/api/v1/integrations/google-sheets",
        max_age=8 * 60 * 60,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": issued.csrf_token, "expires_at": issued.expires_at.isoformat()}


def _revalidate_access(request: Request, spreadsheet_id: str) -> None:
    """Read-only check that the installed credential can open the spreadsheet.

    The transport is built from the canonical credential file, so this always
    uses the key that was just installed, never the one it replaced. Nothing is
    written to the spreadsheet.
    """

    transport = request.app.state.sync_transport_factory()
    SpreadsheetPreflightService(transport).check(spreadsheet_id)


def _revalidate_after_replacement(request: Request, email: str) -> ApiError | None:
    """Confirm the newly installed credential can reach the configured sheet.

    A replacement changes the identity the application presents to Google, so
    the previous validation no longer applies. When the new Service Account
    cannot open the spreadsheet, the workspace must be validated again before
    it counts as ready, and the user is told which address to share.
    """

    state = request.app.state
    try:
        effective = effective_spreadsheet(state.engine, state.settings)
    except GoogleSheetsError:
        return None
    if effective.spreadsheet_id is None:
        return None
    try:
        _revalidate_access(request, effective.spreadsheet_id)
    except GoogleSheetsError as error:
        if error.code == "reconnect_required":
            message = (
                f"The key was saved, but Google rejected it. Create a new key for {email}, "
                "then run Save & Setup again."
            )
        elif error.code == "spreadsheet_not_found":
            message = (
                "The key was saved, but the configured spreadsheet could not be found. "
                "Check the spreadsheet link, then run Save & Setup again."
            )
        elif error.code == "permission_denied":
            message = (
                "The key was saved, but Google denied access to the spreadsheet. Share it "
                f"with {email} as Editor, then run Save & Setup again."
            )
        else:
            message = (
                "The key was saved, but the spreadsheet could not be checked. "
                "Run Save & Setup again."
            )
        if error.code in ACCESS_ERROR_CODES:
            # The credential that validated this workspace is gone, so the
            # workspace is not ready until setup validates it again.
            GoogleSheetsSettingsStore(state.engine).clear_workspace_readiness()
        return ApiError(422, error.code, message)
    return None


@router.put("/service-account", response_model=IntegrationStatus)
async def replace_service_account(request: Request, response: Response) -> IntegrationStatus:
    _require_session(request)
    raw = await request.body()
    if len(raw) > MAX_KEY_BYTES:
        raise ApiError(413, "key_too_large", "The uploaded key is larger than 64 KB.")
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        raise ApiError(415, "unsupported_media_type", "Upload the JSON key file.")
    try:
        key = parse_service_account_key(raw)
        save_service_account_key(request.app.state.settings, key)
    except GoogleSheetsError as error:
        raise ApiError(422, error.code, str(error)) from None
    failure = _revalidate_after_replacement(request, key.client_email)
    if failure is not None:
        raise failure
    response.headers["Cache-Control"] = "no-store"
    return _status(request)


def _refresh_counts(request: Request):
    """Build the refresh callback used after a successful setup."""

    state = request.app.state

    def refresh(spreadsheet_id: str) -> dict[str, int]:
        service = SheetsRefreshService(
            state.engine,
            transport_factory=lambda: state.sync_transport_factory(),
            spreadsheet_id=lambda: spreadsheet_id,
            clock=state.clock,
            max_batch_ranges=state.settings.google_sheets_max_batch_ranges,
        )
        try:
            return service.refresh().counts
        except RefreshError as error:
            raise GoogleSheetsError(error.code, str(error)) from error

    return refresh


@router.post("/setup", response_model=dict[str, Any])
def save_and_setup(
    request: Request,
    response: Response,
    payload: SetupRequest | None = None,
) -> dict[str, Any]:
    _require_session(request)
    result = run_setup(
        request.app.state.settings,
        request.app.state.engine,
        spreadsheet_input=None if payload is None else payload.spreadsheet_url,
        refresh=_refresh_counts(request),
    )
    response.headers["Cache-Control"] = "no-store"
    if not result.ready and result.code in {"spreadsheet_url_invalid", "spreadsheet_id_missing"}:
        raise ApiError(422, result.code, result.message)
    if not result.ready and result.code == "workspace_incompatible":
        raise ApiError(
            409,
            result.code,
            result.message,
            {"preserved": result.preserved},
        )
    return {
        "ready": result.ready,
        "stage": result.stage,
        "code": result.code,
        "message": result.message,
        "service_account_email": result.service_account_email,
        "source": result.source,
        "retry_safe": result.retry_safe,
        "preserved": result.preserved,
        "optional_warning": result.optional_warning,
        "workspace_ready": result.workspace_ready,
        "adopted_existing": result.adopted_existing,
        "refresh_warning": result.refresh_warning,
        "counts": result.counts,
    }


@router.delete("/service-account", response_model=IntegrationStatus)
def disconnect(request: Request, response: Response) -> IntegrationStatus:
    _require_session(request)
    try:
        delete_service_account_key(request.app.state.settings)
    except GoogleSheetsError as error:
        raise ApiError(500, error.code, str(error)) from None
    response.headers["Cache-Control"] = "no-store"
    return _status(request)
