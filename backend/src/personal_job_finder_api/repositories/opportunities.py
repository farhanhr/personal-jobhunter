"""SQLAlchemy Core repository for opportunity staging, provenance, and outbox."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, distinct, func, or_, select, update
from sqlalchemy.engine import Connection

from personal_job_finder_api.db.tables import (
    job_sources,
    opportunities,
    opportunity_run_refs,
    sync_outbox,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_USER_OWNED_HEADERS,
    PENDING_USER_FIELDS_KEY,
)

OUTBOX_DESTINATION = "google_sheets"
OUTBOX_ENTITY_TYPE = "opportunity"
OUTBOX_ENTITY_TYPE_SOURCE = "job_source"
OUTBOX_OPERATION = "upsert"
OUTBOX_OPERATION_WITHDRAW = "withdraw"


def _outbox_matches_run() -> Any:
    """Join predicate matching a projection to the run that observed its entity.

    Opportunity projections key on ``opportunity_id``; source projections key on
    ``source_id``. Both are durable run references, unlike the mutable
    ``sync_outbox.run_id`` column.
    """

    run_entities = opportunity_run_refs
    return or_(
        and_(
            sync_outbox.c.entity_type == OUTBOX_ENTITY_TYPE,
            sync_outbox.c.entity_id == run_entities.c.opportunity_id,
        ),
        and_(
            sync_outbox.c.entity_type == OUTBOX_ENTITY_TYPE_SOURCE,
            sync_outbox.c.entity_id == run_entities.c.source_id,
        ),
    )


def _pending_user_fields(payload: dict[str, Any]) -> frozenset[str]:
    """Read and validate the pending user-owned field list in an outbox payload."""

    raw = payload.get(PENDING_USER_FIELDS_KEY)
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(field, str) for field in raw):
        raise ValueError("The pending user field list must be a list of header names.")
    unknown = set(raw) - set(OPPORTUNITY_USER_OWNED_HEADERS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown pending user fields: {names}.")
    # A pending field without a value is indistinguishable from an explicit blank,
    # so it would erase the canonical cell. Require the value to be present.
    missing = sorted(field for field in raw if field not in payload)
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"Pending user fields without a supplied value: {names}.")
    return frozenset(raw)


def _unowned_user_values(payload: dict[str, Any], pending: frozenset[str]) -> list[str]:
    """Report user-owned values supplied without claiming ownership of them."""

    return sorted(
        header
        for header in OPPORTUNITY_USER_OWNED_HEADERS
        if header in payload and header not in pending
    )


def _merge_opportunity_payload(existing_json: str, new_json: str) -> str:
    """Merge a pending or discovery payload without losing user-owned edits.

    New values win for columns the payload supplies. An unsynced outbox may hold
    pending user-owned tracker values; those values and their ownership metadata
    survive a discovery re-ingestion. A user-owned column that is neither pending
    in the existing payload nor newly supplied by a tracker patch is removed, so
    connector-derived data can never masquerade as user-owned input.
    """

    existing = json.loads(existing_json)
    new = json.loads(new_json)
    if not isinstance(existing, dict) or not isinstance(new, dict):
        raise ValueError("An outbox payload must be a JSON object.")

    existing_pending = _pending_user_fields(existing)
    new_pending = _pending_user_fields(new)

    # A user-owned value that does not declare ownership would be silently
    # discarded, so reject it instead of dropping the user's edit.
    unowned = _unowned_user_values(new, new_pending)
    if unowned:
        names = ", ".join(unowned)
        raise ValueError(f"User-owned values supplied without pending ownership: {names}.")

    merged: dict[str, Any] = {**existing, **new}
    for header in OPPORTUNITY_USER_OWNED_HEADERS:
        if header in new_pending:
            merged[header] = new[header]
        elif header in existing_pending and header in existing:
            merged[header] = existing[header]
        else:
            merged.pop(header, None)

    # Ownership metadata must describe only values the payload actually holds, so
    # a pending header can never be carried forward without its value.
    pending = sorted(header for header in existing_pending | new_pending if header in merged)
    if pending:
        merged[PENDING_USER_FIELDS_KEY] = pending
    else:
        merged.pop(PENDING_USER_FIELDS_KEY, None)

    return json.dumps(merged, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StagedOpportunity:
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
    location_reason: str | None
    description: str | None
    posted_at_utc: str | None
    source_updated_at_utc: str | None
    first_seen_at_utc: str
    last_seen_at_utc: str
    availability_status: str
    feed_bucket: str
    content_hash: str
    created_at_utc: str
    updated_at_utc: str


@dataclass(frozen=True, slots=True)
class StagedSource:
    source_id: str
    opportunity_id: str
    source_type: str
    company_id: str
    external_source_id: str
    source_url: str
    safe_source_url: str | None
    captured_at_utc: str
    last_checked_at_utc: str
    provenance_note: str | None


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    outbox_id: str
    run_id: str | None
    destination: str
    entity_type: str
    entity_id: str
    operation: str
    payload_json: str
    payload_hash: str
    status: str
    updated_at_utc: str
    synced_at_utc: str | None


class OpportunityStagingRepository:
    """Read and write the local staging projection and its sync outbox.

    Only the ``opportunity`` entity type is queued. The canonical ``Job_Sources``
    tab needs one projection row per source record, which is M4B work together
    with the Sheets transport; until then the provenance rows stay local.
    """

    def find_opportunity(
        self,
        connection: Connection,
        opportunity_id: str,
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                select(opportunities).where(opportunities.c.opportunity_id == opportunity_id)
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def insert_opportunity(
        self,
        connection: Connection,
        record: StagedOpportunity,
    ) -> None:
        connection.execute(
            opportunities.insert().values(
                opportunity_id=record.opportunity_id,
                company_id=record.company_id,
                company_name=record.company_name,
                job_title=record.job_title,
                target_role_id=record.target_role_id,
                role_match_type=record.role_match_type,
                role_match_reason=record.role_match_reason,
                location_raw=record.location_raw,
                country=record.country,
                province=record.province,
                city=record.city,
                location_status=record.location_status,
                work_mode=record.work_mode,
                remote_scope=record.remote_scope,
                location_reason=record.location_reason,
                description=record.description,
                posted_at_utc=record.posted_at_utc,
                source_updated_at_utc=record.source_updated_at_utc,
                first_seen_at_utc=record.first_seen_at_utc,
                last_seen_at_utc=record.last_seen_at_utc,
                availability_status=record.availability_status,
                feed_bucket=record.feed_bucket,
                content_hash=record.content_hash,
                created_at_utc=record.created_at_utc,
                updated_at_utc=record.updated_at_utc,
            )
        )

    def update_opportunity(
        self,
        connection: Connection,
        record: StagedOpportunity,
    ) -> None:
        """Update mutable fields; identity and first-seen time are immutable."""

        connection.execute(
            update(opportunities)
            .where(opportunities.c.opportunity_id == record.opportunity_id)
            .values(
                company_name=record.company_name,
                job_title=record.job_title,
                target_role_id=record.target_role_id,
                role_match_type=record.role_match_type,
                role_match_reason=record.role_match_reason,
                location_raw=record.location_raw,
                country=record.country,
                province=record.province,
                city=record.city,
                location_status=record.location_status,
                work_mode=record.work_mode,
                remote_scope=record.remote_scope,
                location_reason=record.location_reason,
                description=record.description,
                posted_at_utc=record.posted_at_utc,
                source_updated_at_utc=record.source_updated_at_utc,
                last_seen_at_utc=record.last_seen_at_utc,
                availability_status=record.availability_status,
                feed_bucket=record.feed_bucket,
                content_hash=record.content_hash,
                updated_at_utc=record.updated_at_utc,
            )
        )

    def touch_last_seen(
        self,
        connection: Connection,
        opportunity_id: str,
        *,
        last_seen_at_utc: str,
    ) -> None:
        connection.execute(
            update(opportunities)
            .where(opportunities.c.opportunity_id == opportunity_id)
            .values(last_seen_at_utc=last_seen_at_utc)
        )

    def find_source(
        self,
        connection: Connection,
        *,
        source_type: str,
        company_id: str,
        external_source_id: str,
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                select(job_sources).where(
                    job_sources.c.source_type == source_type,
                    job_sources.c.company_id == company_id,
                    job_sources.c.external_source_id == external_source_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def upsert_source(
        self,
        connection: Connection,
        record: StagedSource,
    ) -> None:
        existing = self.find_source(
            connection,
            source_type=record.source_type,
            company_id=record.company_id,
            external_source_id=record.external_source_id,
        )
        if existing is None:
            connection.execute(
                job_sources.insert().values(
                    source_id=record.source_id,
                    opportunity_id=record.opportunity_id,
                    source_type=record.source_type,
                    company_id=record.company_id,
                    external_source_id=record.external_source_id,
                    source_url=record.source_url,
                    safe_source_url=record.safe_source_url,
                    captured_at_utc=record.captured_at_utc,
                    last_checked_at_utc=record.last_checked_at_utc,
                    provenance_note=record.provenance_note,
                )
            )
            return

        connection.execute(
            update(job_sources)
            .where(job_sources.c.source_id == existing["source_id"])
            .values(
                opportunity_id=record.opportunity_id,
                source_url=record.source_url,
                safe_source_url=record.safe_source_url,
                last_checked_at_utc=record.last_checked_at_utc,
                provenance_note=record.provenance_note,
            )
        )

    def upsert_run_ref(
        self,
        connection: Connection,
        *,
        opportunity_id: str,
        run_id: str,
        source_id: str,
        seen_at_utc: str,
    ) -> None:
        existing = (
            connection.execute(
                select(opportunity_run_refs).where(
                    opportunity_run_refs.c.opportunity_id == opportunity_id,
                    opportunity_run_refs.c.run_id == run_id,
                    opportunity_run_refs.c.source_id == source_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            connection.execute(
                opportunity_run_refs.insert().values(
                    opportunity_id=opportunity_id,
                    run_id=run_id,
                    source_id=source_id,
                    first_seen_at_utc=seen_at_utc,
                    last_seen_at_utc=seen_at_utc,
                )
            )
            return

        connection.execute(
            update(opportunity_run_refs)
            .where(
                opportunity_run_refs.c.opportunity_id == opportunity_id,
                opportunity_run_refs.c.run_id == run_id,
                opportunity_run_refs.c.source_id == source_id,
            )
            .values(last_seen_at_utc=seen_at_utc)
        )

    def find_outbox(
        self,
        connection: Connection,
        *,
        entity_id: str,
        destination: str = OUTBOX_DESTINATION,
        entity_type: str = OUTBOX_ENTITY_TYPE,
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                select(sync_outbox).where(
                    sync_outbox.c.destination == destination,
                    sync_outbox.c.entity_type == entity_type,
                    sync_outbox.c.entity_id == entity_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def upsert_outbox(
        self,
        connection: Connection,
        *,
        outbox_id: str,
        run_id: str | None,
        entity_id: str,
        payload_json: str,
        payload_hash: str,
        now_utc: str,
        destination: str = OUTBOX_DESTINATION,
        entity_type: str = OUTBOX_ENTITY_TYPE,
        operation: str = OUTBOX_OPERATION,
    ) -> bool:
        """Create or refresh one pending projection; return True when work queued.

        An unchanged payload for an already-synced projection is a no-op. Changed
        content always becomes ``pending`` so the next sync sends the new values.

        For an unsynced opportunity row, discovery values are replaced while the
        pending user-owned tracker fields and their ownership metadata are
        preserved. A ``synced`` row never carries stale user values forward: its
        canonical sheet row is authoritative.

        A row that is ``in_progress`` is being written by a sync worker. When the
        content changes underneath it, the worker must compare ``payload_hash``
        before marking the row ``synced``. The M4B dispatcher must therefore use a
        fenced update:

        ``UPDATE sync_outbox SET status='synced', synced_at_utc=:t
        WHERE outbox_id=:id AND payload_hash=:sent_hash AND status='in_progress'``

        A zero row count means the payload was superseded, so the newer version
        must stay pending instead of being marked as synchronized.
        """

        existing = self.find_outbox(
            connection,
            entity_id=entity_id,
            destination=destination,
            entity_type=entity_type,
        )
        if existing is None:
            connection.execute(
                sync_outbox.insert().values(
                    outbox_id=outbox_id,
                    run_id=run_id,
                    destination=destination,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    operation=operation,
                    payload_json=payload_json,
                    payload_hash=payload_hash,
                    status="pending",
                    attempt_count=0,
                    max_attempts=5,
                    next_attempt_at_utc=None,
                    last_error_code=None,
                    last_error_at_utc=None,
                    created_at_utc=now_utc,
                    updated_at_utc=now_utc,
                    synced_at_utc=None,
                )
            )
            return True

        effective_payload_json = payload_json
        effective_payload_hash = payload_hash
        if entity_type == OUTBOX_ENTITY_TYPE and existing["status"] != "synced":
            # Preserve pending user-owned tracker edits across discovery
            # re-ingestion. A synced row must not carry stale user values back
            # into the outbox; the canonical sheet is authoritative there.
            effective_payload_json = _merge_opportunity_payload(
                str(existing["payload_json"]),
                payload_json,
            )
            effective_payload_hash = _payload_hash(effective_payload_json)

        # Unchanged content never needs new work. Skipping here also preserves an
        # in-flight retry schedule instead of resetting its backoff.
        if existing["payload_hash"] == effective_payload_hash:
            return False

        connection.execute(
            update(sync_outbox)
            .where(sync_outbox.c.outbox_id == existing["outbox_id"])
            .values(
                # ``run_id`` records who first queued this projection; durable run
                # references, not this column, decide a run's sync obligation.
                operation=operation,
                payload_json=effective_payload_json,
                payload_hash=effective_payload_hash,
                status="pending",
                # New content is new work, so it gets a fresh attempt budget.
                # Without this reset a row that exhausted its retries could never
                # be claimed again and would sit at ``pending`` forever.
                attempt_count=0,
                next_attempt_at_utc=None,
                last_error_code=None,
                last_error_at_utc=None,
                updated_at_utc=now_utc,
                synced_at_utc=None,
            )
        )
        return True

    def has_delivered_outbox(
        self,
        connection: Connection,
        *,
        entity_id: str,
        destination: str = OUTBOX_DESTINATION,
        entity_type: str = OUTBOX_ENTITY_TYPE,
    ) -> bool:
        """Report whether a projection may already exist in the spreadsheet.

        ``synced`` obviously exists remotely. ``in_progress``, ``retry_wait``, and
        ``blocked`` may exist too: each can follow a write whose response was lost,
        so treating them as "never sent" would leave a stale row behind.
        """

        row = self.find_outbox(
            connection,
            entity_id=entity_id,
            destination=destination,
            entity_type=entity_type,
        )
        if row is None:
            return False
        return row["status"] in ("synced", "in_progress", "retry_wait", "blocked")

    def discard_unsent_outbox(
        self,
        connection: Connection,
        *,
        entity_id: str,
        destination: str = OUTBOX_DESTINATION,
        entity_type: str = OUTBOX_ENTITY_TYPE,
    ) -> int:
        """Withdraw queued work for a record that became local-only.

        Only rows that provably never left this machine are removed, meaning no
        send attempt was ever recorded. Anything with an attempt, an in-flight
        claim, or a delivered row must be handled as a withdrawal instead, because
        the remote write may have succeeded even if the response was lost.
        """

        result = connection.execute(
            sync_outbox.delete().where(
                sync_outbox.c.destination == destination,
                sync_outbox.c.entity_type == entity_type,
                sync_outbox.c.entity_id == entity_id,
                sync_outbox.c.status.in_(("pending", "retry_wait", "blocked")),
                sync_outbox.c.attempt_count == 0,
            )
        )
        return int(result.rowcount or 0)

    def count_unsynced_for_run(self, connection: Connection, run_id: str) -> int:
        """Count distinct projections still awaiting canonical synchronization.

        Both opportunity and job-source projections count, because a run is not
        synchronized until its provenance rows are also in the canonical store.
        The count is scoped through durable run references, so pending work from
        unrelated runs cannot keep this run pending. Excluded local-only records
        have no outbox row and therefore do not count.
        """

        statement = (
            select(func.count(distinct(sync_outbox.c.outbox_id)))
            .select_from(sync_outbox.join(opportunity_run_refs, _outbox_matches_run()))
            .where(
                opportunity_run_refs.c.run_id == run_id,
                sync_outbox.c.destination == OUTBOX_DESTINATION,
                sync_outbox.c.status != "synced",
            )
        )
        return int(connection.execute(statement).scalar_one())

    def list_outbox(
        self,
        connection: Connection,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(sync_outbox).order_by(sync_outbox.c.created_at_utc)
        if status is not None:
            statement = statement.where(sync_outbox.c.status == status)
        return [dict(row) for row in connection.execute(statement).mappings().all()]
