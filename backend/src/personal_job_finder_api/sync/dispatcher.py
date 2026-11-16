"""Durable dispatcher that projects staged records into Google Sheets.

Invocation is manual and process-local. Each batch follows the same shape:

1. Claim eligible outbox rows in one short ``BEGIN IMMEDIATE`` transaction and
   commit before any network call.
2. Read the spreadsheet identities so an uncertain earlier attempt cannot create a
   duplicate row.
3. Write changed rows in bounded ``RAW`` batches.
4. Settle each claim with a conditional update fenced on the claimed payload hash.

No Google request ever runs inside a SQLite transaction, and no connector is
executed, so a retry never spends search quota.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.domain.run_reconciliation import (
    reconcile_run_status,
    runs_observing,
)
from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleSheetsClient,
    SheetsTransport,
    column_letter,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    GoogleSheetsConfig,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_ENTITY_TYPE,
    OUTBOX_ENTITY_TYPE_SOURCE,
    OUTBOX_OPERATION_WITHDRAW,
)
from personal_job_finder_api.repositories.sync_outbox import (
    STALE_IN_PROGRESS_AFTER,
    OutboxClaim,
    OutboxRepository,
)
from personal_job_finder_api.sync.projections import (
    JOB_SOURCE_TAB,
    OPPORTUNITY_TAB,
    ProjectedRow,
    ProjectionError,
    project_job_source,
    project_opportunity,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 25
DEFAULT_CLAIM_LIMIT = 100
DEFAULT_MAX_BATCH_RANGES = 100
BACKOFF_BASE_SECONDS = 5
BACKOFF_CAP_SECONDS = 600
RETRY_AFTER_CAP_SECONDS = 300

# Errors a retry cannot fix. Everything else is retried within ``max_attempts``.
PERMANENT_ERROR_CODES = frozenset(
    {
        "reconnect_required",
        "permission_denied",
        "spreadsheet_not_found",
        "schema_mismatch",
        "credentials_missing",
        "credentials_invalid",
        "oauth_configuration_invalid",
        "spreadsheet_id_missing",
        "spreadsheet_id_invalid",
        "duplicate_identity",
        "projection_failed",
        "unknown_entity_type",
        "withdrawal_unsupported",
        "batch_too_large",
        "invalid_request",
        "malformed_response",
    }
)

TAB_HEADERS: Mapping[str, tuple[str, ...]] = {
    OPPORTUNITY_TAB: OPPORTUNITY_HEADERS,
    JOB_SOURCE_TAB: JOB_SOURCE_HEADERS,
}

# The supported deployment is one application process. Cross-process exclusion
# for *outbox rows* comes from SQLite's claim transaction, but spreadsheet row
# positions are not transactional, so two independent processes syncing at once
# are not supported. The post-write verification read turns that unsupported
# case into a visible retry instead of a silent overwrite.


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Counters describing one dispatch invocation."""

    synced: int
    failed: int
    blocked: int
    remaining: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _TableState:
    """The identity layout of one tab, read once per dispatch."""

    rows: tuple[tuple[str, ...], ...]
    positions: Mapping[str, int]

    @property
    def next_free_row(self) -> int:
        return len(self.rows) + 1

    def row(self, row_number: int) -> tuple[str, ...] | None:
        if 1 <= row_number <= len(self.rows):
            return self.rows[row_number - 1]
        return None

    def duplicates(self) -> frozenset[str]:
        return frozenset(identity for identity, position in self.positions.items() if position < 0)

    def row_of(self, identity: str) -> int | None:
        position = self.positions.get(identity)
        if position is None or position < 0:
            return None
        return position


@dataclass(frozen=True, slots=True)
class _Settled:
    claim: OutboxClaim
    synced: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _PlannedWrite:
    claim: OutboxClaim
    row_number: int | None
    projection: ProjectedRow


