"""Read-only search-policy endpoint."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from personal_job_finder_api.domain.search_policy import (
    JAKARTA_TIMEZONE,
    SearchPolicyDecision,
)
from personal_job_finder_api.search.service import SearchPolicyService

router = APIRouter(tags=["search-policy"])


class SearchPolicyResponse(BaseModel):
    timezone: str
    operational_day: date
    max_runs: int
    accepted_runs: int
    remaining_runs: int
    last_accepted_at: datetime | None
    cooldown_until: datetime | None
    cooldown_remaining_seconds: int
    next_eligible_at: datetime | None
    next_reset_at: datetime
    has_active_run: bool
    can_start: bool
    block_reason: str | None
    blocking_reasons: list[str]

    @classmethod
    def from_decision(cls, decision: SearchPolicyDecision) -> SearchPolicyResponse:
        reasons = [reason.value for reason in decision.blocking_reasons]
        return cls(
            timezone=JAKARTA_TIMEZONE,
            operational_day=decision.operational_day,
            max_runs=decision.max_runs,
            accepted_runs=decision.accepted_runs,
            remaining_runs=decision.remaining_runs,
            last_accepted_at=decision.last_accepted_at,
            cooldown_until=decision.cooldown_until,
            cooldown_remaining_seconds=decision.cooldown_remaining_seconds,
            next_eligible_at=decision.next_eligible_at,
            next_reset_at=decision.next_reset_at,
            has_active_run=decision.has_active_run,
            can_start=decision.allowed,
            block_reason=reasons[0] if reasons else None,
            blocking_reasons=reasons,
        )


def get_search_policy_service(request: Request) -> SearchPolicyService:
    return SearchPolicyService(
        request.app.state.engine,
        connector_factory=request.app.state.connector_factory,
        clock=request.app.state.clock,
    )


@router.get("/search-policy", response_model=SearchPolicyResponse)
def get_search_policy(
    response: Response,
    service: Annotated[SearchPolicyService, Depends(get_search_policy_service)],
) -> SearchPolicyResponse:
    """Return the current quota, cooldown, and configuration eligibility."""

    response.headers["Cache-Control"] = "no-store"
    return SearchPolicyResponse.from_decision(service.evaluate())
