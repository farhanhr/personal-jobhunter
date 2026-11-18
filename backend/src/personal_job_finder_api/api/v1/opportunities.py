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
        **result.values,
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
                sources=[
                    SourceSummaryResponse(
                        source_type=source.source_type,
                        external_source_id=source.external_source_id,
                        source_url=source.source_url,
                        source_url_is_safe=source.source_url_is_safe,
                        captured_at_utc=source.captured_at_utc,
                        provenance_note=source.provenance_note,
                        run_ids=list(source.run_ids),
                    )
                    for source in item.sources
                ],
            )
            for item in page_result.items
        ],
        page=page_result.page,
        page_size=page_result.page_size,
        total_items=page_result.total_items,
        total_pages=page_result.total_pages,
    )
