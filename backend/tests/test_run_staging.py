"""Tests for staging connector results before a run reaches a terminal state."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import opportunities, search_runs, sync_outbox
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.domain.search_policy import RunStatus
from personal_job_finder_api.search.connectors import SourceRunResult, SourceRunStatus
from personal_job_finder_api.search.orchestrator import SearchOrchestrator
from personal_job_finder_api.search.reservation import (
    ReservationRequest,
    SearchReservationService,
)

JAKARTA = ZoneInfo("Asia/Jakarta")
FIXED_NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
ROLE = TargetRoleSpec(role_id="role_backend", canonical_name="Backend Engineer")


def wib(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JAKARTA)


def make_record(
    *,
    external_id: str = "1001",
    company_id: str = "company_acme",
    title: str = "Backend Engineer",
    location: str = "Jakarta, Indonesia",
) -> OpportunityRecord:
    return OpportunityRecord(
        company_id=company_id,
        company_name="Acme Example",
        job_title=title,
        location=classify_location(location),
        description="<p>Build services.</p>",
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type="greenhouse",
            source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=FIXED_NOW,
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, None, (ROLE,)),
    )


class FakeConnector:
    source_id = "company_acme"

    def __init__(self, records: tuple[OpportunityRecord, ...] = ()) -> None:
        self._records = records
        self.calls = 0

    def run(self) -> SourceRunResult:
        self.calls += 1
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.COMPLETED,
            records_found=len(self._records),
            records_relevant=sum(
                1 for record in self._records if record.location.status.value == "eligible"
            ),
            records_filtered=0,
            records_invalid=0,
            records=self._records,
        )


class FailingConnector:
    source_id = "company_other"

    def run(self) -> SourceRunResult:
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.FAILED,
            records_found=0,
            error_code="http_503",
        )


def accept_run(
    engine: Engine,
    clock,
    key: str = "11111111-1111-4111-8111-111111111111",
) -> str:
    outcome = SearchReservationService(engine, clock=clock).reserve(
        ReservationRequest(idempotency_key=key)
    )
    return outcome.run.run_id


def run_row(engine: Engine, run_id: str):
    with engine.connect() as connection:
        return (
            connection.execute(sa.select(search_runs).where(search_runs.c.run_id == run_id))
            .mappings()
            .one()
        )


def count(engine: Engine, table) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def test_successful_run_stages_records_and_outbox_before_completion(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    connector = FakeConnector((make_record(external_id="1"), make_record(external_id="2")))

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [connector],
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.COMPLETED_PENDING_SYNC
    assert row["status"] == "completed_pending_sync"
    assert count(engine, opportunities) == 2
    assert count(engine, sync_outbox) == 4
    summary = json.loads(row["source_summary_json"])[0]
    assert summary["records_found"] == 2


def test_run_with_zero_records_still_completes(engine: Engine, seed_config, clock_factory) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [FakeConnector(())],
        clock=clock,
    ).run(run_id)

    assert result is RunStatus.COMPLETED
    assert count(engine, opportunities) == 0
    assert count(engine, sync_outbox) == 0


def test_staging_failure_marks_the_run_failed_and_rolls_back(
    engine: Engine,
    seed_config,
    clock_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_job_finder_api.repositories import opportunities as repo_module

    def explode(self, connection, **kwargs):
        raise RuntimeError("private detail: /home/user/secret")

    monkeypatch.setattr(
        repo_module.OpportunityStagingRepository,
        "upsert_outbox",
        explode,
    )

    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [FakeConnector((make_record(),))],
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.FAILED
    assert row["status"] == "failed"
    assert count(engine, opportunities) == 0
    assert count(engine, sync_outbox) == 0
    # Quota stays consumed, and the diagnostic is sanitized.
    assert row["error_summary"] is not None
    assert "/home/user/secret" not in row["error_summary"]
    with engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(search_runs)).scalar_one()
            == 1
        )


def test_recovery_after_an_interrupted_run_leaves_no_partial_staging(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    from personal_job_finder_api.core.time import format_utc
    from personal_job_finder_api.search.recovery import recover_interrupted_runs

    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    # The process dies while the run is accepted, before staging happens.
    with engine.begin() as connection:
        connection.execute(
            search_runs.update()
            .where(search_runs.c.run_id == run_id)
            .values(status="running", started_at_utc=format_utc(clock()))
        )

    recovered = recover_interrupted_runs(engine, now=clock())
    assert recovered == 1
    assert run_row(engine, run_id)["status"] == "interrupted"
    assert count(engine, opportunities) == 0
    assert count(engine, sync_outbox) == 0

    # A later run stages cleanly with no leftover partial state.
    clock.now = wib(2026, 11, 16, 7, 0)
    next_run = accept_run(engine, clock, key="33333333-3333-4333-8333-333333333333")
    SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [FakeConnector((make_record(),))],
        clock=clock,
    ).run(next_run)

    assert run_row(engine, next_run)["status"] == "completed_pending_sync"
    assert count(engine, opportunities) == 1
    assert count(engine, sync_outbox) == 2


def test_one_bad_record_discards_every_source_record_of_the_run(
    engine: Engine,
    seed_config,
    clock_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging is one transaction, so a failure loses all sources, not one."""

    from personal_job_finder_api.repositories import opportunities as repo_module

    original = repo_module.OpportunityStagingRepository.upsert_outbox
    calls = {"n": 0}

    def explode_on_second(self, connection, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure on the second source")
        return original(self, connection, **kwargs)

    monkeypatch.setattr(
        repo_module.OpportunityStagingRepository,
        "upsert_outbox",
        explode_on_second,
    )

    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [
            FakeConnector((make_record(external_id="1"),)),
            FakeConnector((make_record(external_id="2"),)),
        ],
        clock=clock,
    ).run(run_id)

    assert result is RunStatus.FAILED
    assert count(engine, opportunities) == 0
    assert count(engine, sync_outbox) == 0


