"""Startup recovery for runs left active by a previous process."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, update

from personal_job_finder_api.core.time import ensure_aware_utc, format_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import search_runs
from personal_job_finder_api.domain.search_policy import ACTIVE_RUN_STATUSES, RunStatus

DEFAULT_INTERRUPTED_REASON = (
    "Backend restarted before the run reached a terminal state; "
    "the run was not resumed and its accepted quota remains consumed."
)


def recover_interrupted_runs(
    engine: Engine,
    *,
    now: datetime | None = None,
    reason: str = DEFAULT_INTERRUPTED_REASON,
) -> int:
    """Mark accepted/running rows interrupted and return the affected count."""

    recovery_time = format_utc(ensure_aware_utc(now or utc_now(), "now"))
    active_statuses = tuple(status.value for status in ACTIVE_RUN_STATUSES)

    with immediate_transaction(engine) as connection:
        result = connection.execute(
            update(search_runs)
            .where(search_runs.c.status.in_(active_statuses))
            .values(
                status=RunStatus.INTERRUPTED.value,
                finished_at_utc=recovery_time,
                error_summary=reason,
            )
        )
        return max(int(result.rowcount or 0), 0)
