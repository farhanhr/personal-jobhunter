"""Explicit, serialized refresh of the canonical Sheets tabs into SQLite.

The read order is deliberate: validate local configuration, run the read-only
spreadsheet preflight for both required tabs, read both tabs in one bounded
batch, convert every row against the canonical headers, then publish one atomic
snapshot. No Google request runs inside the snapshot transaction, and a rejected
row never replaces the last known good snapshot.

``Contacts`` is not a required tab (ADR-021): a spreadsheet without it refreshes
successfully. The caller owns the process lock wait policy; this service only
serializes the read/publication window and records the outcome.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, utc_now
from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleSheetsClient,
    SheetsTransport,
    column_letter,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    DEFAULT_MAX_BATCH_RANGES,
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    REQUIRED_TABS,
    header_row_for,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.preflight import (
    SpreadsheetPreflightService,
)
from personal_job_finder_api.repositories.read_model import (
    JOB_SOURCE_TAB,
    OPPORTUNITY_TAB,
    ReadModelRepository,
    SnapshotValidationError,
    SnapshotWriteError,
)
from personal_job_finder_api.sync.coordination import (
    SheetsOperationBusy,
    acquire_sheets_io,
    release_sheets_io,
)
from personal_job_finder_api.sync.service import SyncConfigurationError

logger = logging.getLogger(__name__)

# Identifies the canonical header contract stored alongside the snapshot. Bump
# only when the canonical 33/13-column contract changes.
CANONICAL_SCHEMA_VERSION = "1"

TransportFactory = Callable[[], SheetsTransport]
SpreadsheetIdResolver = Callable[[], str]


class RefreshError(Exception):
    """A sanitized refresh failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RefreshAlreadyRunning(RefreshError):
    """Another refresh or synchronization already holds the Sheets I/O lock."""


class RefreshConfigurationError(RefreshError):
    """The local spreadsheet configuration is missing or invalid."""


class RefreshSchemaError(RefreshError):
    """A required tab or the canonical header row is missing or mismatched."""


class RefreshSnapshotError(RefreshError):
    """The converted snapshot violates the read-model contract."""


class RefreshUnavailableError(RefreshError):
    """Google Sheets could not be read; no snapshot was published."""


class RefreshStorageError(RefreshError):
    """The SQLite snapshot transaction could not be committed."""


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """The outcome of one successful refresh."""

    refreshed_at: datetime
    counts: dict[str, int]
    last_full_refresh_at_utc: datetime | None


