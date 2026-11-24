"""AI provider settings and connection testing (ADR-030).

The API never returns a stored API key. Reads expose :func:`mask_api_key`, and a
write that submits the mask keeps the stored key untouched, so the Settings UI
can show a hint without holding the secret.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from personal_job_finder_api.ai.connection import test_llm_connection
from personal_job_finder_api.ai.errors import AIServiceError
from personal_job_finder_api.ai.providers import (
    validate_base_url,
    validate_model,
    validate_provider,
)
from personal_job_finder_api.ai.settings_store import (
    AISettings,
    AISettingsStore,
    compute_is_configured,
    mask_api_key,
    resolve_api_key_update,
)
from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import DEFAULT_AI_MODEL, DEFAULT_AI_PROVIDER

router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[Depends(validate_local_request)],
)


class AISettingsResponse(BaseModel):
    provider: str
    base_url: str
    api_key_masked: str | None = None
    model: str
    is_configured: bool
    updated_at_utc: str | None = None


class AISettingsUpdateRequest(BaseModel):
    provider: str = Field(..., description="Provider preset identifier")
    base_url: str = Field(..., description="OpenAI-compatible base URL")
    api_key: str | None = Field(
        None,
        description="Plaintext API key, the display mask, or empty to keep the saved key",
    )
    model: str = Field(..., description="Model identifier")


class AITestConnectionRequest(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class AITestConnectionResponse(BaseModel):
    success: bool
    latency_ms: int | None = None
    model: str | None = None
    error: str | None = None


def _store(request: Request) -> AISettingsStore:
    return AISettingsStore(request.app.state.engine, clock=request.app.state.clock)


def _to_response(saved: AISettings) -> AISettingsResponse:
    return AISettingsResponse(
        provider=saved.provider,
        base_url=saved.base_url,
        api_key_masked=mask_api_key(saved.api_key),
        model=saved.model,
        is_configured=saved.is_configured,
        updated_at_utc=None if saved.updated_at is None else format_utc(saved.updated_at),
    )


def _unconfigured_response() -> AISettingsResponse:
    """The shape returned before the user has saved anything."""

    return AISettingsResponse(
        provider=DEFAULT_AI_PROVIDER,
        base_url="",
        api_key_masked=None,
        model=DEFAULT_AI_MODEL,
        is_configured=False,
        updated_at_utc=None,
    )


@router.get("/settings", response_model=AISettingsResponse)
def get_ai_settings(request: Request, response: Response) -> AISettingsResponse:
    response.headers["Cache-Control"] = "no-store"
    saved = _store(request).load()
    if saved is None:
        return _unconfigured_response()
    return _to_response(saved)


@router.put("/settings", response_model=AISettingsResponse)
def put_ai_settings(
    payload: AISettingsUpdateRequest,
    request: Request,
    response: Response,
) -> AISettingsResponse:
    """Persist the active endpoint, keeping the stored key when it is unchanged."""

    store = _store(request)
    try:
        provider = validate_provider(payload.provider)
        base_url = validate_base_url(payload.base_url)
        model = validate_model(payload.model)
    except AIServiceError as error:
        raise ApiError(422, error.code, error.message) from None

    existing = store.load()
    api_key = resolve_api_key_update(
        payload.api_key,
        None if existing is None else existing.api_key,
    )
    saved = store.save(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        is_configured=compute_is_configured(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return _to_response(saved)


@router.post("/test-connection", response_model=AITestConnectionResponse)
async def test_connection(
    request: Request,
    response: Response,
    payload: AITestConnectionRequest | None = None,
) -> AITestConnectionResponse:
    """Ping the submitted endpoint, falling back to the saved settings.

    A failed probe is a normal answer, not a server error: the UI needs the
    actionable message, so this always answers ``200`` with ``success: false``.
    """

    response.headers["Cache-Control"] = "no-store"
    saved = _store(request).load()
    submitted = payload or AITestConnectionRequest()

    base_url = _first_non_blank(submitted.base_url, None if saved is None else saved.base_url)
    model = _first_non_blank(submitted.model, None if saved is None else saved.model)
    api_key = resolve_api_key_update(
        submitted.api_key,
        None if saved is None else saved.api_key,
    )

    if not base_url or not model:
        return AITestConnectionResponse(
            success=False,
            error="Save a Base URL and a model name before testing the connection.",
        )

    result = await test_llm_connection(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=request.app.state.settings.ai_timeout_seconds,
    )
    return AITestConnectionResponse(
        success=result.success,
        latency_ms=result.latency_ms,
        model=result.model,
        error=result.error,
    )


def _first_non_blank(*values: str | None) -> str:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return ""