class SyncDispatcher:
    """One-pass, manually invoked outbox dispatcher."""

    def __init__(
        self,
        engine: Engine,
        *,
        transport: SheetsTransport,
        spreadsheet_id: str,
        clock: Callable[[], datetime] = utc_now,
        batch_size: int = DEFAULT_BATCH_SIZE,
        claim_limit: int = DEFAULT_CLAIM_LIMIT,
        max_batch_ranges: int = DEFAULT_MAX_BATCH_RANGES,
        repository: OutboxRepository | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least one")
        if claim_limit < 1:
            raise ValueError("claim_limit must be at least one")

        self._engine = engine
        self._transport = transport
        self._spreadsheet_id = spreadsheet_id
        self._clock = clock
        self._claim_limit = claim_limit
        self._repository = repository or OutboxRepository(engine)
        self._client = GoogleSheetsClient(
            transport,
            max_batch_ranges=max_batch_ranges,
        )
        self._batch_size = min(batch_size, self._client.max_batch_ranges)
        self._config_error = _configuration_error(spreadsheet_id)

    # ------------------------------------------------------------------ public

    def recover(self) -> int:
        """Return abandoned ``in_progress`` rows to the retry queue."""

        now = self._clock()
        return self._repository.recover_stale_in_progress(
            now=now,
            stale_before=now - STALE_IN_PROGRESS_AFTER,
        )

    def dispatch_due(self) -> SyncOutcome:
        """Dispatch eligible work until nothing is due or the claim bound is hit."""

        return self._dispatch_loop()

    def _dispatch_loop(self) -> SyncOutcome:
        synced = 0
        failed = 0
        blocked = 0
        affected_opportunities: list[str] = []
        affected_sources: list[str] = []

        while True:
            claims = self._repository.claim_due(
                now=self._clock(),
                limit=self._claim_limit,
            )
            if not claims:
                break
            self._after_claim(claims)

            settled = self._dispatch_claims(claims)
            for item in settled:
                if item.synced:
                    synced += 1
                    if item.claim.entity_type == OUTBOX_ENTITY_TYPE:
                        affected_opportunities.append(item.claim.entity_id)
                    else:
                        affected_sources.append(item.claim.entity_id)
                elif item.error_code:
                    failed += 1
                    if item.error_code in PERMANENT_ERROR_CODES:
                        blocked += 1

            if len(claims) < self._claim_limit:
                break

        if affected_opportunities or affected_sources:
            run_ids = runs_observing(
                self._engine,
                opportunity_ids=affected_opportunities,
                source_ids=affected_sources,
            )
        else:
            run_ids = ()

        reconcile_run_status(self._engine, run_ids=run_ids)

        return SyncOutcome(
            synced=synced,
            failed=failed,
            blocked=blocked,
            remaining=self._remaining(),
        )

    # ----------------------------------------------------------------- private

    def _after_claim(self, claims: Sequence[OutboxClaim]) -> None:
        """Hook used by tests to simulate a concurrent payload change."""

    def _dispatch_claims(self, claims: Sequence[OutboxClaim]) -> list[_Settled]:
        if self._config_error is not None:
            return [self._block(claim, error_code=self._config_error) for claim in claims]

        grouped: dict[str, list[OutboxClaim]] = {}
        settled: list[_Settled] = []

        for claim in claims:
            tab = _tab_for(claim)
            if tab is None:
                settled.append(self._block(claim, error_code="unknown_entity_type"))
                continue
            grouped.setdefault(tab, []).append(claim)

        for tab, tab_claims in grouped.items():
            settled.extend(self._dispatch_tab(tab, tab_claims))
        return settled

    def _dispatch_tab(self, tab: str, claims: Sequence[OutboxClaim]) -> list[_Settled]:
        try:
            state = self._read_table(tab)
        except GoogleSheetsError as error:
            return [self._settle_failure(claim, error) for claim in claims]

        duplicates = state.duplicates()
        settled: list[_Settled] = []
        planned: list[_PlannedWrite] = []
        next_row = state.next_free_row

        for claim in claims:
            if claim.entity_id in duplicates:
                settled.append(self._block(claim, error_code="duplicate_identity"))
                continue

            try:
                payload = _payload_of(claim)
            except ProjectionError as error:
                settled.append(self._block(claim, error_code=error.code))
                continue

            row_number = state.row_of(claim.entity_id)
            existing = state.row(row_number) if row_number is not None else None

            if claim.operation == OUTBOX_OPERATION_WITHDRAW and row_number is None:
                # Nothing reached the spreadsheet for this record, so there is
                # nothing to withdraw and inserting a blank row would be wrong.
                settled.append(self._mark_synced(claim))
                continue

            try:
                projection = (
                    project_opportunity(payload, existing_row=existing)
                    if tab == OPPORTUNITY_TAB
                    else project_job_source(payload, existing_row=existing)
                )
            except ProjectionError as error:
                settled.append(self._block(claim, error_code=error.code))
                continue

            if claim.operation == OUTBOX_OPERATION_WITHDRAW and not projection.is_withdrawn:
                # The accepted contract has no tombstone column. Only a record
                # whose location is excluded has a contract-valid way to say
                # "not in the feed"; anything else would need a misleading value,
                # so surface the conflict instead of writing one.
                settled.append(self._block(claim, error_code="withdrawal_unsupported"))
                continue

            if row_number is None:
                row_number = next_row
                next_row += 1
            planned.append(
                _PlannedWrite(
                    claim=claim,
                    row_number=row_number,
                    projection=projection,
                )
            )
        if not planned:
            return settled

        written: list[_PlannedWrite] = []
        try:
            for start in range(0, len(planned), self._batch_size):
                chunk = planned[start : start + self._batch_size]
                self._write(tab, chunk)
                self._verify(tab, chunk)
                written.extend(chunk)
        except GoogleSheetsError as error:
            remainder = planned[len(written) :]
            return [
                *settled,
                *(self._mark_synced(item.claim) for item in written),
                *(self._settle_failure(item.claim, error) for item in remainder),
            ]

        return [*settled, *(self._mark_synced(item.claim) for item in written)]

    def _read_table(self, tab: str) -> _TableState:
        headers = TAB_HEADERS[tab]
        last_column = column_letter(len(headers))
        requested = f"{tab}!A:{last_column}"
        values = self._client.get_values(self._spreadsheet_id, [requested])
        rows = tuple(tuple(row) for row in values.get(requested, []))
        if not rows:
            raise GoogleSheetsError(
                "schema_mismatch",
                "The required spreadsheet tab has no header row.",
            )

        header_row = tuple(cell for cell in rows[0][: len(headers)])
        if header_row != headers:
            # Writing against a reordered header would silently put values in the
            # wrong columns, so refuse until the user fixes the sheet.
            raise GoogleSheetsError(
                "schema_mismatch",
                f"The {tab} header row does not match the accepted schema.",
            )

        positions: dict[str, int] = {}
        for row_number, row in enumerate(rows[1:], start=2):
            identity = row[0] if row else ""
            if not identity:
                continue
            if identity in positions:
                # A negative marker makes the duplicate visible without losing
                # the fact that at least one row exists.
                positions[identity] = -1
                continue
            positions[identity] = row_number
        return _TableState(rows=rows, positions=positions)

    def _write(self, tab: str, planned: Sequence[_PlannedWrite]) -> None:
        last_column = column_letter(len(TAB_HEADERS[tab]))
        data: list[dict[str, Any]] = []

        for item in planned:
            assert item.row_number is not None
            data.append(
                {
                    "range": (f"{tab}!A{item.row_number}:{last_column}{item.row_number}"),
                    "values": [list(item.projection.values)],
                }
            )

        self._client.batch_update_values(self._spreadsheet_id, data)

    def _verify(self, tab: str, written: Sequence[_PlannedWrite]) -> None:
        """Confirm the written identities are present before settling as synced.

        ``batchUpdate`` is not transactional, so a partial failure can leave some
        rows applied. Re-reading also makes a lost response safe: the next attempt
        finds the row by identity instead of appending a duplicate.
        """

        state = self._read_table(tab)
        for item in written:
            if state.row_of(item.claim.entity_id) is None:
                raise GoogleSheetsError(
                    "write_not_confirmed",
                    "The spreadsheet did not retain the written row.",
                )

    def _mark_synced(self, claim: OutboxClaim) -> _Settled:
        if self._repository.mark_synced(claim, synced_at=self._clock()):
            return _Settled(claim=claim, synced=True)
        # The payload changed while this request was in flight, so the newer
        # version must stay pending for the next dispatch.
        logger.info(
            "sync_projection_superseded",
            extra={"outbox_id": claim.outbox_id, "entity": claim.entity_type},
        )
        return _Settled(claim=claim, synced=False)

    def _settle_failure(
        self,
        claim: OutboxClaim,
        error: GoogleSheetsError,
    ) -> _Settled:
        now = self._clock()
        if error.code in PERMANENT_ERROR_CODES:
            return self._block(claim, error_code=error.code)

        delay = _backoff_delay(claim.attempt_count)
        retry_after = getattr(error, "retry_after_seconds", None)
        if isinstance(retry_after, int) and retry_after > 0:
            delay = min(retry_after, RETRY_AFTER_CAP_SECONDS)

        self._repository.mark_retry_wait(
            claim,
            error_code=error.code,
            next_attempt_at=now + timedelta(seconds=delay),
            updated_at=now,
        )
        return _Settled(claim=claim, synced=False, error_code=error.code)

    def _block(self, claim: OutboxClaim, *, error_code: str) -> _Settled:
        self._repository.mark_blocked(
            claim,
            error_code=error_code,
            updated_at=self._clock(),
        )
        return _Settled(claim=claim, synced=False, error_code=error_code)

    def _remaining(self) -> int:
        with self._engine.connect() as connection:
            return int(
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM sync_outbox WHERE status != 'synced'"
                ).scalar_one()
            )


