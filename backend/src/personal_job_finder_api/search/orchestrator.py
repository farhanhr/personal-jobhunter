"""In-process search-run orchestration.

The orchestrator owns lifecycle transitions only. It claims an accepted run, runs
connectors outside any database transaction, and persists a sanitized terminal
summary. It never refunds quota, never retries automatically, and never resumes a
run after a process restart.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.domain.search_policy import RunStatus
from personal_job_finder_api.repositories.search_policy import (
    PersistedSearchRun,
    SearchPolicyRepository,
)
from personal_job_finder_api.search.connectors import (
    ConnectorFactory,
    SourceConnector,
    SourceRunResult,
    SourceRunStatus,
    connector_snapshot,
)
from personal_job_finder_api.search.staging import (
    OpportunityStagingService,
    StagingSummary,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SOURCE_REQUESTS = 2
NO_CONNECTOR_SUMMARY = "No connector implementation is available for the configured sources."
ALL_SOURCES_FAILED_SUMMARY = "All configured sources failed; the accepted run remains consumed."
CONNECTOR_INIT_FAILED_SUMMARY = (
    "Connector initialization failed; the accepted run remains consumed."
)
STAGING_FAILED_SUMMARY = (
    "Search results could not be stored durably; the accepted run remains consumed."
)


class SearchOrchestrator:
    """Execute one accepted run against a snapshot of configured connectors."""

    def __init__(
        self,
        engine: Engine,
        *,
        connector_factory: ConnectorFactory,
        clock: Callable[[], datetime] = utc_now,
        staging: OpportunityStagingService | None = None,
    ) -> None:
        self._engine = engine
        self._connector_factory = connector_factory
        self._clock = clock
        self._repository = SearchPolicyRepository()
        self._staging = staging or OpportunityStagingService(engine, clock=clock)

    def run(self, run_id: str) -> RunStatus | None:
        """Execute an accepted run once; return its terminal status or None.

        ``None`` means the run was missing or already claimed/terminal, so this
        call deliberately did nothing.
        """

        run = self._claim(run_id)
        if run is None:
            return None

        try:
            snapshot = connector_snapshot(run.config_snapshot)
            connectors: Sequence[SourceConnector] = list(self._connector_factory(snapshot))
        except Exception:
            return self._finish(
                run.run_id,
                status=RunStatus.FAILED,
                results=[],
                stage="failed",
                error_summary=CONNECTOR_INIT_FAILED_SUMMARY,
            )

        if not connectors:
            return self._finish(
                run.run_id,
                status=RunStatus.FAILED,
                results=[],
                stage="failed",
                error_summary=NO_CONNECTOR_SUMMARY,
            )

        results = self._execute(connectors)

        # Records must be durable before the run can report success, so staging
        # happens before the terminal transition and outside the run transaction.
        try:
            staging_summary = self._staging.stage(
                run_id=run.run_id,
                records=[record for result in results for record in result.records],
            )
        except Exception:
            logger.warning(
                "Search run %s could not stage its records; marking it failed.",
                run.run_id,
            )
            return self._finish(
                run.run_id,
                status=RunStatus.FAILED,
                results=results,
                stage="failed",
                error_summary=STAGING_FAILED_SUMMARY,
            )

        succeeded = [result for result in results if result.status is SourceRunStatus.COMPLETED]
        failed_count = len(results) - len(succeeded)

        if failed_count == len(results):
            status = RunStatus.FAILED
            error_summary: str | None = ALL_SOURCES_FAILED_SUMMARY
        elif failed_count:
            status = RunStatus.COMPLETED_WITH_WARNINGS
            error_summary = (
                f"{failed_count} of {len(results)} configured sources failed; "
                "review the source summary."
            )
        elif staging_summary.outbox_enqueued > 0:
            # Google Sheets transport is not implemented yet, so staged records
            # are durable but not canonical. Report that honestly rather than
            # claiming a completed sync.
            status = RunStatus.COMPLETED_PENDING_SYNC
            error_summary = None
        else:
            status = RunStatus.COMPLETED
            error_summary = None

        return self._finish(
            run.run_id,
            status=status,
            results=results,
            stage="completed",
            error_summary=error_summary,
            staging_summary=staging_summary,
        )

    def _claim(self, run_id: str) -> PersistedSearchRun | None:
        started_at = ensure_aware_utc(self._clock(), "clock")
        with immediate_transaction(self._engine) as connection:
            run = self._repository.get_run(connection, run_id)
            if run is None or run.status is not RunStatus.ACCEPTED:
                return None
            claimed = self._repository.mark_running(
                connection,
                run.run_id,
                started_at=started_at,
            )
            return run if claimed else None

    def _execute(self, connectors: Sequence[SourceConnector]) -> list[SourceRunResult]:
        worker_count = min(MAX_CONCURRENT_SOURCE_REQUESTS, len(connectors))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="search-source",
        ) as executor:
            return list(executor.map(_execute_connector, connectors))

    def _finish(
        self,
        run_id: str,
        *,
        status: RunStatus,
        results: list[SourceRunResult],
        stage: str,
        error_summary: str | None,
        staging_summary: StagingSummary | None = None,
    ) -> RunStatus:
        progress: dict[str, Any] = {
            "stage": stage,
            "completed_sources": sum(
                1 for result in results if result.status is SourceRunStatus.COMPLETED
            ),
            "total_sources": len(results),
        }
        if staging_summary is not None:
            progress.update(
                {
                    "records_staged": staging_summary.records_received,
                    "records_new": staging_summary.records_new,
                    "records_updated": staging_summary.records_updated,
                    "records_unchanged": staging_summary.records_unchanged,
                    "pending_sync": staging_summary.outbox_enqueued,
                }
            )
        summaries: list[dict[str, Any]] = [result.to_summary() for result in results]
        finished_at = ensure_aware_utc(self._clock(), "clock")

        with immediate_transaction(self._engine) as connection:
            self._repository.finish_run(
                connection,
                run_id,
                status=status,
                finished_at=finished_at,
                error_summary=error_summary,
                progress_json=json.dumps(progress, sort_keys=True, separators=(",", ":")),
                source_summary_json=json.dumps(
                    summaries,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            )
        return status


def _execute_connector(connector: SourceConnector) -> SourceRunResult:
    try:
        result = connector.run()
        return SourceRunResult(
            source_id=str(result.source_id),
            status=SourceRunStatus(result.status),
            records_found=max(0, int(result.records_found)),
            error_code=result.error_code,
            records_relevant=result.records_relevant,
            records_filtered=result.records_filtered,
            records_invalid=result.records_invalid,
            records=tuple(result.records),
        )
    except Exception:
        return SourceRunResult(
            source_id=str(getattr(connector, "source_id", "unknown_source")),
            status=SourceRunStatus.FAILED,
            records_found=0,
            error_code="connector_error",
        )
