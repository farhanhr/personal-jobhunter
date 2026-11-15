"""Company source registry API.

Registration is local-only, and approval is an explicit user action. No endpoint
accepts a base URL or any other free-form connector address.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.domain.companies import CompanyValidationError
from personal_job_finder_api.settings.company_sources import (
    CompanySource,
    CompanySourceNotFound,
    CompanySourceService,
    DuplicateCompanySource,
)

router = APIRouter(
    prefix="/companies",
    tags=["companies"],
    dependencies=[Depends(validate_local_request)],
)

PolicyStatus = Literal["unreviewed", "permitted", "blocked"]


class CompanyResponse(BaseModel):
    company_id: str
    company_name: str
    ats_type: str
    board_token: str
    enabled: bool
    policy_status: str
    can_fetch: bool

    @classmethod
    def from_source(cls, source: CompanySource) -> CompanyResponse:
        return cls(
            company_id=source.company_id,
            company_name=source.company_name,
            ats_type=source.ats_type,
            board_token=source.board_token,
            enabled=source.enabled,
            policy_status=source.policy_status,
            can_fetch=source.can_fetch,
        )


class CompanyListResponse(BaseModel):
    companies: list[CompanyResponse]


class CompanyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str
    board_token: str


class CompanyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_status: PolicyStatus | None = None
    enabled: bool | None = None


def get_company_source_service(request: Request) -> CompanySourceService:
    return CompanySourceService(
        request.app.state.engine,
        clock=request.app.state.clock,
    )


@router.get("", response_model=CompanyListResponse)
def list_companies(
    response: Response,
    service: Annotated[CompanySourceService, Depends(get_company_source_service)],
) -> CompanyListResponse:
    """Return every registered company source with its review state."""

    response.headers["Cache-Control"] = "no-store"
    return CompanyListResponse(
        companies=[CompanyResponse.from_source(source) for source in service.list_companies()]
    )


@router.post("", response_model=CompanyResponse, status_code=201)
def create_company(
    payload: CompanyCreateRequest,
    response: Response,
    service: Annotated[CompanySourceService, Depends(get_company_source_service)],
) -> CompanyResponse:
    """Register a Greenhouse board. The new source is never auto-approved."""

    try:
        created = service.create_company(
            company_name=payload.company_name,
            board_token=payload.board_token,
        )
    except CompanyValidationError as exc:
        raise ApiError(422, exc.code, exc.message, exc.details) from exc
    except DuplicateCompanySource as exc:
        raise ApiError(409, exc.code, exc.message, exc.details) from exc

    response.headers["Cache-Control"] = "no-store"
    return CompanyResponse.from_source(created)


@router.patch("/{company_id}", response_model=CompanyResponse)
def update_company(
    company_id: str,
    payload: CompanyUpdateRequest,
    response: Response,
    service: Annotated[CompanySourceService, Depends(get_company_source_service)],
) -> CompanyResponse:
    """Approve, block, or enable/disable one registered company source."""

    try:
        updated = service.update_company(
            company_id,
            policy_status=payload.policy_status,
            enabled=payload.enabled,
        )
    except CompanyValidationError as exc:
        raise ApiError(422, exc.code, exc.message, exc.details) from exc
    except CompanySourceNotFound as exc:
        raise ApiError(
            404,
            "company_source_not_found",
            "Company source was not found.",
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    return CompanyResponse.from_source(updated)
