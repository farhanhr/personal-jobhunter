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
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import (
    READ_MODEL_STATE_ID,
    job_sources_read_model,
    opportunities_read_model,
    sheets_sync_state,
)

REFRESH_SUCCEEDED = "succeeded"
REFRESH_FAILED = "failed"

# Failure codes are short, sanitized identifiers (never provider text, paths, or
# tokens), so they are safe to persist and later expose in the dashboard.
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SnapshotWriteError(Exception):
    """Raised when a snapshot cannot be published and was fully rolled back."""


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

        Rows are deleted and re-inserted inside one transaction, in foreign-key
        order, so readers never observe a half-published snapshot and a rejected
        row leaves the previous snapshot intact. Omitting the schema version
        keeps the stored one rather than clearing it.
        """

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


class _Unchanged:
    """Sentinel meaning "leave the stored value alone"."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNCHANGED"


_UNCHANGED = _Unchanged()
