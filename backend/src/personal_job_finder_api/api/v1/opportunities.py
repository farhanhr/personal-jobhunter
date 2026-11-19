"""Local opportunity feed and review-queue API.

Only the local staging projection is exposed. Configuration snapshots, board
tokens, credentials, and raw internal errors are never returned.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_job_finder_api.api.errors import ApiError
from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.search.feed import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    FeedPage,
    OpportunityFeedService,
)
from personal_job_finder_api.search.tracker import (
    TRACKER_FIELDS,
    OpportunityTrackerService,
    TrackerNotFound,
    TrackerStateInconsistent,
)

router = APIRouter(
    tags=["opportunities"],
    dependencies=[Depends(validate_local_request)],
)

FeedName = Literal["main", "review"]

# The contract fixes this at 10,000 characters; a longer note is rejected rather
# than silently truncated, so the user's text is never altered.
NOTES_MAX_LENGTH = 10_000


class SourceSummaryResponse(BaseModel):
    source_type: str
    external_source_id: str
    source_url: str | None
    source_url_is_safe: bool
    captured_at_utc: str
    provenance_note: str | None
    run_ids: list[str]


class TrackerSummaryResponse(BaseModel):
    """The tracker fields a feed card needs. ``notes`` stays on the detail view."""

    application_status: str | None
    date_applied: str | None
    follow_up_date: str | None
    interview_date: str | None
    sync_status: str
    known_fields: list[str]


class OpportunityResponse(BaseModel):
    opportunity_id: str
    company_id: str
    company_name: str
    job_title: str
    target_role_id: str | None
    role_match_type: str | None
    role_match_reason: str | None
    location_raw: str | None
    country: str | None
    province: str | None
    city: str | None
    location_status: str
    work_mode: str
    remote_scope: str
    posted_at_utc: str | None
    first_seen_at_utc: str
    last_seen_at_utc: str
    availability_status: str
    feed_bucket: str
    description_excerpt: str | None
    has_more_description: bool
    tracker_summary: TrackerSummaryResponse
    sources: list[SourceSummaryResponse]


class OpportunityDetailResponse(BaseModel):
    """One opportunity: discovery metadata, full description, provenance, tracker."""

    opportunity_id: str
    company_id: str
    company_name: str
    job_title: str
    target_role_id: str | None
    role_match_type: str | None
    role_match_reason: str | None
    location_raw: str | None
    country: str | None
    province: str | None
    city: str | None
    location_status: str
    location_reason: str | None
    work_mode: str
    remote_scope: str
    posted_at_utc: str | None
    source_updated_at_utc: str | None
    first_seen_at_utc: str
    last_seen_at_utc: str
    availability_status: str
    feed_bucket: str
    description: str | None
    job_type: str | None
    date_applied: str | None
    application_status: str | None
    follow_up_date: str | None
    interview_date: str | None
    vacancy_url: str | None
    notes: str | None
    sync_status: str
    known_fields: list[str]
    sources: list[SourceSummaryResponse]


class OpportunityFeedResponse(BaseModel):
    items: list[OpportunityResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class TrackerPatchRequest(BaseModel):
    """One tracker patch; only the seven user-owned fields are accepted.

    ``extra="forbid"`` is what keeps ``user_status`` and every discovery-owned
    column out of this endpoint. A field that is omitted stays unchanged, while an
    explicit ``null`` or an empty string clears it, so the request model relies on
    ``model_fields_set`` rather than the parsed value.
    """

    model_config = ConfigDict(extra="forbid")

    job_type: str | None = None
    date_applied: str | None = None
    application_status: str | None = None
    follow_up_date: str | None = None
    interview_date: str | None = None
    vacancy_url: str | None = None
    notes: str | None = Field(default=None, max_length=NOTES_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> TrackerPatchRequest:
        if not self.model_fields_set:
            raise ValueError("Supply at least one tracker field to update.")
        return self

    def supplied(self) -> dict[str, str | None]:
        """Return only the fields the client actually sent."""

        return {
            field: getattr(self, field)
            for field in TRACKER_FIELDS
            if field in self.model_fields_set
        }


class TrackerResponse(BaseModel):
    opportunity_id: str
    job_type: str | None
    date_applied: str | None
    application_status: str | None
    follow_up_date: str | None
    interview_date: str | None
    vacancy_url: str | None
    notes: str | None
    sync_status: str
    known_fields: list[str]


def get_opportunity_feed_service(request: Request) -> OpportunityFeedService:
    return OpportunityFeedService(request.app.state.engine)


def get_tracker_service(request: Request) -> OpportunityTrackerService:
    return OpportunityTrackerService(
        request.app.state.engine,
        clock=request.app.state.clock,
    )


@router.get("/jobs", response_model=OpportunityFeedResponse)
def list_opportunities(
    response: Response,
    service: Annotated[OpportunityFeedService, Depends(get_opportunity_feed_service)],
    feed: FeedName = "main",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> OpportunityFeedResponse:
    """Return one page of the main feed or the review queue."""

    page_result = service.list_feed(feed=feed, page=page, page_size=page_size)
    response.headers["Cache-Control"] = "no-store"
    return _to_response(page_result)


@router.get("/jobs/{opportunity_id}", response_model=OpportunityDetailResponse)
def get_opportunity_detail(
    opportunity_id: str,
    response: Response,
    service: Annotated[OpportunityFeedService, Depends(get_opportunity_feed_service)],
) -> OpportunityDetailResponse:
    """Return one opportunity with its full description and tracker state.

    This reads local data only. It never contacts Google Sheets, and a tracker
    edit still queued in the outbox is reported as the effective value.
    """

    detail = service.get_detail(opportunity_id)
    if detail is None:
        raise ApiError(
            404,
            "opportunity_not_found",
            "The opportunity does not exist in local data.",
        )

    response.headers["Cache-Control"] = "no-store"
    return _to_detail_response(detail)


@router.patch(
    "/opportunities/{opportunity_id}/tracker",
    response_model=TrackerResponse,
    status_code=200,
)
def update_tracker(
    opportunity_id: str,
    patch: TrackerPatchRequest,
    response: Response,
    service: Annotated[OpportunityTrackerService, Depends(get_tracker_service)],
) -> TrackerResponse:
    """Queue a user-owned tracker edit; synchronization stays manual.

    No Google request happens here. The edit becomes pending outbox work that the
    existing dispatcher writes on the next explicit sync, and the read model is
    updated only by that settlement.
    """

    try:
        result = service.update(opportunity_id, patch.supplied())
    except TrackerNotFound:
        raise ApiError(
            404,
            "opportunity_not_found",
            "The opportunity does not exist in local data.",
        ) from None
    except TrackerStateInconsistent:
        raise ApiError(
            409,
            "tracker_state_inconsistent",
            "This opportunity has no canonical row yet. Synchronize it before "
            "editing tracker fields.",
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return TrackerResponse(
        opportunity_id=result.opportunity_id,
        sync_status=result.sync_status,
        known_fields=list(result.known_fields),
        **result.values,
    )


def _to_source_response(source) -> SourceSummaryResponse:
    return SourceSummaryResponse(
        source_type=source.source_type,
        external_source_id=source.external_source_id,
        source_url=source.source_url,
        source_url_is_safe=source.source_url_is_safe,
        captured_at_utc=source.captured_at_utc,
        provenance_note=source.provenance_note,
        run_ids=list(source.run_ids),
    )


def _to_tracker_summary(tracker) -> TrackerSummaryResponse:
    return TrackerSummaryResponse(
        application_status=tracker.values.get("application_status"),
        date_applied=tracker.values.get("date_applied"),
        follow_up_date=tracker.values.get("follow_up_date"),
        interview_date=tracker.values.get("interview_date"),
        sync_status=tracker.sync_status,
        known_fields=list(tracker.known_fields),
    )


def _to_response(page_result: FeedPage) -> OpportunityFeedResponse:
    return OpportunityFeedResponse(
        items=[
            OpportunityResponse(
                opportunity_id=item.opportunity_id,
                company_id=item.company_id,
                company_name=item.company_name,
                job_title=item.job_title,
                target_role_id=item.target_role_id,
                role_match_type=item.role_match_type,
                role_match_reason=item.role_match_reason,
                location_raw=item.location_raw,
                country=item.country,
                province=item.province,
                city=item.city,
                location_status=item.location_status,
                work_mode=item.work_mode,
                remote_scope=item.remote_scope,
                posted_at_utc=item.posted_at_utc,
                first_seen_at_utc=item.first_seen_at_utc,
                last_seen_at_utc=item.last_seen_at_utc,
                availability_status=item.availability_status,
                feed_bucket=item.feed_bucket,
                description_excerpt=item.description_excerpt,
                has_more_description=item.has_more_description,
                tracker_summary=_to_tracker_summary(item.tracker),
                sources=[_to_source_response(source) for source in item.sources],
            )
            for item in page_result.items
        ],
        page=page_result.page,
        page_size=page_result.page_size,
        total_items=page_result.total_items,
        total_pages=page_result.total_pages,
    )


def _to_detail_response(detail) -> OpportunityDetailResponse:
    return OpportunityDetailResponse(
        opportunity_id=detail.opportunity_id,
        company_id=detail.company_id,
        company_name=detail.company_name,
        job_title=detail.job_title,
        target_role_id=detail.target_role_id,
        role_match_type=detail.role_match_type,
        role_match_reason=detail.role_match_reason,
        location_raw=detail.location_raw,
        country=detail.country,
        province=detail.province,
        city=detail.city,
        location_status=detail.location_status,
        location_reason=detail.location_reason,
        work_mode=detail.work_mode,
        remote_scope=detail.remote_scope,
        posted_at_utc=detail.posted_at_utc,
        source_updated_at_utc=detail.source_updated_at_utc,
        first_seen_at_utc=detail.first_seen_at_utc,
        last_seen_at_utc=detail.last_seen_at_utc,
        availability_status=detail.availability_status,
        feed_bucket=detail.feed_bucket,
        description=detail.description,
        job_type=detail.tracker.values.get("job_type"),
        date_applied=detail.tracker.values.get("date_applied"),
        application_status=detail.tracker.values.get("application_status"),
        follow_up_date=detail.tracker.values.get("follow_up_date"),
        interview_date=detail.tracker.values.get("interview_date"),
        vacancy_url=detail.tracker.values.get("vacancy_url"),
        notes=detail.tracker.values.get("notes"),
        sync_status=detail.tracker.sync_status,
        known_fields=list(detail.tracker.known_fields),
        sources=[_to_source_response(source) for source in detail.sources],
    )
