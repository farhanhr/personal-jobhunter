"""Durable staging of connector results.

Connector output lives only in memory, so a run is not complete until its records
and their pending-sync outbox entries are committed. Staging happens in one SQLite
transaction: either every record and outbox row lands, or none does.

One staged opportunity represents exactly one source record. Merging two source
records into one opportunity would need the deterministic high-confidence
fingerprint (company, title, location, description hash) described in the P0
reconciliation notes, and near matches belong in review rather than an automatic
merge. Until a second source type exists there is nothing to merge, so records
are never combined on title similarity alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.engine import Connection

from personal_job_finder_api.core.time import ensure_aware_utc, format_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.domain.opportunities import (
    OpportunityRecord,
    is_main_feed,
    is_review_candidate,
)
from personal_job_finder_api.domain.opportunity_identity import (
    opportunity_id_for,
    source_id_for,
)
from personal_job_finder_api.domain.source_urls import validate_source_url
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_DESTINATION,
    OUTBOX_ENTITY_TYPE,
    OUTBOX_ENTITY_TYPE_SOURCE,
    OUTBOX_OPERATION,
    OUTBOX_OPERATION_WITHDRAW,
    OpportunityStagingRepository,
    OutboxEntry,
    StagedOpportunity,
    StagedSource,
)

# Google Sheets caps a single cell at 50,000 characters.
SHEETS_CELL_MAX_CHARS = 50_000

FEED_MAIN = "main"
FEED_REVIEW = "review"
FEED_EXCLUDED = "excluded"

OBSERVATION_SEEN = "seen"

# The accepted contract defines these provenance methods. A source type without a
# mapping stays blank instead of claiming provenance the contract does not define.
PROVENANCE_BY_SOURCE_TYPE: dict[str, str] = {
    "greenhouse": "ats_api",
    "lever": "ats_api",
    "ashby": "ats_api",
    "permitted_career_page": "permitted_page",
    "linkedin_post_manual": "manual_user_input",
}


@dataclass(frozen=True, slots=True)
class StagingSummary:
    """Counters describing what one staging call persisted."""

    run_id: str
    records_received: int
    records_new: int
    records_updated: int
    records_unchanged: int
    outbox_enqueued: int
    pending_sync_count: int
    feed_main: int
    feed_review: int
    feed_excluded: int


class OpportunityStagingService:
    """Persist normalized opportunities and queue their sync work atomically."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = OpportunityStagingRepository()

    def stage(
        self,
        *,
        run_id: str,
        records: Sequence[OpportunityRecord],
    ) -> StagingSummary:
        """Stage every record for one run, or roll back the whole batch."""

        if not run_id or not run_id.strip():
            raise ValueError("run_id is required")

        staged_at = format_utc(ensure_aware_utc(self._clock(), "clock"))

        counters = {
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "outbox": 0,
            "withdrawn": 0,
            "preserved": 0,
            FEED_MAIN: 0,
            FEED_REVIEW: 0,
            FEED_EXCLUDED: 0,
        }

        with immediate_transaction(self._engine) as connection:
            for record in records:
                bucket = _feed_bucket(record)
                counters[bucket] += 1

                staged = _build_opportunity(record, bucket, staged_at)
                existing = self._repository.find_opportunity(
                    connection,
                    staged.opportunity_id,
                )
                first_seen_at_utc = (
                    existing["first_seen_at_utc"] if existing is not None else staged_at
                )

                if existing is None:
                    self._repository.insert_opportunity(connection, staged)
                    counters["new"] += 1
                elif existing["content_hash"] == staged.content_hash:
                    self._repository.touch_last_seen(
                        connection,
                        staged.opportunity_id,
                        last_seen_at_utc=staged_at,
                    )
                    counters["unchanged"] += 1
                else:
                    self._repository.update_opportunity(connection, staged)
                    counters["updated"] += 1

                source = _build_source(record, staged.opportunity_id, staged_at)
                self._repository.upsert_source(connection, source)
                self._repository.upsert_run_ref(
                    connection,
                    opportunity_id=staged.opportunity_id,
                    run_id=run_id,
                    source_id=source.source_id,
                    seen_at_utc=staged_at,
                )

                if bucket == FEED_EXCLUDED:
                    # Excluded records stay staged for audit but must not enter the
                    # user-facing feed. A projection that never reached the sheet is
                    # withdrawn outright. A projection that may already exist remotely
                    # is replaced by an explicit withdrawal marker, so the sheet does
                    # not keep a stale eligible row.
                    self._queue_withdrawal(
                        connection,
                        run_id=run_id,
                        staged=staged,
                        source=source,
                        record=record,
                        first_seen_at_utc=first_seen_at_utc,
                        staged_at=staged_at,
                        counters=counters,
                    )
                    continue

                payload_json = _payload_json(
                    record,
                    staged,
                    bucket,
                    first_seen_at_utc=first_seen_at_utc,
                )
                queued = self._repository.upsert_outbox(
                    connection,
                    outbox_id=_opportunity_outbox_id(source.source_id),
                    run_id=run_id,
                    entity_id=staged.opportunity_id,
                    payload_json=payload_json,
                    payload_hash=_payload_hash(payload_json),
                    now_utc=staged_at,
                )
                if queued:
                    counters["outbox"] += 1

                source_payload_json = _job_source_payload_json(record, source)
                queued_source = self._repository.upsert_outbox(
                    connection,
                    outbox_id=_source_outbox_id(source.source_id),
                    run_id=run_id,
                    entity_id=source.source_id,
                    payload_json=source_payload_json,
                    payload_hash=_payload_hash(source_payload_json),
                    now_utc=staged_at,
                    entity_type=OUTBOX_ENTITY_TYPE_SOURCE,
                )
                if queued_source:
                    counters["outbox"] += 1

            pending_sync_count = self._repository.count_unsynced_for_run(
                connection,
                run_id,
            )

        return StagingSummary(
            run_id=run_id,
            records_received=len(records),
            records_new=counters["new"],
            records_updated=counters["updated"],
            records_unchanged=counters["unchanged"],
            outbox_enqueued=counters["outbox"],
            pending_sync_count=pending_sync_count,
            feed_main=counters[FEED_MAIN],
            feed_review=counters[FEED_REVIEW],
            feed_excluded=counters[FEED_EXCLUDED],
        )

    def _queue_withdrawal(
        self,
        connection: Connection,
        *,
        run_id: str,
        staged: StagedOpportunity,
        source: StagedSource,
        record: OpportunityRecord,
        first_seen_at_utc: str,
        staged_at: str,
        counters: dict[str, int],
    ) -> None:
        """Retire projections for a record that is no longer in the feed.

        The accepted contract defines no delete or tombstone semantics, and
        deleting a row would destroy the user's own ``user_status`` value. A
        projection that certainly never left this machine is withdrawn locally; a
        projection that may already be in the spreadsheet is replaced by an
        explicit ``withdraw`` marker that the dispatcher applies non-destructively.

        "May have been delivered" includes a retried or blocked projection, because
        either can follow a write whose response was lost.
        """

        opportunity_delivered = self._repository.has_delivered_outbox(
            connection,
            entity_id=staged.opportunity_id,
        )
        source_delivered = self._repository.has_delivered_outbox(
            connection,
            entity_id=source.source_id,
            entity_type=OUTBOX_ENTITY_TYPE_SOURCE,
        )

        # Discard first, then re-queue the withdrawal markers. Discarding only
        # removes work that provably never left this machine.
        counters["withdrawn"] += self._repository.discard_unsent_outbox(
            connection,
            entity_id=staged.opportunity_id,
        )
        counters["withdrawn"] += self._repository.discard_unsent_outbox(
            connection,
            entity_id=source.source_id,
            entity_type=OUTBOX_ENTITY_TYPE_SOURCE,
        )

        if opportunity_delivered:
            payload_json = _payload_json(
                record,
                staged,
                FEED_EXCLUDED,
                first_seen_at_utc=first_seen_at_utc,
                operation=OUTBOX_OPERATION_WITHDRAW,
            )
            if self._repository.upsert_outbox(
                connection,
                outbox_id=_opportunity_outbox_id(source.source_id),
                run_id=run_id,
                entity_id=staged.opportunity_id,
                payload_json=payload_json,
                payload_hash=_payload_hash(payload_json),
                now_utc=staged_at,
                operation=OUTBOX_OPERATION_WITHDRAW,
            ):
                counters["outbox"] += 1

        if source_delivered:
            # A source row is provenance: it records where the job came from and
            # has no feed status of its own, so it is deliberately left in place.
            counters["preserved"] += 1

    def list_outbox(self, *, status: str | None = None) -> list[OutboxEntry]:
        with self._engine.connect() as connection:
            stored = self._repository.list_outbox(connection, status=status)
        return [
            OutboxEntry(
                outbox_id=row["outbox_id"],
                run_id=row["run_id"],
                destination=row["destination"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                operation=row["operation"],
                payload_json=row["payload_json"],
                payload_hash=row["payload_hash"],
                status=row["status"],
                updated_at_utc=row["updated_at_utc"],
                synced_at_utc=row["synced_at_utc"],
            )
            for row in stored
        ]


def _feed_bucket(record: OpportunityRecord) -> str:
    if is_main_feed(record):
        return FEED_MAIN
    if is_review_candidate(record):
        return FEED_REVIEW
    return FEED_EXCLUDED


def _opportunity_outbox_id(source_id: str) -> str:
    return f"outbox_opp_{source_id.removeprefix('src_')}"


def _source_outbox_id(source_id: str) -> str:
    return f"outbox_src_{source_id.removeprefix('src_')}"


def _build_opportunity(
    record: OpportunityRecord,
    bucket: str,
    staged_at: str,
) -> StagedOpportunity:
    match = record.role_match
    content_hash = _content_hash(record, bucket)
    return StagedOpportunity(
        opportunity_id=opportunity_id_for(
            source_type=record.source.source_type,
            company_id=record.company_id,
            external_source_id=record.source.external_source_id,
        ),
        company_id=record.company_id,
        company_name=record.company_name,
        job_title=record.job_title,
        target_role_id=match.role_id if match is not None else None,
        role_match_type=match.match_type.value if match is not None else None,
        role_match_reason=match.reason if match is not None else None,
        location_raw=record.location.location_raw,
        country=record.location.country,
        province=record.location.province,
        city=record.location.city,
        location_status=record.location.status.value,
        work_mode=record.location.work_mode.value,
        remote_scope=record.location.remote_scope.value,
        location_reason=record.location.reason,
        description=record.description,
        posted_at_utc=_format_optional(record.posted_at),
        source_updated_at_utc=_format_optional(record.source_updated_at),
        first_seen_at_utc=staged_at,
        last_seen_at_utc=staged_at,
        availability_status=record.availability_status,
        feed_bucket=bucket,
        content_hash=content_hash,
        created_at_utc=staged_at,
        updated_at_utc=staged_at,
    )


def _build_source(
    record: OpportunityRecord,
    opportunity_id: str,
    staged_at: str,
) -> StagedSource:
    validation = validate_source_url(record.source.source_url)
    return StagedSource(
        source_id=source_id_for(
            source_type=record.source.source_type,
            company_id=record.company_id,
            external_source_id=record.source.external_source_id,
        ),
        opportunity_id=opportunity_id,
        source_type=record.source.source_type,
        company_id=record.company_id,
        external_source_id=record.source.external_source_id,
        source_url=record.source.source_url,
        safe_source_url=validation.safe_url,
        captured_at_utc=format_utc(record.source.captured_at),
        last_checked_at_utc=staged_at,
        provenance_note=record.source.provenance_note,
    )


def _content_hash(record: OpportunityRecord, bucket: str) -> str:
    """Hash the business fields so unchanged re-ingests stay silent."""

    match = record.role_match
    canonical = json.dumps(
        {
            "company_name": record.company_name,
            "job_title": record.job_title,
            "target_role_id": match.role_id if match is not None else None,
            "role_match_type": match.match_type.value if match is not None else None,
            "role_match_reason": match.reason if match is not None else None,
            "location_raw": record.location.location_raw,
            "country": record.location.country,
            "province": record.location.province,
            "city": record.location.city,
            "location_status": record.location.status.value,
            "work_mode": record.location.work_mode.value,
            "remote_scope": record.location.remote_scope.value,
            "location_reason": record.location.reason,
            "description": record.description,
            "posted_at": _format_optional(record.posted_at),
            "source_updated_at": _format_optional(record.source_updated_at),
            "source_url": record.source.source_url,
            "feed_bucket": bucket,
            "availability_status": record.availability_status,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload_json(
    record: OpportunityRecord,
    staged: StagedOpportunity,
    bucket: str,
    *,
    first_seen_at_utc: str,
    operation: str = OUTBOX_OPERATION,
) -> str:
    """Build the Sheets projection, excluding configuration and credentials.

    Values are stored verbatim so the canonical store keeps the source text.
    Because any value may begin with ``=``, ``+``, ``-`` or ``@``, the Sheets
    writer must use ``RAW`` value input (or escape those prefixes) rather than
    ``USER_ENTERED``, otherwise source text would become a formula.
    """

    description = staged.description
    if description is not None and len(description) > SHEETS_CELL_MAX_CHARS:
        description = description[:SHEETS_CELL_MAX_CHARS]

    payload = {
        "opportunity_id": staged.opportunity_id,
        "company_id": staged.company_id,
        "company_name": staged.company_name,
        "job_title": staged.job_title,
        "target_role_id": staged.target_role_id,
        "role_match_type": staged.role_match_type,
        "role_match_reason": staged.role_match_reason,
        "location_raw": staged.location_raw,
        "country": staged.country,
        "province": staged.province,
        "city": staged.city,
        "location_status": staged.location_status,
        "work_mode": staged.work_mode,
        "remote_scope": staged.remote_scope,
        "description": description,
        "posted_at_utc": staged.posted_at_utc,
        "source_updated_at_utc": staged.source_updated_at_utc,
        # First-seen is immutable; a later re-ingest must not move it.
        "first_seen_at_utc": first_seen_at_utc,
        "last_seen_at_utc": staged.last_seen_at_utc,
        "created_at_utc": staged.created_at_utc,
        "updated_at_utc": staged.updated_at_utc,
        "availability_status": staged.availability_status,
        "feed_bucket": bucket,
        "source_type": record.source.source_type,
        "source_url": record.source.source_url,
        "external_source_id": record.source.external_source_id,
        "destination": OUTBOX_DESTINATION,
        "entity_type": OUTBOX_ENTITY_TYPE,
        "operation": operation,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _job_source_payload_json(
    record: OpportunityRecord,
    source: StagedSource,
    *,
    operation: str = OUTBOX_OPERATION,
) -> str:
    """Build the ``Job_Sources`` projection for one source record.

    Only values the connector actually observed are included. Author fields and a
    post excerpt are absent for an ATS record, so they stay blank rather than
    being guessed.
    """

    payload = {
        "source_id": source.source_id,
        "opportunity_id": source.opportunity_id,
        "source_type": source.source_type,
        "source_url": source.source_url,
        "safe_source_url": source.safe_source_url,
        "external_source_id": source.external_source_id,
        "captured_at_utc": source.captured_at_utc,
        "last_seen_at_utc": source.last_checked_at_utc,
        "posted_at": _format_optional(record.posted_at),
        "provenance_method": PROVENANCE_BY_SOURCE_TYPE.get(source.source_type, ""),
        "observation_status": OBSERVATION_SEEN,
        "destination": OUTBOX_DESTINATION,
        "entity_type": OUTBOX_ENTITY_TYPE_SOURCE,
        "operation": operation,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload_hash(payload_json: str) -> str:
    """Hash the exact bytes queued for sync, not the business content."""

    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _format_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return format_utc(ensure_aware_utc(value, "timestamp"))
