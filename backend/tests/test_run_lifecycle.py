"""Tests for terminal run-status precedence."""

from __future__ import annotations

from personal_job_finder_api.domain.run_lifecycle import resolve_completion_status
from personal_job_finder_api.domain.search_policy import RunStatus


def test_pending_sync_takes_precedence_over_source_warnings() -> None:
    status = resolve_completion_status(
        pending_sync_count=2,
        failed_sources=1,
        total_sources=2,
    )

    assert status is RunStatus.COMPLETED_PENDING_SYNC


def test_finished_sync_with_a_failed_source_becomes_completed_with_warnings() -> None:
    status = resolve_completion_status(
        pending_sync_count=0,
        failed_sources=1,
        total_sources=2,
    )

    assert status is RunStatus.COMPLETED_WITH_WARNINGS


def test_finished_sync_with_all_sources_failed_is_failed() -> None:
    status = resolve_completion_status(
        pending_sync_count=0,
        failed_sources=2,
        total_sources=2,
    )

    assert status is RunStatus.FAILED


def test_finished_sync_with_no_failed_sources_is_completed() -> None:
    status = resolve_completion_status(
        pending_sync_count=0,
        failed_sources=0,
        total_sources=1,
    )

    assert status is RunStatus.COMPLETED


def test_pending_sync_without_any_sources_is_still_pending() -> None:
    status = resolve_completion_status(
        pending_sync_count=1,
        failed_sources=0,
        total_sources=0,
    )

    assert status is RunStatus.COMPLETED_PENDING_SYNC
