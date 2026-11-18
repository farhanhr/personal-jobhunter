"""Persistence primitives for the SQLite read model.

The read model is a rebuildable cache of the canonical Google Sheets tabs
(ADR-015). This module owns the two writes a refresh needs:

- :meth:`ReadModelRepository.replace_snapshot` publishes a complete snapshot.
- :meth:`ReadModelRepository.record_refresh_failure` records a failed attempt.

Both run in one short ``BEGIN IMMEDIATE`` transaction, and the snapshot write
never spans a network call: the caller fetches from Sheets first, then hands the
already-converted rows here. A failure therefore rolls back to the previous
snapshot, so the last known good data and its
``last_full_refresh_at_utc`` survive a partial or failed refresh.

The caller (the M4C2 refresh service, not this module) owns the remaining
guarantees: it must read every required tab and validate required values before
publishing, must pass each canonical column (including user-owned
``user_status`` and the application tracker fields) so a blank cell stays an
explicit blank, and must serialize overlapping refreshes the way the M4B3 manual
sync serializes dispatches.

Contacts is deferred from MVP (ADR-021). No snapshot argument, count, or lookup
depends on a Contacts tab or table.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import (
    AVAILABILITY_STATUS_VALUES,
    LOCATION_STATUS_VALUES,
    OBSERVATION_STATUS_VALUES,
    PROVENANCE_METHOD_VALUES,
    READ_MODEL_STATE_ID,
    REMOTE_SCOPE_VALUES,
    ROLE_MATCH_TYPE_VALUES,
    SOURCE_TYPE_VALUES,
    USER_STATUS_VALUES,
    WORK_MODE_VALUES,
    job_sources_read_model,
    opportunities_read_model,
    sheets_sync_state,
)

REFRESH_SUCCEEDED = "succeeded"
REFRESH_FAILED = "failed"

OPPORTUNITY_TAB = "Opportunities"
JOB_SOURCE_TAB = "Job_Sources"

_READ_MODEL_TABLES = {
    OPPORTUNITY_TAB: opportunities_read_model,
    JOB_SOURCE_TAB: job_sources_read_model,
}
_IDENTITY_COLUMNS = {
    OPPORTUNITY_TAB: "opportunity_id",
    JOB_SOURCE_TAB: "source_id",
}
_ENUM_COLUMNS = {
    OPPORTUNITY_TAB: {
        "role_match_type": ROLE_MATCH_TYPE_VALUES,
        "location_status": LOCATION_STATUS_VALUES,
        "work_mode": WORK_MODE_VALUES,
        "remote_scope": REMOTE_SCOPE_VALUES,
        "availability_status": AVAILABILITY_STATUS_VALUES,
        "user_status": USER_STATUS_VALUES,
    },
    JOB_SOURCE_TAB: {
        "source_type": SOURCE_TYPE_VALUES,
        "provenance_method": PROVENANCE_METHOD_VALUES,
        "observation_status": OBSERVATION_STATUS_VALUES,
    },
}

# Failure codes are short, sanitized identifiers (never provider text, paths, or
# tokens), so they are safe to persist and later expose in the dashboard.
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SnapshotWriteError(Exception):
    """Raised when a snapshot cannot be published and was fully rolled back."""


class SnapshotValidationError(SnapshotWriteError):
    """Raised before any write when a snapshot row violates the table contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ReadModelSettlementError(Exception):
    """Raised when a fenced settlement could not also update the read model."""


@dataclass(frozen=True, slots=True)
class ReadModelChange:
    """One row-level read-model change applied inside an existing transaction.

    ``values`` is ``None`` for a withdrawal, meaning the remote record no longer
    exists and the local row must be removed. Otherwise every column must be
    present, including explicit blanks, so an update cannot leave a stale value.
    """

    tab: str
    identity: str
    values: Mapping[str, str | None] | None


