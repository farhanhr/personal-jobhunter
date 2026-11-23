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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.domain.run_reconciliation import (
    reconcile_run_status,
    reconcile_settled_runs,
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
    header_row_for,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_ENTITY_TYPE,
    OUTBOX_ENTITY_TYPE_SOURCE,
    OUTBOX_OPERATION_WITHDRAW,
)
from personal_job_finder_api.repositories.read_model import (
    ReadModelChange,
    ReadModelSettlementError,
    read_model_change,
)
from personal_job_finder_api.repositories.sync_outbox import (
    STALE_IN_PROGRESS_AFTER,
    OutboxClaim,
    OutboxRepository,
)
from personal_job_finder_api.sync.coordination import (
    SheetsOperationBusy,
    acquire_sheets_io,
    release_sheets_io,
)
from personal_job_finder_api.sync.projections import (
    JOB_SOURCE_TAB,
    OPPORTUNITY_TAB,
    ProjectedRow,
    ProjectionError,
    is_tracker_only_opportunity_payload,
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
        "spreadsheet_id_missing",
        "spreadsheet_id_invalid",
        "duplicate_identity",
        "projection_failed",
        "unknown_entity_type",
        "withdrawal_unsupported",
        "missing_parent_opportunity",
        # A tracker-only payload cannot create a complete canonical row, so a
        # deleted row is an operator-action-required conflict, not a retry.
        "canonical_row_missing",
        # A write that may have committed but cannot be proven correct needs a
        # human to inspect the sheet; retrying could repeat the damage.
        "canonical_identity_map_changed",
        "canonical_write_count_mismatch",
        "batch_too_large",
        "invalid_request",
        "malformed_response",
    }
)

TAB_HEADERS: Mapping[str, tuple[str, ...]] = {
    OPPORTUNITY_TAB: OPPORTUNITY_HEADERS,
    JOB_SOURCE_TAB: JOB_SOURCE_HEADERS,
}

