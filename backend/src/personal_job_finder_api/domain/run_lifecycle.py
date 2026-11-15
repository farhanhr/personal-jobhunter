"""Terminal status rules for a search run.

The externally visible status must not claim completion while durable search
results still need to reach the canonical Google Sheets store. Source warnings
remain in the run summary and become visible again only after synchronization
finishes.
"""

from __future__ import annotations

from personal_job_finder_api.domain.search_policy import RunStatus


def resolve_completion_status(
    *,
    pending_sync_count: int,
    failed_sources: int,
    total_sources: int,
) -> RunStatus:
    """Resolve a finished run while keeping pending sync work authoritative."""

    if pending_sync_count > 0:
        return RunStatus.COMPLETED_PENDING_SYNC
    if total_sources <= 0 or failed_sources >= total_sources:
        return RunStatus.FAILED
    if failed_sources > 0:
        return RunStatus.COMPLETED_WITH_WARNINGS
    return RunStatus.COMPLETED
