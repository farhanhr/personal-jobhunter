"""Application services for the manual synchronization API.

These services are a thin layer over the M4B2 dispatcher and outbox repository.
They add no new retry, projection, or reconciliation behaviour; they decide when
to call it, and they sanitize what the API may expose.

Invocation is manual. Nothing here is called at application startup, and no
service schedules or polls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc, utc_now
from personal_job_finder_api.domain.run_reconciliation import reconcile_settled_runs
from personal_job_finder_api.integrations.google_sheets.client import SheetsTransport
from personal_job_finder_api.integrations.google_sheets.config import (
    GoogleSheetsConfig,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_ENTITY_TYPE,
    OUTBOX_ENTITY_TYPE_SOURCE,
)
from personal_job_finder_api.repositories.sync_outbox import (
    OUTBOX_STATUSES,
    STALE_IN_PROGRESS_AFTER,
    OutboxRepository,
)
from personal_job_finder_api.sync.dispatcher import (
    SyncDispatcher,
    SyncOutcome,
    dispatch_guard,
)

MAX_BLOCKED_LISTED = 20

TransportFactory = Callable[[], SheetsTransport]


class SyncConfigurationError(Exception):
    """Raised when the local spreadsheet configuration cannot be used."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SyncOutboxNotFound(Exception):
    """Raised when a requeue targets a projection that does not exist."""


class SyncRequeueInvalidState(Exception):
    """Raised when a requeue targets a projection that is not blocked."""

    def __init__(self, status: str) -> None:
        super().__init__("Only a blocked projection can be requeued.")
        self.status = status


def resolve_spreadsheet_id(
    *,
    spreadsheet_id: str | None,
    fallback: Callable[[], str | None],
) -> str:
    """Return a validated spreadsheet id, without echoing it in failures.

    Status reads never call this, so inspecting sync state does not require
    Google credentials or a configured spreadsheet.
    """

    raw = spreadsheet_id if spreadsheet_id is not None else fallback()
    try:
        return GoogleSheetsConfig(spreadsheet_id=raw or "").spreadsheet_id
    except GoogleSheetsError as error:
        raise SyncConfigurationError(error.code, str(error)) from None


class SyncStatusService:
    """Read-only aggregation of durable outbox state."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = OutboxRepository(engine)

    def snapshot(self) -> dict[str, object]:
        now = self._clock()
        counts = self._repository.status_counts()
        totals = dict.fromkeys(OUTBOX_STATUSES, 0)
        for entity_counts in counts.values():
            for status, count in entity_counts.items():
                totals[status] = totals.get(status, 0) + int(count)

        blocked_rows = self._repository.blocked_projections(limit=MAX_BLOCKED_LISTED + 1)
        blocked_count = totals.get("blocked", 0)

        return {
            "counts": {
                "opportunity": counts.get(OUTBOX_ENTITY_TYPE, {}),
                "job_source": counts.get(OUTBOX_ENTITY_TYPE_SOURCE, {}),
            },
            "totals": {
                **totals,
                "outstanding": sum(count for status, count in totals.items() if status != "synced"),
            },
            "due_now": self._repository.due_count(now=now),
            # Blocked work needs a user decision; pending and retry_wait do not.
            "action_required": blocked_count > 0,
            "blocked": blocked_rows[:MAX_BLOCKED_LISTED],
            "blocked_truncated": len(blocked_rows) > MAX_BLOCKED_LISTED,
            "last_synced_at_utc": self._repository.last_synced_at(),
            "checked_at_utc": format_utc(now),
        }


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What one manual retry request did."""

    outcome: str
    recovered: int
    counters: SyncOutcome

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "recovered": self.recovered,
            **self.counters.as_dict(),
        }


class SyncDispatchService:
    """Run one manual dispatch, reusing the M4B2 dispatcher unchanged."""

    def __init__(
        self,
        engine: Engine,
        *,
        transport_factory: TransportFactory,
        spreadsheet_id: Callable[[], str],
        clock: Callable[[], datetime] = utc_now,
        batch_size: int | None = None,
        max_batch_ranges: int | None = None,
    ) -> None:
        self._engine = engine
        self._transport_factory = transport_factory
        self._spreadsheet_id = spreadsheet_id
        self._clock = clock
        self._batch_size = batch_size
        self._max_batch_ranges = max_batch_ranges
        self._repository = OutboxRepository(engine)

    def dispatch(self) -> DispatchResult:
        """Recover abandoned work, then dispatch whatever is due.

        The whole operation runs under the single-process dispatch guard, so a
        duplicate trigger cannot slip past the "nothing due" shortcut while work
        is genuinely in flight. Recovery happens first so a process that died
        mid-write does not leave work stranded forever. It only returns rows to
        the retry queue; it never marks anything synchronized.
        """

        with dispatch_guard():
            now = self._clock()
            recovered = self._repository.recover_stale_in_progress(
                now=now,
                stale_before=now - STALE_IN_PROGRESS_AFTER,
            )

            if self._repository.due_count(now=now) == 0:
                # Nothing to send, so do not build a transport or call Google.
                # A run may still be pending because a crash interrupted the
                # reconcile step, so sweep that here too: it only touches SQLite.
                reconcile_settled_runs(self._engine)
                return DispatchResult(
                    outcome="nothing_due",
                    recovered=recovered,
                    counters=SyncOutcome(
                        synced=0,
                        failed=0,
                        blocked=0,
                        remaining=self._repository.remaining_count(),
                    ),
                )

            return DispatchResult(
                outcome="dispatched",
                recovered=recovered,
                counters=self._build_dispatcher().dispatch_due(guard=False),
            )

    def _build_dispatcher(self) -> SyncDispatcher:
        # Resolve the spreadsheet id before constructing the transport so a
        # missing/invalid configuration is reported as a sanitized
        # ``sync_configuration_invalid`` instead of an unhandled provider error.
        spreadsheet_id = self._spreadsheet_id()
        try:
            transport = self._transport_factory()
        except GoogleSheetsError as error:
            raise SyncConfigurationError(error.code, str(error)) from None

        options: dict[str, object] = {}
        if self._batch_size is not None:
            options["batch_size"] = self._batch_size
        if self._max_batch_ranges is not None:
            options["max_batch_ranges"] = self._max_batch_ranges
        return SyncDispatcher(
            self._engine,
            transport=transport,
            spreadsheet_id=spreadsheet_id,
            clock=self._clock,
            **options,  # type: ignore[arg-type]
        )


class SyncRequeueService:
    """Re-enable exactly one blocked projection."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = OutboxRepository(engine)

    def requeue(self, outbox_id: str) -> str:
        """Requeue one blocked projection; return its new status."""

        status = self._repository.status_of(outbox_id=outbox_id)
        if status is None:
            raise SyncOutboxNotFound(outbox_id)
        if status != "blocked":
            raise SyncRequeueInvalidState(status)

        if not self._repository.requeue_blocked(
            outbox_id=outbox_id,
            now=self._clock(),
        ):
            # The row changed between the read and the conditional update.
            current = self._repository.status_of(outbox_id=outbox_id)
            if current is None:
                raise SyncOutboxNotFound(outbox_id)
            raise SyncRequeueInvalidState(current)
        return "pending"