def read_model_change(
    *,
    tab: str,
    headers: Sequence[str],
    values: Sequence[str],
) -> ReadModelChange:
    """Translate one verified projection row into a read-model change.

    The projection headers are the canonical sheet headers, which mirror the
    read-model columns exactly. Reusing that single mapping (instead of a second
    hand-written map) keeps settlement and refresh aligned. A blank cell becomes
    SQL ``NULL``; it is never dropped, so clearing a tracker field clears the
    local value too.
    """

    table = _READ_MODEL_TABLES.get(tab)
    identity_column = _IDENTITY_COLUMNS.get(tab)
    if table is None or identity_column is None:
        raise ValueError("Unknown read-model tab.")

    columns = tuple(table.c.keys())
    if tuple(headers) != columns:
        raise ValueError("Projection headers do not match the read-model columns.")
    if len(values) != len(columns):
        raise ValueError("Projection values do not match the read-model columns.")

    row = {
        header: (None if value == "" else value)
        for header, value in zip(headers, values, strict=True)
    }
    identity = row[identity_column]
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("A read-model change requires a stable identity.")
    return ReadModelChange(tab=tab, identity=identity, values=row)


def apply_read_model_change(connection: Any, change: ReadModelChange) -> None:
    """Apply one change using the caller's transaction.

    An update is attempted first so an opportunity update never cascades into its
    job-source rows. A missing row is inserted. Withdrawals delete by identity and
    are idempotent when the remote row was already absent.
    """

    table = _READ_MODEL_TABLES.get(change.tab)
    identity_column = _IDENTITY_COLUMNS.get(change.tab)
    if table is None or identity_column is None:
        raise ValueError("Unknown read-model tab.")

    column = table.c[identity_column]
    if change.values is None:
        connection.execute(table.delete().where(column == change.identity))
        return

    existing = connection.execute(select(column).where(column == change.identity)).first()
    if existing is None:
        connection.execute(table.insert().values(**dict(change.values)))
        return
    connection.execute(update(table).where(column == change.identity).values(**dict(change.values)))