# The supported deployment is one application process. Dispatch and refresh share
# one lock so a refresh cannot publish a snapshot read in the middle of a write
# batch. Cross-process exclusion for *outbox rows* comes from SQLite's claim
# transaction, but spreadsheet row positions are not transactional, so two
# independent processes syncing at once are not supported. The post-write
# verification read turns that unsupported case into a visible retry instead of a
# silent overwrite.


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Counters describing one dispatch invocation."""

    synced: int
    failed: int
    blocked: int
    remaining: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class SyncAlreadyRunning(RuntimeError):
    """Raised when a dispatch is already running inside this process.

    The supported deployment is a single process, so this is the deterministic
    answer to a duplicate trigger: the caller is told to wait instead of a second
    worker starting.
    """


@contextmanager
def dispatch_guard() -> Iterator[None]:
    """Hold the shared Sheets I/O lock, or fail immediately.

    Callers that must also protect a pre-dispatch shortcut (for example the API
    deciding there is nothing due) should hold this guard themselves and then call
    ``SyncDispatcher.dispatch_due(guard=False)``.
    """

    try:
        acquire_sheets_io()
    except SheetsOperationBusy:
        raise SyncAlreadyRunning(
            "A Google Sheets refresh or synchronization is already running. "
            "Wait for it to finish before starting another."
        ) from None
    try:
        yield
    finally:
        release_sheets_io()


@dataclass(frozen=True, slots=True)
class _TableState:
    """The identity layout of one tab, read once per dispatch.

    ``rows`` starts at the tab's canonical header row, so ``first_row`` is the
    1-based sheet row of ``rows[0]``. Positions and ``row()`` use absolute sheet
    row numbers, which is what the writer and the identity map speak.
    """

    rows: tuple[tuple[str, ...], ...]
    positions: Mapping[str, int]
    first_row: int = 1

    @property
    def next_free_row(self) -> int:
        return self.first_row + len(self.rows)

    def row(self, row_number: int) -> tuple[str, ...] | None:
        index = row_number - self.first_row
        if 0 <= index < len(self.rows):
            return self.rows[index]
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
    error: GoogleSheetsError | None = None


@dataclass(frozen=True, slots=True)
class _PlannedWrite:
    claim: OutboxClaim
    row_number: int | None
    projection: ProjectedRow
    parent: ReadModelChange | None = None
    # True when the plan targets a row that already held this identity at read
    # time. False for a planned append, whose destination must still be empty.
    existing_row_expected: bool = False


def _row_moved_error() -> GoogleSheetsError:
    """A retryable signal that the planned physical row no longer matches."""

    return GoogleSheetsError(
        "row_moved",
        "The spreadsheet changed after it was read. The row will be planned again.",
    )


def _identity_at(actual: Mapping[int, str], row: int) -> str | None:
    """Return the identity at one canonical row, or None when the row is empty."""

    return actual.get(row)


def _parse_identity_map(
    rows: Sequence[Sequence[str]],
    *,
    first_data_row: int,
) -> dict[int, str]:
    """Map every populated canonical row to its identity, skipping the header.

    ``first_data_row`` is the 1-based row number of ``rows[1]``. It keeps the
    absolute row numbers correct now that the canonical header is not row 1.
    """

    identities: dict[int, str] = {}
    for row_number, row in enumerate(rows[1:], start=first_data_row):
        identity = row[0] if row else ""
        if identity:
            identities[row_number] = identity
    return identities


def _identity_map_changed() -> GoogleSheetsError:
    """An operator-action-required signal for an unverifiable committed write."""

    return GoogleSheetsError(
        "canonical_identity_map_changed",
        "The canonical sheet changed in an unexpected way after writing. "
        "Manual inspection is required; the affected work was not marked "
        "as synchronized.",
    )


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

    def dispatch_due(self, *, guard: bool = True) -> SyncOutcome:
        """Dispatch eligible work until nothing is due or the claim bound is hit.

        ``guard=False`` is for callers that already hold :func:`dispatch_guard`,
        such as the API service that must also protect its no-work shortcut.
        """

        if not guard:
            return self._dispatch_loop()
        with dispatch_guard():
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

        # Also sweep runs that still look pending but have no outstanding work.
        # Without this a crash between settling a row and reconciling its run
        # would leave the run stuck forever, because no later dispatch claims an
        # already-synced row.
        reconcile_run_status(self._engine, run_ids=run_ids)
        reconcile_settled_runs(self._engine)

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

        opportunity_claims = grouped.pop(OPPORTUNITY_TAB, [])
        source_claims = grouped.pop(JOB_SOURCE_TAB, [])
        parent_failures: dict[str, GoogleSheetsError] = {}

        if opportunity_claims:
            # Opportunities are dispatched first so a source in the same batch
            # can only be written after its parent is confirmed present.
            opportunity_settled = self._dispatch_tab(
                OPPORTUNITY_TAB,
                opportunity_claims,
            )
            settled.extend(opportunity_settled)
            parent_failures = {
                item.claim.entity_id: item.error
                for item in opportunity_settled
                if not item.synced and item.error is not None
            }

        if source_claims:
            # Google Sheets is canonical and has no foreign key, so a source
            # written before its parent would leave an orphan that blocks the
            # next canonical refresh. Confirm the parent is really there (after
            # any write from this batch) before writing the source.
            try:
                parent_state = self._read_table(OPPORTUNITY_TAB)
            except GoogleSheetsError as error:
                settled.extend(self._settle_failure(claim, error) for claim in source_claims)
                return settled

            known_opportunities = frozenset(
                identity for identity, position in parent_state.positions.items() if position > 0
            )
            parent_rows = {
                identity: row
                for identity, position in parent_state.positions.items()
                if position > 0 and (row := parent_state.row(position)) is not None
            }
            settled.extend(
                self._dispatch_tab(
                    JOB_SOURCE_TAB,
                    source_claims,
                    known_opportunities=known_opportunities,
                    parent_failures=parent_failures,
                    parent_rows=parent_rows,
                )
            )
        return settled

    def _dispatch_tab(
        self,
        tab: str,
        claims: Sequence[OutboxClaim],
        *,
        known_opportunities: frozenset[str] | None = None,
        parent_failures: Mapping[str, GoogleSheetsError] | None = None,
        parent_rows: Mapping[str, Sequence[str]] | None = None,
    ) -> list[_Settled]:
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

            if (
                tab == OPPORTUNITY_TAB
                and row_number is None
                and is_tracker_only_opportunity_payload(payload)
            ):
                # This lookup is a fresh read of the canonical tab, so an absent
                # row means the record was removed there. A tracker-only payload
                # carries no discovery columns, so writing it would publish a
                # blank replacement row. Keep the pending edits and stop until
                # the user restores the row and requeues.
                settled.append(self._block(claim, error_code="canonical_row_missing"))
                continue

            parent_change: ReadModelChange | None = None
            if tab == JOB_SOURCE_TAB:
                parent_id = payload.get("opportunity_id")
                if not isinstance(parent_id, str) or not parent_id.strip():
                    settled.append(self._block(claim, error_code="missing_parent_opportunity"))
                    continue
                if known_opportunities is None or parent_id not in known_opportunities:
                    deferred = (parent_failures or {}).get(parent_id)
                    if deferred is None:
                        # The parent is genuinely absent from the canonical tab.
                        settled.append(self._block(claim, error_code="missing_parent_opportunity"))
                    else:
                        # The parent failed in this same batch. Defer with the
                        # same classification instead of writing an orphan.
                        settled.append(
                            self._settle_failure(
                                claim,
                                GoogleSheetsError(
                                    deferred.code,
                                    "The parent opportunity has not been synchronized yet.",
                                    reconnect_required=deferred.reconnect_required,
                                    retry_after_seconds=deferred.retry_after_seconds,
                                ),
                            )
                        )
                    continue
                parent_change = _canonical_change(
                    tab=OPPORTUNITY_TAB,
                    headers=OPPORTUNITY_HEADERS,
                    row=(parent_rows or {}).get(parent_id, ()),
                )

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

            existing_row_expected = row_number is not None
            if row_number is None:
                row_number = next_row
                next_row += 1
            planned.append(
                _PlannedWrite(
                    claim=claim,
                    row_number=row_number,
                    projection=projection,
                    parent=parent_change,
                    existing_row_expected=existing_row_expected,
                )
            )
        if not planned:
            return settled

        written: list[_PlannedWrite] = []
        pending = list(planned)
        try:
            for start in range(0, len(planned), self._batch_size):
                chunk = planned[start : start + self._batch_size]
                # Revalidate immediately before writing: a manual insert or delete
                # since the read would otherwise send a full row to a stale index.
                fresh, stale, baseline = self._revalidate(tab, chunk)
                for item in stale:
                    settled.append(self._settle_failure(item.claim, _row_moved_error()))
                settled_ids = {item.claim.outbox_id for item in stale}
                pending = [item for item in pending if item.claim.outbox_id not in settled_ids]
                if not fresh:
                    continue
                response = self._write(tab, fresh)
                self._verify_write_count(fresh, response)
                self._verify(baseline=baseline, planned=fresh, tab=tab)
                written.extend(fresh)
                written_ids = {item.claim.outbox_id for item in fresh}
                pending = [item for item in pending if item.claim.outbox_id not in written_ids]
        except GoogleSheetsError as error:
            # ``pending`` holds exactly the claims that were neither written nor
            # already settled as row-moved, so none is marked synced by mistake.
            return [
                *settled,
                *(self._mark_synced(item.claim, item.projection, item.parent) for item in written),
                *(self._settle_failure(item.claim, error) for item in pending),
            ]

        return [
            *settled,
            *(self._mark_synced(item.claim, item.projection, item.parent) for item in written),
        ]

    def _read_table(self, tab: str) -> _TableState:
        headers = TAB_HEADERS[tab]
        last_column = column_letter(len(headers))
        header_row = header_row_for(tab)
        requested = f"{tab}!A{header_row}:{last_column}"
        values = self._client.get_values(self._spreadsheet_id, [requested])
        rows = tuple(tuple(row) for row in values.get(requested, []))
        if not rows:
            raise GoogleSheetsError(
                "schema_mismatch",
                "The required spreadsheet tab has no header row.",
            )

        current_header = tuple(cell for cell in rows[0][: len(headers)])
        if current_header != headers:
            # Writing against a reordered header would silently put values in the
            # wrong columns, so refuse until the user fixes the sheet.
            raise GoogleSheetsError(
                "schema_mismatch",
                f"The {tab} header row does not match the accepted schema.",
            )

        positions: dict[str, int] = {}
        for row_number, row in enumerate(rows[1:], start=header_row + 1):
            identity = row[0] if row else ""
            if not identity:
                continue
            if identity in positions:
                # A negative marker makes the duplicate visible without losing
                # the fact that at least one row exists.
                positions[identity] = -1
                continue
            positions[identity] = row_number
        return _TableState(rows=rows, positions=positions, first_row=header_row)

    def _write(self, tab: str, planned: Sequence[_PlannedWrite]) -> dict[str, Any]:
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

        return self._client.batch_update_values(self._spreadsheet_id, data)

    def _capture_identity_map(self, tab: str) -> Mapping[int, str]:
        """Read the canonical identity column as ``{row_number: identity}``.

        The mapping covers every populated row (the header is skipped). Comparing
        it before and after a write catches a lost or moved identity even when the
        overwrite does not create a duplicate identity.
        """

        header_row = header_row_for(tab)
        identity_range = f"{tab}!A{header_row}:A"
        values = self._client.get_values(self._spreadsheet_id, [identity_range])
        return _parse_identity_map(
            values.get(identity_range, []),
            first_data_row=header_row + 1,
        )

    def _revalidate(
        self,
        tab: str,
        planned: Sequence[_PlannedWrite],
    ) -> tuple[list[_PlannedWrite], list[_PlannedWrite], Mapping[int, str]]:
        """Split a chunk and capture the baseline identity map in one read.

        One batched read of the canonical identity column both checks every
        planned physical row and records the complete pre-write identity map.
        Keeping them in the same read avoids adding another read→write gap just
        to take the baseline.

        An existing row is fresh only if it still holds the claimed identity. A
        planned append row is fresh only if it is still empty. Anything else
        means the plan was built from a stale read, so it must not be written to
        that row. This narrows the window; it does not make the read and write
        atomic.
        """

        if not planned:
            return [], [], {}
        header_row = header_row_for(tab)
        identity_range = f"{tab}!A{header_row}:A"
        values = self._client.get_values(self._spreadsheet_id, [identity_range])
        baseline = _parse_identity_map(
            values.get(identity_range, []),
            first_data_row=header_row + 1,
        )

        fresh: list[_PlannedWrite] = []
        stale: list[_PlannedWrite] = []
        for item in planned:
            assert item.row_number is not None
            current = _identity_at(baseline, item.row_number)
            if item.claim.entity_id == current:
                fresh.append(item)
            elif item.existing_row_expected:
                stale.append(item)
            elif current is None:
                fresh.append(item)
            else:
                stale.append(item)
        return fresh, stale, baseline

    def _verify(
        self,
        *,
        baseline: Mapping[int, str],
        planned: Sequence[_PlannedWrite],
        tab: str,
    ) -> None:
        """Compare the post-write identity map with the expected one.

        Expected = the revalidation baseline plus this chunk's legitimate
        operations. Existing-row updates keep the identity at the same row;
        planned inserts add exactly one identity at their planned row. No planned
        operation deletes an identity: a withdrawal still writes its canonical
        row, only its status changes.

        Any other difference is an integrity anomaly: a known identity vanished,
        moved, appeared twice, appeared out of nowhere, a planned insert landed
        elsewhere, or the maps differ in any other way. The write has already
        committed; this never rolls anything back. Its only job is to prevent an
        uncertain claim from being settled as synchronized.

        Residual limits (this is not atomic and not race-free):

        - Only identities present in the revalidation baseline can be missed. A
          row inserted externally after baseline capture and then overwritten by
          this write was never in the comparison.
        - ``totalUpdatedRows`` (checked by the caller) cannot say which row was
          overwritten; it is supplementary evidence only.
        - The process-local dispatch guard does not block external spreadsheet
          editors or other processes.
        - The map compares identities, not cell values: a write that keeps the
          identity in place but silently fails to update another column is not
          detected here.
        """

        expected = dict(baseline)
        for item in planned:
            assert item.row_number is not None
            identity = item.claim.entity_id
            if item.existing_row_expected:
                if baseline.get(item.row_number) != identity:
                    raise _identity_map_changed()
                continue
            if item.row_number in baseline:
                raise _identity_map_changed()
            if identity in baseline.values():
                # Inserting an identity that already exists elsewhere would
                # create a duplicate the map comparison could otherwise accept.
                raise _identity_map_changed()
            expected[item.row_number] = identity

        actual = self._capture_identity_map(tab)
        if actual != expected:
            raise _identity_map_changed()

    def _verify_write_count(
        self,
        planned: Sequence[_PlannedWrite],
        response: Mapping[str, Any],
    ) -> None:
        """Validate the optional ``totalUpdatedRows`` write response field.

        The official Sheets ``values.batchUpdate`` response supplies it; a simple
        transport may omit it. When absent or not an integer, the count check is
        unavailable and is skipped rather than inventing a value -- the identity
        map comparison remains the primary integrity check. When present, a count
        different from the number of distinct rows in this request means the batch
        may have been applied somewhere unexpected.

        The count cannot identify which row was affected. It is supplementary
        evidence, not a substitute for the identity map.
        """

        reported = response.get("totalUpdatedRows")
        if not isinstance(reported, int) or isinstance(reported, bool):
            return
        expected = len({item.row_number for item in planned})
        if reported != expected:
            raise GoogleSheetsError(
                "canonical_write_count_mismatch",
                "The spreadsheet reported a different number of updated rows "
                "than this batch contained. Manual inspection is required; the "
                "affected work was not marked as synchronized.",
            )

    def _mark_synced(
        self,
        claim: OutboxClaim,
        projection: ProjectedRow | None = None,
        parent: ReadModelChange | None = None,
    ) -> _Settled:
        tab = _tab_for(claim)
        assert tab is not None
        if projection is None:
            # A withdrawal whose remote row is already absent: remove any stale
            # local copy, because there is nothing left to mirror.
            change = ReadModelChange(tab=tab, identity=claim.entity_id, values=None)
        else:
            change = read_model_change(
                tab=projection.tab,
                headers=projection.headers,
                values=projection.values,
            )

        try:
            settled = self._repository.settle_synced(
                claim,
                synced_at=self._clock(),
                read_model=change,
                parents=(parent,) if parent is not None else (),
            )
        except ReadModelSettlementError:
            logger.warning(
                "sync_read_model_settlement_failed",
                extra={"outbox_id": claim.outbox_id, "entity": claim.entity_type},
            )
            return self._settle_failure(
                claim,
                GoogleSheetsError(
                    "read_model_settlement_failed",
                    "The local read model could not be updated with the verified row.",
                ),
            )

        if settled:
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
            return self._block(claim, error_code=error.code, error=error)

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
        return _Settled(claim=claim, synced=False, error_code=error.code, error=error)

    def _block(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        error: GoogleSheetsError | None = None,
    ) -> _Settled:
        self._repository.mark_blocked(
            claim,
            error_code=error_code,
            updated_at=self._clock(),
        )
        return _Settled(claim=claim, synced=False, error_code=error_code, error=error)

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


def _canonical_change(
    *,
    tab: str,
    headers: Sequence[str],
    row: Sequence[str],
) -> ReadModelChange | None:
    """Build a read-model change from a canonical row already read from Sheets.

    Used only to mirror a parent row that the local cache is missing. Google omits
    trailing empty cells, so the row is padded to the canonical width first.
    """

    if not row:
        return None
    cells = list(row[: len(headers)])
    cells.extend([""] * (len(headers) - len(cells)))
    return read_model_change(tab=tab, headers=headers, values=cells)


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_BATCH_SIZE",
    "PERMANENT_ERROR_CODES",
    "RETRY_AFTER_CAP_SECONDS",
    "SyncAlreadyRunning",
    "SyncDispatcher",
    "SyncOutcome",
]
