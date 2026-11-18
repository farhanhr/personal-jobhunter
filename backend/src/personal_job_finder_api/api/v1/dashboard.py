"""Dashboard summary API.

The endpoint answers from the local read model only. The browser receives the
Jakarta calendar day and the exact inclusive window boundaries, so the frontend
never computes "today" or a window on its own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.dashboard.summary import (
    DashboardSummary,
    DashboardSummaryService,
)

router = APIRouter(
    tags=["dashboard"],
    dependencies=[Depends(validate_local_request)],
)


class DateWindowResponse(BaseModel):
    """An inclusive calendar-date window in ISO-8601."""

    start: str
    end: str


class InvalidTrackerDateResponse(BaseModel):
    """One non-empty tracker date value that could not be read."""

    opportunity_id: str
    field: str
    raw_value: str
    reason: str


class RoleGroupResponse(BaseModel):
    """Opportunities grouped by target role."""

    role_id: str | None
    label: str
    count: int


class SourceGroupResponse(BaseModel):
    """Opportunities grouped by source type."""

    source_type: str
    count: int


class LocationGroupResponse(BaseModel):
    """Opportunities grouped by the ``(province, city)`` pair."""

    province: str
    city: str
    count: int


class DashboardDataQualityResponse(BaseModel):
    """Data-quality information for tracker date values."""

    invalid_date_value_count: int
    invalid_date_values: list[InvalidTrackerDateResponse]
    invalid_date_values_truncated: bool


class DashboardSummaryResponse(BaseModel):
    """The dashboard metrics with their exact window boundaries."""

    as_of_date_jkt: str
    total_opportunities: int
    excluded_opportunities: int
    new_opportunities: int
    new_opportunities_window: DateWindowResponse
    applications_window: DateWindowResponse
    applications_last_7_days: int
    interviews_window: DateWindowResponse
    upcoming_interviews: int
    opportunities_by_target_role: list[RoleGroupResponse]
    opportunities_by_source: list[SourceGroupResponse]
    opportunities_by_location: list[LocationGroupResponse]
    latest_read_model_refresh_at_utc: str | None
    data_quality: DashboardDataQualityResponse


def get_dashboard_service(request: Request) -> DashboardSummaryService:
    return DashboardSummaryService(
        request.app.state.engine,
        clock=request.app.state.clock,
    )


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    response: Response,
    service: Annotated[DashboardSummaryService, Depends(get_dashboard_service)],
) -> DashboardSummaryResponse:
    """Return applications in the last 7 calendar days and upcoming interviews."""

    summary = service.summary()
    response.headers["Cache-Control"] = "no-store"
    return _to_response(summary)


def _to_response(summary: DashboardSummary) -> DashboardSummaryResponse:
    return DashboardSummaryResponse(
        as_of_date_jkt=summary.as_of_date_jkt.isoformat(),
        total_opportunities=summary.total_opportunities,
        excluded_opportunities=summary.excluded_opportunities,
        new_opportunities=summary.new_opportunities,
        new_opportunities_window=DateWindowResponse(
            start=summary.new_opportunities_window.start.isoformat(),
            end=summary.new_opportunities_window.end.isoformat(),
        ),
        applications_window=DateWindowResponse(
            start=summary.applications_window.start.isoformat(),
            end=summary.applications_window.end.isoformat(),
        ),
        applications_last_7_days=summary.applications_last_7_days,
        interviews_window=DateWindowResponse(
            start=summary.interviews_window.start.isoformat(),
            end=summary.interviews_window.end.isoformat(),
        ),
        upcoming_interviews=summary.upcoming_interviews,
        opportunities_by_target_role=[
            RoleGroupResponse(
                role_id=group.role_id,
                label=group.label,
                count=group.count,
            )
            for group in summary.opportunities_by_target_role
        ],
        opportunities_by_source=[
            SourceGroupResponse(
                source_type=group.source_type,
                count=group.count,
            )
            for group in summary.opportunities_by_source
        ],
        opportunities_by_location=[
            LocationGroupResponse(
                province=group.province,
                city=group.city,
                count=group.count,
            )
            for group in summary.opportunities_by_location
        ],
        latest_read_model_refresh_at_utc=summary.latest_read_model_refresh_at_utc,
        data_quality=DashboardDataQualityResponse(
            invalid_date_value_count=summary.invalid_value_count,
            invalid_date_values=[
                InvalidTrackerDateResponse(
                    opportunity_id=entry.opportunity_id,
                    field=entry.field,
                    raw_value=entry.raw_value,
                    reason=entry.reason,
                )
                for entry in summary.invalid_values
            ],
            invalid_date_values_truncated=summary.invalid_values_truncated,
        ),
    )
