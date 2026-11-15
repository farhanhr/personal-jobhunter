"""Tests for the in-process search run orchestrator."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import companies, search_runs
from personal_job_finder_api.domain.search_policy import RunStatus
from personal_job_finder_api.search.connectors import SourceRunResult, SourceRunStatus
from personal_job_finder_api.search.orchestrator import SearchOrchestrator
from personal_job_finder_api.search.reservation import (
    ReservationRequest,
    SearchReservationService,
)

JAKARTA = ZoneInfo("Asia/Jakarta")


def wib(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=JAKARTA)


def _accepted_run(engine: Engine, clock) -> str:
    outcome = SearchReservationService(engine, clock=clock).reserve(
        ReservationRequest(idempotency_key=str(uuid.uuid4()), payload={})
    )
    return outcome.run.run_id


def _row(engine: Engine, run_id: str):
    with engine.connect() as connection:
        return (
            connection.execute(sa.select(search_runs).where(search_runs.c.run_id == run_id))
            .mappings()
            .one()
        )


def _factory(*connectors):
    return lambda _snapshot: list(connectors)


class FakeConnector:
    def __init__(
        self,
        source_id: str = "source_1",
        *,
        records_found: int = 1,
        status: SourceRunStatus = SourceRunStatus.COMPLETED,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.source_id = source_id
        self.records_found = records_found
        self.status = status
        self.error = error
        self.delay = delay
        self.calls = 0

    def run(self) -> SourceRunResult:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return SourceRunResult(
            source_id=self.source_id,
            status=self.status,
            records_found=self.records_found,
            error_code=None if self.status is SourceRunStatus.COMPLETED else "source_failed",
        )


def test_successful_connector_completes_run_and_persists_safe_summary(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)
    connector = FakeConnector(records_found=3)

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(connector),
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.COMPLETED
    assert connector.calls == 1
    assert row["status"] == "completed"
    assert row["started_at_utc"] is not None
    assert row["finished_at_utc"] is not None
    assert row["error_summary"] is None
    progress = json.loads(row["progress_json"])
    assert progress["stage"] == "completed"
    assert progress["completed_sources"] == 1
    assert progress["total_sources"] == 1
    assert progress["records_staged"] == 0
    assert progress["pending_sync"] == 0
    assert json.loads(row["source_summary_json"]) == [
        {
            "source_id": "source_1",
            "status": "completed",
            "records_found": 3,
            "error_code": None,
        }
    ]


def test_zero_result_run_still_completes_and_keeps_its_accepted_row(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(FakeConnector(records_found=0)),
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.COMPLETED
    assert row["status"] == "completed"
    assert json.loads(row["source_summary_json"])[0]["records_found"] == 0


def test_connector_exception_marks_run_failed_without_leaking_exception_text(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(FakeConnector(error=RuntimeError("token=super-secret"))),
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.FAILED
    assert row["status"] == "failed"
    assert "super-secret" not in (row["error_summary"] or "")
    assert json.loads(row["source_summary_json"]) == [
        {
            "source_id": "source_1",
            "status": "failed",
            "records_found": 0,
            "error_code": "connector_error",
        }
    ]


def test_all_connectors_failing_marks_run_failed(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(
            FakeConnector("source_1", status=SourceRunStatus.FAILED),
            FakeConnector("source_2", status=SourceRunStatus.FAILED),
        ),
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.FAILED
    assert row["status"] == "failed"
    assert row["error_summary"] == (
        "All configured sources failed; the accepted run remains consumed."
    )


def test_partial_source_failure_marks_completed_with_warnings(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(
            FakeConnector("source_1", records_found=2),
            FakeConnector("source_2", status=SourceRunStatus.FAILED),
        ),
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.COMPLETED_WITH_WARNINGS
    assert row["status"] == "completed_with_warnings"
    progress = json.loads(row["progress_json"])
    assert progress["stage"] == "completed"
    assert progress["completed_sources"] == 1
    assert progress["total_sources"] == 2


def test_connector_factory_only_receives_usable_permitted_sources(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    now = format_utc(wib(2026, 11, 1, 8, 0))
    with engine.begin() as connection:
        for company_id, policy_status, ats_type, token, enabled in (
            ("company_blocked", "blocked", "greenhouse", "blocked-board", True),
            ("company_unreviewed", "unreviewed", "lever", "review-board", True),
            ("company_disabled", "permitted", "ashby", "disabled-board", False),
        ):
            connection.execute(
                companies.insert().values(
                    company_id=company_id,
                    company_name=company_id,
                    company_website="https://example.test",
                    career_url=None,
                    ats_type=ats_type,
                    ats_board_token=token,
                    enabled=enabled,
                    policy_status=policy_status,
                    access_notes=None,
                    created_at_utc=now,
                    updated_at_utc=now,
                )
            )

    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)
    captured: dict[str, object] = {}

    def factory(snapshot):
        captured.update(snapshot)
        return [FakeConnector("company_1")]

    SearchOrchestrator(engine, connector_factory=factory, clock=clock).run(run_id)

    company_ids = [company["company_id"] for company in captured["companies"]]
    assert company_ids == ["company_1"]


def test_no_connectors_marks_accepted_run_failed(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(),
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.FAILED
    assert row["status"] == "failed"
    assert row["error_summary"] == (
        "No connector implementation is available for the configured sources."
    )
    assert json.loads(row["source_summary_json"]) == []


def test_connector_factory_exception_marks_run_failed_without_leaking_text(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)

    def broken_factory(_snapshot):
        raise RuntimeError("private credential path=/home/user/secret")

    result = SearchOrchestrator(
        engine,
        connector_factory=broken_factory,
        clock=clock,
    ).run(run_id)

    row = _row(engine, run_id)
    assert result is RunStatus.FAILED
    assert row["status"] == "failed"
    assert "/home/user/secret" not in (row["error_summary"] or "")


def test_run_is_not_executed_twice(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)
    connector = FakeConnector()
    orchestrator = SearchOrchestrator(
        engine,
        connector_factory=_factory(connector),
        clock=clock,
    )

    first = orchestrator.run(run_id)
    second = orchestrator.run(run_id)

    assert first is RunStatus.COMPLETED
    assert second is None
    assert connector.calls == 1


class TrackingConnector:
    def __init__(self, source_id: str, tracker: ConcurrencyTracker) -> None:
        self.source_id = source_id
        self._tracker = tracker

    def run(self) -> SourceRunResult:
        with self._tracker.lock:
            self._tracker.active += 1
            self._tracker.max_active = max(self._tracker.max_active, self._tracker.active)
        time.sleep(0.05)
        with self._tracker.lock:
            self._tracker.active -= 1
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.COMPLETED,
            records_found=0,
        )


class ConcurrencyTracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0


def test_source_execution_never_exceeds_two_workers(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    run_id = _accepted_run(engine, clock)
    tracker = ConcurrencyTracker()
    connectors = [TrackingConnector(f"source_{index}", tracker) for index in range(3)]

    result = SearchOrchestrator(
        engine,
        connector_factory=_factory(*connectors),
        clock=clock,
    ).run(run_id)

    assert result is RunStatus.COMPLETED
    assert tracker.max_active == 2
    assert len(json.loads(_row(engine, run_id)["source_summary_json"])) == 3