def test_second_run_does_not_duplicate_staged_records(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    first_run = accept_run(engine, clock)

    SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [FakeConnector((make_record(),))],
        clock=clock,
    ).run(first_run)

    clock.now = wib(2026, 11, 16, 7, 0)
    second_run = SearchReservationService(engine, clock=clock).reserve(
        ReservationRequest(idempotency_key="22222222-2222-4222-8222-222222222222")
    )
    SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [FakeConnector((make_record(),))],
        clock=clock,
    ).run(second_run.run.run_id)

    assert count(engine, opportunities) == 1
    assert count(engine, sync_outbox) == 2
    assert run_row(engine, second_run.run.run_id)["status"] == "completed_pending_sync"


def test_mixed_source_failure_with_pending_sync_reports_pending_sync(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    """A successful source's unsynced records outrank another source's failure."""

    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [
            FakeConnector((make_record(external_id="1"),)),
            FailingConnector(),
        ],
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.COMPLETED_PENDING_SYNC
    assert row["status"] == "completed_pending_sync"
    assert count(engine, sync_outbox) == 2
    assert "1 of 2 configured sources failed" in (row["error_summary"] or "")
    progress = json.loads(row["progress_json"])
    assert progress["pending_sync"] == 2
    summaries = json.loads(row["source_summary_json"])
    assert [summary["status"] for summary in summaries] == ["completed", "failed"]
    assert summaries[1]["error_code"] == "http_503"


def test_excluded_only_records_allow_completion_without_sheets_sync(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [
            FakeConnector((make_record(external_id="1", location="Singapore"),)),
        ],
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.COMPLETED
    assert count(engine, opportunities) == 1
    assert count(engine, sync_outbox) == 0
    assert json.loads(row["progress_json"])["pending_sync"] == 0


def test_excluded_only_records_with_a_failed_source_report_warnings(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)

    result = SearchOrchestrator(
        engine,
        connector_factory=lambda _snapshot: [
            FakeConnector((make_record(external_id="1", location="Singapore"),)),
            FailingConnector(),
        ],
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.COMPLETED_WITH_WARNINGS
    assert row["status"] == "completed_with_warnings"
    assert count(engine, sync_outbox) == 0
    assert json.loads(row["progress_json"])["pending_sync"] == 0
    assert "1 of 2 configured sources failed" in (row["error_summary"] or "")