class SheetsRefreshService:
    """Read both canonical tabs and publish one validated snapshot."""

    def __init__(
        self,
        engine: Engine,
        *,
        transport_factory: TransportFactory,
        spreadsheet_id: SpreadsheetIdResolver,
        clock: Callable[[], datetime] = utc_now,
        max_batch_ranges: int = DEFAULT_MAX_BATCH_RANGES,
    ) -> None:
        self._repository = ReadModelRepository(engine)
        self._transport_factory = transport_factory
        self._spreadsheet_id = spreadsheet_id
        self._clock = clock
        self._max_batch_ranges = max_batch_ranges

    def refresh(self, *, wait_seconds: float = 0.0) -> RefreshResult:
        """Refresh under the shared Sheets I/O lock.

        ``wait_seconds`` is zero for the manual endpoint, which fails fast on a
        concurrent operation. The pre-reservation caller may wait briefly so a
        start request is not rejected by a momentary overlap.
        """

        attempted_at = ensure_aware_utc(self._clock(), "clock")

        try:
            acquire_sheets_io(wait_seconds=wait_seconds)
        except SheetsOperationBusy:
            raise RefreshAlreadyRunning(
                "refresh_in_progress",
                "A Google Sheets refresh or synchronization is already running. "
                "Wait for it to finish before retrying.",
            ) from None

        try:
            return self._refresh_locked(attempted_at)
        except RefreshError as error:
            self._record_failure(attempted_at, error.code)
            raise
        except SnapshotValidationError as error:
            self._record_failure(attempted_at, error.code)
            raise RefreshSnapshotError(error.code, str(error)) from None
        except SnapshotWriteError:
            self._record_failure(attempted_at, "snapshot_write_failed")
            raise RefreshStorageError(
                "snapshot_write_failed",
                "The read-model snapshot could not be committed.",
            ) from None
        except GoogleSheetsError as error:
            self._record_failure(attempted_at, error.code)
            raise RefreshUnavailableError(
                error.code,
                "Google Sheets could not be read. No snapshot was published.",
            ) from None
        except Exception:
            # A transport factory or other runtime failure must still produce a
            # recorded attempt and a sanitized error. Log locally (no response
            # text or state value uses the exception), then replace it.
            logger.exception("sheets_refresh_unexpected_error")
            self._record_failure(attempted_at, "refresh_unexpected_error")
            raise RefreshUnavailableError(
                "refresh_unexpected_error",
                "The refresh could not be completed. No snapshot was published.",
            ) from None
        finally:
            release_sheets_io()

    def _refresh_locked(self, attempted_at: datetime) -> RefreshResult:
        try:
            spreadsheet_id = self._spreadsheet_id()
        except SyncConfigurationError as error:
            raise RefreshConfigurationError(error.code, str(error)) from None

        transport = self._transport_factory()
        client = GoogleSheetsClient(
            transport,
            max_batch_ranges=self._max_batch_ranges,
        )
        preflight = SpreadsheetPreflightService(transport, client=client)
        report = preflight.check(spreadsheet_id)

        if report.missing_tabs:
            raise RefreshSchemaError(
                "missing_tab",
                "A required canonical tab is missing from the spreadsheet.",
            )
        for tab, expected_headers in REQUIRED_TABS.items():
            schema = report.tab(tab)
            if schema.headers != expected_headers:
                raise RefreshSchemaError(
                    "schema_mismatch",
                    f"The {tab} header row does not match the canonical contract.",
                )

        targets = [
            (tab, f"{tab}!A{header_row_for(tab)}:{column_letter(len(headers))}")
            for tab, headers in REQUIRED_TABS.items()
        ]
        ranges = [range_name for _, range_name in targets]
        values = client.get_values(spreadsheet_id, ranges)

        opportunities = _convert_tab(
            tab=OPPORTUNITY_TAB,
            headers=OPPORTUNITY_HEADERS,
            raw_rows=values.get(targets[0][1], []),
        )
        job_sources = _convert_tab(
            tab=JOB_SOURCE_TAB,
            headers=JOB_SOURCE_HEADERS,
            raw_rows=values.get(targets[1][1], []),
        )

        self._repository.replace_snapshot(
            opportunities=opportunities,
            job_sources=job_sources,
            refreshed_at=attempted_at,
            spreadsheet_schema_version=CANONICAL_SCHEMA_VERSION,
        )

        return RefreshResult(
            refreshed_at=attempted_at,
            counts={
                "opportunities": len(opportunities),
                "job_sources": len(job_sources),
            },
            last_full_refresh_at_utc=attempted_at,
        )

    def _record_failure(self, attempted_at: datetime, error_code: str) -> None:
        try:
            self._repository.record_refresh_failure(
                attempted_at=attempted_at,
                error_code=error_code,
            )
        except Exception:
            # The published snapshot is untouched either way; a state-write
            # failure must not hide the sanitized refresh failure.
            logger.warning("sheets_refresh_state_write_failed", exc_info=True)


def _convert_tab(
    *,
    tab: str,
    headers: Sequence[str],
    raw_rows: Sequence[Sequence[str]],
) -> list[dict[str, str | None]]:
    """Convert one tab into complete canonical dictionaries.

    Google omits trailing empty cells, so every row is padded to the canonical
    width. A fully empty row is skipped; a row with data but no stable identity
    rejects the whole snapshot. Values stay text, and an empty cell becomes
    ``None`` so the nullable read-model columns and enum CHECK constraints keep
    their meaning without inventing a default for tracker fields.
    """

    if not raw_rows:
        raise RefreshSchemaError(
            "schema_mismatch",
            f"The {tab} tab has no header row.",
        )

    header_row = _trim_trailing_blanks(raw_rows[0])
    if tuple(header_row) != tuple(headers):
        raise RefreshSchemaError(
            "schema_mismatch",
            f"The {tab} header row does not match the canonical contract.",
        )

    # ``raw_rows`` starts at the tab's canonical header row, so ``enumerate``
    # reports positions relative to that header, which is what the error text
    # names. Rows above the header (the KPI strip) were never read.
    converted: list[dict[str, str | None]] = []
    for position, raw_row in enumerate(raw_rows[1:], start=2):
        cells = list(raw_row[: len(headers)])
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))

        if not any(cells):
            continue
        if not cells[0].strip():
            raise SnapshotValidationError(
                "snapshot_missing_identity",
                f"{tab} row {position} contains data but no stable identity.",
            )

        converted.append(
            {
                header: (None if cell == "" else cell)
                for header, cell in zip(headers, cells, strict=True)
            }
        )
    return converted


def _trim_trailing_blanks(row: Sequence[str]) -> list[str]:
    cells = list(row)
    while cells and cells[-1] == "":
        cells.pop()
    return cells