def _configuration_error(spreadsheet_id: str) -> str | None:
    """Return a stable error code when the local configuration is unusable."""

    try:
        GoogleSheetsConfig(spreadsheet_id=spreadsheet_id)
    except GoogleSheetsError as error:
        return error.code
    return None


def _tab_for(claim: OutboxClaim) -> str | None:
    if claim.entity_type == OUTBOX_ENTITY_TYPE:
        return OPPORTUNITY_TAB
    if claim.entity_type == OUTBOX_ENTITY_TYPE_SOURCE:
        return JOB_SOURCE_TAB
    return None


def _payload_of(claim: OutboxClaim) -> dict[str, Any]:
    try:
        payload = json.loads(claim.payload_json)
    except (TypeError, ValueError) as error:
        raise ProjectionError(
            "projection_failed",
            "The stored projection payload is not readable.",
        ) from error
    if not isinstance(payload, dict):
        raise ProjectionError(
            "projection_failed",
            "The stored projection payload is not an object.",
        )
    return payload


def _backoff_delay(attempt_count: int) -> int:
    exponent = max(0, min(attempt_count - 1, 10))
    return min(BACKOFF_BASE_SECONDS * (2**exponent), BACKOFF_CAP_SECONDS)


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_BATCH_SIZE",
    "PERMANENT_ERROR_CODES",
    "RETRY_AFTER_CAP_SECONDS",
    "SyncDispatcher",
    "SyncOutcome",
]
