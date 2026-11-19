"""Read model for the local opportunity feed and review queue.

Google Sheets remains the user-facing record store; this service only reads the
local staging projection so the dashboard can render without network calls.

Application-tracker values are resolved separately, from the Read Model plus any
pending user-owned outbox edits (see ``search.tracker_read``). They are never
taken from the staged discovery payload, which is a copy made at ingestion time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine, func, select

from personal_job_finder_api.db.tables import (
    job_sources,
    opportunities,
    opportunity_run_refs,
)
from personal_job_finder_api.search.tracker_read import (
    TrackerView,
    load_tracker_views,
)

DESCRIPTION_EXCERPT_CHARS = 500
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source_type: str
    external_source_id: str
    source_url: str | None
    source_url_is_safe: bool
    captured_at_utc: str
    provenance_note: str | None
    run_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OpportunitySummary:
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
    tracker: TrackerView
    sources: tuple[SourceSummary, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class OpportunityDetail:
    """One opportunity with its full description, provenance, and tracker state."""

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
    tracker: TrackerView
    sources: tuple[SourceSummary, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class FeedPage:
    items: tuple[OpportunitySummary, ...]
    page: int
    page_size: int
    total_items: int

    @property
    def total_pages(self) -> int:
        if self.total_items == 0:
            return 0
        return (self.total_items + self.page_size - 1) // self.page_size


class OpportunityFeedService:
    """Query the staged projection with stable ordering and bounded fields."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_feed(self, *, feed: str, page: int, page_size: int) -> FeedPage:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

        offset = (page - 1) * page_size

        with self._engine.connect() as connection:
            total_items = connection.execute(
                select(func.count())
                .select_from(opportunities)
                .where(opportunities.c.feed_bucket == feed)
            ).scalar_one()

            rows = (
                connection.execute(
                    select(opportunities)
                    .where(opportunities.c.feed_bucket == feed)
                    .order_by(
                        # First-seen is immutable, so paging stays stable while
                        # new runs re-observe existing records.
                        opportunities.c.first_seen_at_utc.desc(),
                        opportunities.c.opportunity_id.asc(),
                    )
                    .limit(page_size)
                    .offset(offset)
                )
                .mappings()
                .all()
            )

            opportunity_ids = [row["opportunity_id"] for row in rows]
            sources_by_opportunity = self._load_sources(connection, opportunity_ids)
            trackers_by_id = load_tracker_views(connection, opportunity_ids)

        items = tuple(
            _to_summary(
                row,
                sources_by_opportunity.get(row["opportunity_id"], ()),
                trackers_by_id[row["opportunity_id"]],
            )
            for row in rows
        )
        return FeedPage(
            items=items,
            page=page,
            page_size=page_size,
            total_items=int(total_items),
        )

    def get_detail(self, opportunity_id: str) -> OpportunityDetail | None:
        """Return one opportunity with its full description, or ``None``.

        The staged projection is the source of discovery metadata because it is
        what the feed lists. Tracker values come from the Read Model plus pending
        outbox edits, so a user edit that has not synced is still visible here.
        No network call is made.
        """

        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(opportunities).where(opportunities.c.opportunity_id == opportunity_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None

            sources = self._load_sources(connection, [opportunity_id]).get(opportunity_id, ())
            tracker = load_tracker_views(connection, [opportunity_id])[opportunity_id]

        return _to_detail(row, sources, tracker)

    def _load_sources(
        self,
        connection,
        opportunity_ids: list[str],
    ) -> dict[str, tuple[SourceSummary, ...]]:
        if not opportunity_ids:
            return {}

        source_rows = (
            connection.execute(
                select(job_sources)
                .where(job_sources.c.opportunity_id.in_(opportunity_ids))
                .order_by(job_sources.c.source_type, job_sources.c.source_id)
            )
            .mappings()
            .all()
        )

        run_rows = (
            connection.execute(
                select(
                    opportunity_run_refs.c.source_id,
                    opportunity_run_refs.c.run_id,
                )
                .where(opportunity_run_refs.c.opportunity_id.in_(opportunity_ids))
                .order_by(
                    opportunity_run_refs.c.first_seen_at_utc,
                    opportunity_run_refs.c.run_id,
                )
            )
            .mappings()
            .all()
        )
        runs_by_source: dict[str, list[str]] = {}
        for row in run_rows:
            runs_by_source.setdefault(row["source_id"], []).append(row["run_id"])

        grouped: dict[str, list[SourceSummary]] = {}
        for row in source_rows:
            safe_url = row["safe_source_url"]
            summary = SourceSummary(
                source_type=row["source_type"],
                external_source_id=row["external_source_id"],
                source_url=safe_url,
                source_url_is_safe=safe_url is not None,
                captured_at_utc=row["captured_at_utc"],
                provenance_note=row["provenance_note"],
                run_ids=tuple(runs_by_source.get(row["source_id"], ())),
            )
            grouped.setdefault(row["opportunity_id"], []).append(summary)

        return {key: tuple(value) for key, value in grouped.items()}


def _to_summary(
    row,
    sources: tuple[SourceSummary, ...],
    tracker: TrackerView,
) -> OpportunitySummary:
    description = row["description"]
    excerpt = None
    has_more = False
    if description is not None:
        excerpt = description[:DESCRIPTION_EXCERPT_CHARS]
        has_more = len(description) > DESCRIPTION_EXCERPT_CHARS

    return OpportunitySummary(
        opportunity_id=row["opportunity_id"],
        company_id=row["company_id"],
        company_name=row["company_name"],
        job_title=row["job_title"],
        target_role_id=row["target_role_id"],
        role_match_type=row["role_match_type"],
        role_match_reason=row["role_match_reason"],
        location_raw=row["location_raw"],
        country=row["country"],
        province=row["province"],
        city=row["city"],
        location_status=row["location_status"],
        work_mode=row["work_mode"],
        remote_scope=row["remote_scope"],
        posted_at_utc=row["posted_at_utc"],
        first_seen_at_utc=row["first_seen_at_utc"],
        last_seen_at_utc=row["last_seen_at_utc"],
        availability_status=row["availability_status"],
        feed_bucket=row["feed_bucket"],
        description_excerpt=excerpt,
        has_more_description=has_more,
        tracker=tracker,
        sources=sources,
    )


def _to_detail(
    row,
    sources: tuple[SourceSummary, ...],
    tracker: TrackerView,
) -> OpportunityDetail:
    return OpportunityDetail(
        opportunity_id=row["opportunity_id"],
        company_id=row["company_id"],
        company_name=row["company_name"],
        job_title=row["job_title"],
        target_role_id=row["target_role_id"],
        role_match_type=row["role_match_type"],
        role_match_reason=row["role_match_reason"],
        location_raw=row["location_raw"],
        country=row["country"],
        province=row["province"],
        city=row["city"],
        location_status=row["location_status"],
        location_reason=row["location_reason"],
        work_mode=row["work_mode"],
        remote_scope=row["remote_scope"],
        posted_at_utc=row["posted_at_utc"],
        source_updated_at_utc=row["source_updated_at_utc"],
        first_seen_at_utc=row["first_seen_at_utc"],
        last_seen_at_utc=row["last_seen_at_utc"],
        availability_status=row["availability_status"],
        feed_bucket=row["feed_bucket"],
        description=row["description"],
        tracker=tracker,
        sources=sources,
    )