def find_read_model_opportunity(
    connection: Any,
    *,
    opportunity_id: str,
) -> dict[str, Any] | None:
    """Return one cached canonical opportunity row, or ``None`` when unknown."""

    row = (
        connection.execute(
            select(opportunities_read_model).where(
                opportunities_read_model.c.opportunity_id == opportunity_id
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


def read_model_row_exists(connection: Any, *, tab: str, identity: str) -> bool:
    """Report whether one read-model row is already cached."""

    table = _READ_MODEL_TABLES.get(tab)
    identity_column = _IDENTITY_COLUMNS.get(tab)
    if table is None or identity_column is None:
        raise ValueError("Unknown read-model tab.")

    column = table.c[identity_column]
    return connection.execute(select(column).where(column == identity)).first() is not None


class ReadModelRepository:
    """Read and write the local projection of the canonical Sheets tabs."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ writes

    def replace_snapshot(
        self,
        *,
        opportunities: Sequence[dict[str, Any]],
        job_sources: Sequence[dict[str, Any]],
        refreshed_at: datetime,
        spreadsheet_schema_version: str | None = None,
    ) -> None:
        """Publish a complete snapshot, or change nothing at all.

        Every row is validated before the delete/insert transaction starts, so a
        row with missing or unknown keys, a blank or duplicate identity, an
        unknown foreign key, an invalid enum value, or a non-text value rejects
        the whole snapshot and leaves the previous one untouched. Rows are then
        deleted and re-inserted in foreign-key order inside one transaction, so
        readers never observe a half-published snapshot. Omitting the schema
        version keeps the stored one rather than clearing it.
        """

        opportunity_ids = _validate_snapshot(
            tab=OPPORTUNITY_TAB,
            rows=opportunities,
        )
        _validate_snapshot(
            tab=JOB_SOURCE_TAB,
            rows=job_sources,
            required_columns=("opportunity_id",),
            reference_column="opportunity_id",
            known_references=opportunity_ids,
        )

        refreshed_iso = format_utc(refreshed_at)
        version: object = (
            _UNCHANGED if spreadsheet_schema_version is None else spreadsheet_schema_version
        )
        try:
            with immediate_transaction(self._engine) as connection:
                connection.execute(job_sources_read_model.delete())
                connection.execute(opportunities_read_model.delete())

                if opportunities:
                    connection.execute(
                        opportunities_read_model.insert(),
                        list(opportunities),
                    )
                if job_sources:
                    connection.execute(
                        job_sources_read_model.insert(),
                        list(job_sources),
                    )

                self._upsert_state(
                    connection,
                    last_full_refresh_at_utc=refreshed_iso,
                    last_attempt_at_utc=refreshed_iso,
                    last_refresh_outcome=REFRESH_SUCCEEDED,
                    last_refresh_error_code=None,
                    spreadsheet_schema_version=version,
                    updated_at_utc=refreshed_iso,
                )
        except SQLAlchemyError as error:
            raise SnapshotWriteError(
                "The read-model snapshot was rejected and rolled back."
            ) from error

    def record_refresh_failure(
        self,
        *,
        attempted_at: datetime,
        error_code: str,
    ) -> None:
        """Record a failed attempt without touching the published rows.

        ``last_full_refresh_at_utc`` is deliberately left unchanged, so stale data
        cannot masquerade as freshly refreshed. The error code must be a short
        sanitized identifier; raw exception text is rejected so provider bodies,
        paths, or tokens can never reach the stored state.
        """

        if not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("error_code must be a snake_case identifier of 64 characters or fewer")

        attempted_iso = format_utc(attempted_at)
        try:
            with immediate_transaction(self._engine) as connection:
                self._upsert_state(
                    connection,
                    last_full_refresh_at_utc=_UNCHANGED,
                    last_attempt_at_utc=attempted_iso,
                    last_refresh_outcome=REFRESH_FAILED,
                    last_refresh_error_code=error_code,
                    spreadsheet_schema_version=_UNCHANGED,
                    updated_at_utc=attempted_iso,
                )
        except SQLAlchemyError as error:
            raise SnapshotWriteError(
                "The refresh failure could not be recorded and was rolled back."
            ) from error

    def _upsert_state(
        self,
        connection: Any,
        *,
        last_full_refresh_at_utc: object,
        last_attempt_at_utc: str,
        last_refresh_outcome: str,
        last_refresh_error_code: str | None,
        spreadsheet_schema_version: object,
        updated_at_utc: str,
    ) -> None:
        existing = (
            connection.execute(
                select(sheets_sync_state).where(sheets_sync_state.c.state_id == READ_MODEL_STATE_ID)
            )
            .mappings()
            .one_or_none()
        )

        values: dict[str, Any] = {
            "last_attempt_at_utc": last_attempt_at_utc,
            "last_refresh_outcome": last_refresh_outcome,
            "last_refresh_error_code": last_refresh_error_code,
            "updated_at_utc": updated_at_utc,
        }
        if last_full_refresh_at_utc is not _UNCHANGED:
            values["last_full_refresh_at_utc"] = last_full_refresh_at_utc
        if spreadsheet_schema_version is not _UNCHANGED:
            values["spreadsheet_schema_version"] = spreadsheet_schema_version

        if existing is None:
            values["state_id"] = READ_MODEL_STATE_ID
            connection.execute(sheets_sync_state.insert().values(**values))
            return

        connection.execute(
            sheets_sync_state.update()
            .where(sheets_sync_state.c.state_id == READ_MODEL_STATE_ID)
            .values(**values)
        )

    # ------------------------------------------------------------------- reads

    def get_sync_state(self) -> dict[str, Any] | None:
        """Return the refresh state row, or ``None`` before the first attempt."""

        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(sheets_sync_state).where(
                        sheets_sync_state.c.state_id == READ_MODEL_STATE_ID
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    def snapshot_counts(self) -> dict[str, int]:
        """Count projected rows per table, for refresh results and aggregates."""

        with self._engine.connect() as connection:
            return {
                "opportunities": int(
                    connection.execute(
                        select(func.count()).select_from(opportunities_read_model)
                    ).scalar_one()
                ),
                "job_sources": int(
                    connection.execute(
                        select(func.count()).select_from(job_sources_read_model)
                    ).scalar_one()
                ),
            }

    def find_job_sources_by_external_identity(
        self,
        *,
        source_type: str,
        external_source_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return every source matching ``(source_type, external_source_id)``.

        This is a non-unique lookup (ADR-022), not an identity. Zero matches
        means no candidate, one match is a dedup candidate, and multiple matches
        are ambiguous and require review. Results are ordered by ``source_id`` so
        read-only display and tests stay deterministic. A missing external id
        cannot identify anything, so it never matches.
        """

        if not external_source_id:
            return []
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(job_sources_read_model)
                    .where(
                        job_sources_read_model.c.source_type == source_type,
                        job_sources_read_model.c.external_source_id == external_source_id,
                    )
                    .order_by(job_sources_read_model.c.source_id)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def find_job_sources_by_url(self, *, source_url: str) -> list[dict[str, Any]]:
        """Return every source matching a canonical URL.

        ``source_url`` is a non-unique fallback attribute, never an identity. A
        single match may be a dedup candidate; multiple matches are ambiguous and
        must not be merged arbitrarily.
        """

        if not source_url:
            return []
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(job_sources_read_model)
                    .where(job_sources_read_model.c.source_url == source_url)
                    .order_by(job_sources_read_model.c.source_id)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]


def _validate_snapshot(
    *,
    tab: str,
    rows: Sequence[Mapping[str, Any]],
    required_columns: Sequence[str] = (),
    reference_column: str | None = None,
    known_references: set[str] | None = None,
) -> set[str]:
    """Validate every row against the read-model table contract.

    Returns the set of identities so the caller can validate child references.
    Error messages name the tab, row position, and column only; canonical values
    are user data and never travel into a failure code or API response.
    """

    table = _READ_MODEL_TABLES[tab]
    identity_column = _IDENTITY_COLUMNS[tab]
    columns = tuple(table.c.keys())
    expected = set(columns)
    enum_columns = _ENUM_COLUMNS[tab]
    identities: set[str] = set()

    for position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SnapshotValidationError(
                "snapshot_invalid_row",
                f"{tab} row {position} is not an object.",
            )

        keys = set(row)
        missing = expected - keys
        if missing:
            raise SnapshotValidationError(
                "snapshot_missing_columns",
                f"{tab} row {position} is missing a required column.",
            )
        unknown = keys - expected
        if unknown:
            raise SnapshotValidationError(
                "snapshot_unknown_columns",
                f"{tab} row {position} has a column that is not part of the table.",
            )

        identity = row[identity_column]
        if not isinstance(identity, str) or not identity.strip():
            raise SnapshotValidationError(
                "snapshot_missing_identity",
                f"{tab} row {position} has no stable identity.",
            )
        if identity in identities:
            raise SnapshotValidationError(
                "snapshot_duplicate_identity",
                f"{tab} row {position} repeats an earlier stable identity.",
            )
        identities.add(identity)

        for column in columns:
            value = row[column]
            if value is not None and not isinstance(value, str):
                raise SnapshotValidationError(
                    "snapshot_invalid_value_type",
                    f"{tab} row {position} has a non-text value in one column.",
                )

        for column in required_columns:
            value = row[column]
            if not isinstance(value, str) or not value.strip():
                raise SnapshotValidationError(
                    "snapshot_missing_reference",
                    f"{tab} row {position} has no required reference.",
                )

        for column, allowed in enum_columns.items():
            value = row[column]
            if value is not None and value not in allowed:
                raise SnapshotValidationError(
                    "snapshot_invalid_enum",
                    f"{tab} row {position} has an unaccepted value in one column.",
                )

        if reference_column is not None and known_references is not None:
            reference = row[reference_column]
            if reference not in known_references:
                raise SnapshotValidationError(
                    "snapshot_unknown_reference",
                    f"{tab} row {position} points at a record outside this snapshot.",
                )

    return identities


class _Unchanged:
    """Sentinel meaning "leave the stored value alone"."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNCHANGED"


_UNCHANGED = _Unchanged()
