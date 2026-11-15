"""Local opportunity feed and review-queue API.

Only the local staging projection is exposed. Configuration snapshots, board
tokens, credentials, and raw internal errors are never returned.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from personal_job_finder_api.api.security import validate_local_request
from personal_job_finder_api.search.feed import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    FeedPage,
    OpportunityFeedService,
)

router = APIRouter(
    tags=["opportunities"],
    dependencies=[Depends(validate_local_request)],
)

FeedName = Literal["main", "review"]


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


def get_opportunity_feed_service(request: Request) -> OpportunityFeedService:
    return OpportunityFeedService(request.app.state.engine)


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
