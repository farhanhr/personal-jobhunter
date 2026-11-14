"""Target-role settings API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.domain.target_roles import (
    MAX_TARGET_ROLES,
    TargetRoleDraft,
    TargetRoleValidationError,
)
from personal_job_finder_api.settings.service import TargetRole, TargetRoleService

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(validate_local_request)],
)


class TargetRoleResponse(BaseModel):
    role_id: str
    canonical_name: str
    aliases: list[str]


class SearchSettingsResponse(BaseModel):
    target_roles: list[TargetRoleResponse]
    max_target_roles: int = MAX_TARGET_ROLES


class TargetRoleUpdate(BaseModel):
    role_id: str | None = None
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)


class SearchSettingsUpdate(BaseModel):
    target_roles: list[TargetRoleUpdate]


def get_target_role_service(request: Request) -> TargetRoleService:
    return TargetRoleService(
        request.app.state.engine,
        clock=request.app.state.clock,
    )


@router.get("/search", response_model=SearchSettingsResponse)
def get_search_settings(
    response: Response,
    service: Annotated[TargetRoleService, Depends(get_target_role_service)],
) -> SearchSettingsResponse:
    """Return the persisted target-role configuration."""

    response.headers["Cache-Control"] = "no-store"
    return _response(service.list_roles())


@router.put("/search", response_model=SearchSettingsResponse)
def put_search_settings(
    payload: SearchSettingsUpdate,
    response: Response,
    service: Annotated[TargetRoleService, Depends(get_target_role_service)],
) -> SearchSettingsResponse:
    """Replace all target roles atomically after validation."""

    drafts = tuple(
        TargetRoleDraft(
            role_id=role.role_id,
            canonical_name=role.canonical_name,
            aliases=tuple(role.aliases),
        )
        for role in payload.target_roles
    )
    try:
        saved = service.replace_roles(drafts)
    except TargetRoleValidationError as exc:
        raise ApiError(422, exc.code, exc.message, exc.details) from exc

    response.headers["Cache-Control"] = "no-store"
    return _response(saved)


def _response(roles: list[TargetRole]) -> SearchSettingsResponse:
    return SearchSettingsResponse(
        target_roles=[
            TargetRoleResponse(
                role_id=role.role_id,
                canonical_name=role.canonical_name,
                aliases=list(role.aliases),
            )
            for role in roles
        ]
    )
