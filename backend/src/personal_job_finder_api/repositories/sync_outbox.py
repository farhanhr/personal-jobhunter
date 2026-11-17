"""Durable outbox claiming, fencing, and restart recovery.

Every status transition is a conditional update on the exact claim that was
granted. A worker that lost its claim cannot overwrite a newer payload or a newer
status, so a stale response can never mark superseded work as synchronized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import sync_outbox
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_DESTINATION,
    OUTBOX_ENTITY_TYPE,
)
from personal_job_finder_api.repositories.read_model import (
    ReadModelChange,
    ReadModelSettlementError,
    apply_read_model_change,
)

OUTBOX_PENDING = "pending"
OUTBOX_IN_PROGRESS = "in_progress"
OUTBOX_RETRY_WAIT = "retry_wait"
OUTBOX_BLOCKED = "blocked"
OUTBOX_SYNCED = "synced"

OUTBOX_ENTITY_TYPE_SOURCE = "job_source"

ELIGIBLE_STATUSES = (OUTBOX_PENDING, OUTBOX_RETRY_WAIT)
OUTBOX_STATUSES = (
    OUTBOX_PENDING,
    OUTBOX_IN_PROGRESS,
    OUTBOX_RETRY_WAIT,
    OUTBOX_BLOCKED,
    OUTBOX_SYNCED,
)
INTERRUPTED_ERROR_CODE = "interrupted"

# How long an ``in_progress`` row must be untouched before recovery treats it as
# abandoned. A live dispatch refreshes ``updated_at_utc`` when it claims.
STALE_IN_PROGRESS_AFTER = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    """An immutable snapshot of the work granted to this worker."""

    outbox_id: str
    entity_type: str
    entity_id: str
    operation: str
    payload_json: str
    payload_hash: str
    attempt_count: int
    max_attempts: int

    @property
    def exhausted(self) -> bool:
        return self.attempt_count >= self.max_attempts


class OutboxRepository:
    """Claim and settle outbox rows with compare-and-set semantics."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim_due(self, *, now: datetime, limit: int) -> list[OutboxClaim]:
        """Atomically claim eligible rows and return their snapshots.

        The selection and the transition happen in one ``BEGIN IMMEDIATE``
        transaction, and each row is claimed by a conditional update on its
        observed status, payload hash, and attempt count. Two callers therefore
        cannot both win the same row.
        """

        if limit < 1:
            raise ValueError("claim limit must be at least one")

        now_iso = format_utc(now)
        claims: list[OutboxClaim] = []
        with immediate_transaction(self._engine) as connection:
            candidates = (
                connection.execute(
                    select(sync_outbox)
                    .where(
                        sync_outbox.c.destination == OUTBOX_DESTINATION,
                        sync_outbox.c.status.in_(ELIGIBLE_STATUSES),
                        sync_outbox.c.attempt_count < sync_outbox.c.max_attempts,
                        or_(
                            sync_outbox.c.next_attempt_at_utc.is_(None),
                            sync_outbox.c.next_attempt_at_utc <= now_iso,
                        ),
                    )
                    .order_by(
                        sync_outbox.c.created_at_utc,
                        sync_outbox.c.outbox_id,
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )

            for candidate in candidates:
                claimed = connection.execute(
                    update(sync_outbox)
                    .where(
                        sync_outbox.c.outbox_id == candidate["outbox_id"],
                        sync_outbox.c.status == candidate["status"],
                        sync_outbox.c.payload_hash == candidate["payload_hash"],
                        sync_outbox.c.attempt_count < sync_outbox.c.max_attempts,
                    )
                    .values(
                        status=OUTBOX_IN_PROGRESS,
                        attempt_count=candidate["attempt_count"] + 1,
                        next_attempt_at_utc=None,
                        updated_at_utc=now_iso,
                    )
                )
                if claimed.rowcount != 1:
                    continue
                claims.append(
                    OutboxClaim(
                        outbox_id=candidate["outbox_id"],
                        entity_type=candidate["entity_type"],
                        entity_id=candidate["entity_id"],
                        operation=candidate["operation"],
                        payload_json=candidate["payload_json"],
                        payload_hash=candidate["payload_hash"],
                        attempt_count=candidate["attempt_count"] + 1,
                        max_attempts=candidate["max_attempts"],
                    )
                )
        return claims

    def mark_synced(self, claim: OutboxClaim, *, synced_at: datetime) -> bool:
        """Mark the claim synced only if its payload is still current."""

        synced_iso = format_utc(synced_at)
        with immediate_transaction(self._engine) as connection:
            result = connection.execute(
                update(sync_outbox)
                .where(*self._fence(claim))
                .values(
                    status=OUTBOX_SYNCED,
                    synced_at_utc=synced_iso,
                    updated_at_utc=synced_iso,
                    last_error_code=None,
                    last_error_at_utc=None,
                )
            )
            return result.rowcount == 1

    def settle_synced(
        self,
        claim: OutboxClaim,
        *,
        synced_at: datetime,
        read_model: ReadModelChange,
    ) -> bool:
        """Fence the outbox row and apply the read-model change atomically.

        The conditional update runs first. Only when it affects exactly one row is
        the verified projection applied to the read model, and both commit or roll
        back together. A superseded claim therefore never touches the read model
        and leaves the newer payload pending for the next dispatch.
        """

        synced_iso = format_utc(synced_at)
        try:
            with immediate_transaction(self._engine) as connection:
                result = connection.execute(
                    update(sync_outbox)
                    .where(*self._fence(claim))
                    .values(
                        status=OUTBOX_SYNCED,
                        synced_at_utc=synced_iso,
                        updated_at_utc=synced_iso,
                        last_error_code=None,
                        last_error_at_utc=None,
                    )
                )
                if result.rowcount != 1:
                    return False
                apply_read_model_change(connection, read_model)
                return True
        except SQLAlchemyError as error:
            raise ReadModelSettlementError(
                "The outbox settlement and read-model update were rolled back."
            ) from error

    def mark_retry_wait(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        next_attempt_at: datetime,
        updated_at: datetime,
    ) -> bool:
        """Schedule another attempt, or block once attempts are exhausted."""

        if claim.exhausted:
            return self.mark_blocked(
                claim,
                error_code=error_code,
                updated_at=updated_at,
            )

        retry_iso = format_utc(next_attempt_at)
        updated_iso = format_utc(updated_at)
        with immediate_transaction(self._engine) as connection:
            result = connection.execute(
                update(sync_outbox)
                .where(*self._fence(claim))
                .values(
                    status=OUTBOX_RETRY_WAIT,
                    next_attempt_at_utc=retry_iso,
                    last_error_code=error_code,
                    last_error_at_utc=updated_iso,
                    updated_at_utc=updated_iso,
                )
            )
            return result.rowcount == 1

    def mark_blocked(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        updated_at: datetime,
    ) -> bool:
        """Stop automatic retries until the cause is corrected and requeued."""

        updated_iso = format_utc(updated_at)
        with immediate_transaction(self._engine) as connection:
            result = connection.execute(
                update(sync_outbox)
                .where(*self._fence(claim))
                .values(
                    status=OUTBOX_BLOCKED,
                    last_error_code=error_code,
                    last_error_at_utc=updated_iso,
                    updated_at_utc=updated_iso,
                    next_attempt_at_utc=None,
                )
            )
            return result.rowcount == 1

    def recover_stale_in_progress(
        self,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> int:
        """Return abandoned ``in_progress`` work to the retry queue.

        A crash can leave a row claimed forever. Recovery never marks work
        synced: the remote outcome is unknown, so the next attempt must re-read
        the spreadsheet identities before writing again.
        """

        now_iso = format_utc(now)
        stale_iso = format_utc(stale_before)
        with immediate_transaction(self._engine) as connection:
            result = connection.execute(
                update(sync_outbox)
                .where(
                    sync_outbox.c.destination == OUTBOX_DESTINATION,
                    sync_outbox.c.status == OUTBOX_IN_PROGRESS,
                    sync_outbox.c.updated_at_utc <= stale_iso,
                )
                .values(
                    status=OUTBOX_RETRY_WAIT,
                    last_error_code=INTERRUPTED_ERROR_CODE,
                    last_error_at_utc=now_iso,
                    next_attempt_at_utc=None,
                    updated_at_utc=now_iso,
                    synced_at_utc=None,
                )
            )
            return int(result.rowcount or 0)

    def requeue_blocked(self, *, outbox_id: str, now: datetime) -> bool:
        """Re-enable one blocked projection after the user fixed the cause."""

        now_iso = format_utc(now)
        with immediate_transaction(self._engine) as connection:
            result = connection.execute(
                update(sync_outbox)
                .where(
                    sync_outbox.c.outbox_id == outbox_id,
                    sync_outbox.c.status == OUTBOX_BLOCKED,
                )
                .values(
                    status=OUTBOX_PENDING,
                    attempt_count=0,
                    next_attempt_at_utc=None,
                    last_error_code=None,
                    last_error_at_utc=None,
                    updated_at_utc=now_iso,
                )
            )
            return result.rowcount == 1

    @staticmethod
    def _fence(claim: OutboxClaim) -> tuple[object, ...]:
        """Identify the exact claim that was granted.

        The attempt count changes on every claim, so a stale earlier attempt
        cannot settle newer in-progress work even when the payload hash is
        unchanged. The operation is included for the same reason: a withdrawn
        record and an upsert are different intents.
        """

        return (
            sync_outbox.c.outbox_id == claim.outbox_id,
            sync_outbox.c.status == OUTBOX_IN_PROGRESS,
            sync_outbox.c.payload_hash == claim.payload_hash,
            sync_outbox.c.attempt_count == claim.attempt_count,
            sync_outbox.c.operation == claim.operation,
        )

    # -------------------------------------------------------------- inspection

    def status_counts(self) -> dict[str, dict[str, int]]:
        """Count projections per entity type and state.

        Reads only durable operational state; no Google request and no payload.
        """

        counts = {
            entity_type: dict.fromkeys(OUTBOX_STATUSES, 0)
            for entity_type in (OUTBOX_ENTITY_TYPE, OUTBOX_ENTITY_TYPE_SOURCE)
        }
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    sync_outbox.c.entity_type,
                    sync_outbox.c.status,
                    func.count(),
                )
                .where(sync_outbox.c.destination == OUTBOX_DESTINATION)
                .group_by(sync_outbox.c.entity_type, sync_outbox.c.status)
            ).all()
        for entity_type, status, count in rows:
            bucket = counts.setdefault(str(entity_type), dict.fromkeys(OUTBOX_STATUSES, 0))
            bucket[str(status)] = int(count)
        return counts

    def due_count(self, *, now: datetime) -> int:
        """Count projections eligible for a dispatch right now."""

        now_iso = format_utc(now)
        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(sync_outbox)
                    .where(
                        sync_outbox.c.destination == OUTBOX_DESTINATION,
                        sync_outbox.c.status.in_(ELIGIBLE_STATUSES),
                        sync_outbox.c.attempt_count < sync_outbox.c.max_attempts,
                        or_(
                            sync_outbox.c.next_attempt_at_utc.is_(None),
                            sync_outbox.c.next_attempt_at_utc <= now_iso,
                        ),
                    )
                ).scalar_one()
            )

    def last_synced_at(self) -> str | None:
        with self._engine.connect() as connection:
            return connection.execute(
                select(func.max(sync_outbox.c.synced_at_utc)).where(
                    sync_outbox.c.destination == OUTBOX_DESTINATION
                )
            ).scalar_one()

    def remaining_count(self) -> int:
        """Count projections that have not reached the canonical store yet."""

        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(sync_outbox)
                    .where(
                        sync_outbox.c.destination == OUTBOX_DESTINATION,
                        sync_outbox.c.status != OUTBOX_SYNCED,
                    )
                ).scalar_one()
            )

    def blocked_projections(self, *, limit: int) -> list[dict[str, object]]:
        """Return actionable metadata for blocked work, never its payload."""

        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        sync_outbox.c.outbox_id,
                        sync_outbox.c.entity_type,
                        sync_outbox.c.entity_id,
                        sync_outbox.c.attempt_count,
                        sync_outbox.c.max_attempts,
                        sync_outbox.c.last_error_code,
                        sync_outbox.c.next_attempt_at_utc,
                        sync_outbox.c.updated_at_utc,
                    )
                    .where(
                        sync_outbox.c.destination == OUTBOX_DESTINATION,
                        sync_outbox.c.status == OUTBOX_BLOCKED,
                    )
                    .order_by(sync_outbox.c.updated_at_utc, sync_outbox.c.outbox_id)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def status_of(self, *, outbox_id: str) -> str | None:
        with self._engine.connect() as connection:
            return connection.execute(
                select(sync_outbox.c.status).where(sync_outbox.c.outbox_id == outbox_id)
            ).scalar_one_or_none()
