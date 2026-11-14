"""Manual search-run start and status API."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.core.time import ensure_aware_utc
from personal_job_finder_api.domain.search_policy import (
    BlockingReason,
    SearchPolicyDecision,
)
from personal_job_finder_api.search.reservation import (
    IdempotencyKeyConflict,
    InvalidIdempotencyKey,
    ReservationOutcome,
    ReservationRejected,
)
from personal_job_finder_api.search.run_service import SearchRunService

router = APIRouter(
    tags=["search-runs"],
    dependencies=[Depends(validate_local_request)],
)

_CONFIGURATION_REASONS = frozenset(
    {
        BlockingReason.NO_TARGET_ROLES,
        BlockingReason.TOO_MANY_TARGET_ROLES,
        BlockingReason.NO_USABLE_SOURCE,
    }
)


class SearchRunStartRequest(BaseModel):
    """The start contract deliberately has no role or location overrides."""

    model_config = ConfigDict(extra="forbid")


class SearchRunAcceptedResponse(BaseModel):
    run_id: str
    status: str
    accepted_at_utc: datetime
    operational_day: date
    remaining_runs: int
    status_url: str


class SearchRunStatusResponse(BaseModel):
    run_id: str
    status: str
    accepted_at_utc: datetime
    started_at_utc: datetime | None
    finished_at_utc: datetime | None
    operational_day: date
    progress: dict[str, Any] | None
    source_results: list[dict[str, Any]]
    error_summary: str | None


def get_search_run_service(request: Request) -> SearchRunService:
    return SearchRunService(
        request.app.state.engine,
        connector_factory=request.app.state.connector_factory,
        clock=request.app.state.clock,
    )


@router.post(
    "/search-runs",
    response_model=SearchRunAcceptedResponse,
    status_code=202,
)
def start_search_run(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    body: SearchRunStartRequest | None = None,
) -> SearchRunAcceptedResponse:
    """Reserve one manual run and hand execution to in-process orchestration."""

    if idempotency_key is None or not idempotency_key.strip():
        raise ApiError(
            400,
            "missing_idempotency_key",
            "The Idempotency-Key header is required.",
        )

    payload = body.model_dump() if body is not None else {}

    try:
        outcome = service.start(idempotency_key=idempotency_key, payload=payload)
    except InvalidIdempotencyKey as exc:
        raise ApiError(
            400,
            "invalid_idempotency_key",
            "The Idempotency-Key header must contain a UUID.",
        ) from exc
    except IdempotencyKeyConflict as exc:
        raise ApiError(
            409,
            "idempotency_key_reuse",
            "The Idempotency-Key was already used with a different request.",
            {"existing_run_id": exc.existing_run_id},
        ) from exc
    except ReservationRejected as exc:
        _raise_policy_error(exc.decision, request)

    if not outcome.replayed:
        background_tasks.add_task(service.execute, outcome.run.run_id)

    response.headers["Cache-Control"] = "no-store"
    return _accepted_response(outcome, request)


@router.get(
    "/search-runs/{run_id}",
    response_model=SearchRunStatusResponse,
)
def get_search_run(
    run_id: str,
    response: Response,
    service: Annotated[SearchRunService, Depends(get_search_run_service)],
) -> SearchRunStatusResponse:
    """Return one run's lifecycle and sanitized source summary."""

    run = service.get_run(run_id)
    if run is None:
        raise ApiError(404, "search_run_not_found", "Search run was not found.")

    response.headers["Cache-Control"] = "no-store"
    return SearchRunStatusResponse(
        run_id=run.run_id,
        status=run.status.value,
        accepted_at_utc=run.accepted_at,
        started_at_utc=run.started_at,
        finished_at_utc=run.finished_at,
        operational_day=run.operational_day,
        progress=_load_json_object(run.progress_json),
        source_results=_load_json_list(run.source_summary_json),
        error_summary=run.error_summary,
    )


def _accepted_response(
    outcome: ReservationOutcome,
    request: Request,
) -> SearchRunAcceptedResponse:
    prefix = request.app.state.settings.api_v1_prefix
    return SearchRunAcceptedResponse(
        run_id=outcome.run.run_id,
        status=outcome.run.status.value,
        accepted_at_utc=outcome.run.accepted_at,
        operational_day=outcome.run.operational_day,
        remaining_runs=outcome.remaining_runs,
        status_url=f"{prefix}/search-runs/{outcome.run.run_id}",
    )


def _raise_policy_error(decision: SearchPolicyDecision, request: Request) -> NoReturn:
    reasons = [reason.value for reason in decision.blocking_reasons]
    details: dict[str, Any] = {"blocking_reasons": reasons}

    if _CONFIGURATION_REASONS.intersection(decision.blocking_reasons):
        raise ApiError(
            422,
            "search_configuration_invalid",
            "Search configuration is not eligible to start.",
            details,
        )

    if BlockingReason.ACTIVE_RUN_EXISTS in decision.blocking_reasons:
        raise ApiError(
            409,
            "search_already_running",
            "Another search run is already active.",
            details,
        )

    now = ensure_aware_utc(request.app.state.clock(), "clock")

    if BlockingReason.QUOTA_EXHAUSTED in decision.blocking_reasons:
        details["next_reset_at"] = _iso(decision.next_reset_at)
        if decision.next_eligible_at is not None:
            details["next_eligible_at"] = _iso(decision.next_eligible_at)
        retry_after = max(0, math.ceil((decision.next_reset_at - now).total_seconds()))
        raise ApiError(
            429,
            "daily_quota_exhausted",
            "The daily search quota is exhausted.",
            details,
            headers={"Retry-After": str(retry_after)},
        )

    if BlockingReason.COOLDOWN_ACTIVE in decision.blocking_reasons:
        if decision.next_eligible_at is not None:
            details["next_eligible_at"] = _iso(decision.next_eligible_at)
        details["cooldown_remaining_seconds"] = decision.cooldown_remaining_seconds
        raise ApiError(
            429,
            "search_cooldown_active",
            "A search run is not currently eligible.",
            details,
            headers={"Retry-After": str(decision.cooldown_remaining_seconds)},
        )

    raise ApiError(
        422,
        "search_configuration_invalid",
        "Search configuration is not eligible to start.",
        details,
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_json_object(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _load_json_list(value: str | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]
