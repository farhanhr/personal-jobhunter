"""Reconcile run status once its synchronization duty is finished.

A run reports ``completed_pending_sync`` while any projection it observed is still
outstanding. This module decides the final status when that work is done, using
durable run references rather than the mutable ``sync_outbox.run_id`` column, so a
later run cannot erase an earlier run's obligation.

Connector failures are never discarded: a run whose sources partly failed becomes
``completed_with_warnings`` instead of a clean ``completed``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, select, update

from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import opportunity_run_refs, search_runs
from personal_job_finder_api.domain.run_lifecycle import resolve_completion_status
from personal_job_finder_api.domain.search_policy import RunStatus
from personal_job_finder_api.repositories.opportunities import (
    OpportunityStagingRepository,
)

PENDING_SYNC_STATUS = RunStatus.COMPLETED_PENDING_SYNC.value
COMPLETED_SOURCE_STATUS = "completed"


def reconcile_run_status(
    engine: Engine,
    *,
    run_ids: Sequence[str],
) -> tuple[tuple[str, RunStatus], ...]:
    """Resolve every pending run in ``run_ids``; return the ones that changed."""

    repository = OpportunityStagingRepository()
    changed: list[tuple[str, RunStatus]] = []

    with immediate_transaction(engine) as connection:
        for run_id in dict.fromkeys(run_ids):
            row = (
                connection.execute(select(search_runs).where(search_runs.c.run_id == run_id))
                .mappings()
                .one_or_none()
            )
            if row is None or row["status"] != PENDING_SYNC_STATUS:
                continue

            pending = repository.count_unsynced_for_run(connection, run_id)
            if pending > 0:
                continue

            summaries = _source_summaries(row["source_summary_json"])
            total_sources = len(summaries)
            failed_sources = sum(
                1 for summary in summaries if summary.get("status") != COMPLETED_SOURCE_STATUS
            )

            status = resolve_completion_status(
                pending_sync_count=0,
                failed_sources=failed_sources,
                total_sources=total_sources,
            )
            if status is RunStatus.FAILED:
                # Every connector failed yet syncable records existed. That is
                # contradictory data, so keep the recorded status instead of
                # inventing a terminal failure.
                continue

            connection.execute(
                update(search_runs)
                .where(search_runs.c.run_id == run_id)
                .values(
                    status=status.value,
                    progress_json=_with_zero_pending(row["progress_json"]),
                )
            )
            changed.append((run_id, status))

    return tuple(changed)


def runs_observing(
    engine: Engine,
    *,
    opportunity_ids: Sequence[str],
    source_ids: Sequence[str],
) -> tuple[str, ...]:
    """Return every run that observed one of the given records."""

    opportunities = {value for value in opportunity_ids if value}
    sources = {value for value in source_ids if value}
    if not opportunities and not sources:
        return ()

    conditions = []
    if opportunities:
        conditions.append(opportunity_run_refs.c.opportunity_id.in_(opportunities))
    if sources:
        conditions.append(opportunity_run_refs.c.source_id.in_(sources))

    statement = select(opportunity_run_refs.c.run_id).where(*conditions).distinct()
    with engine.connect() as connection:
        return tuple(str(run_id) for run_id in connection.execute(statement).scalars().all())


def _source_summaries(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _with_zero_pending(raw: str | None) -> str | None:
    if not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(parsed, dict):
        return raw
    parsed["pending_sync"] = 0
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
