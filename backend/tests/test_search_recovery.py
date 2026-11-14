"""Tests for startup recovery of stale active runs."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import search_runs
from personal_job_finder_api.domain.search_policy import BlockingReason, RunStatus
from personal_job_finder_api.search.recovery import (
    DEFAULT_INTERRUPTED_REASON,
    recover_interrupted_runs,
)
from personal_job_finder_api.search.reservation import (
    ReservationRejected,
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


def _insert_run(
    engine: Engine,
    *,
    run_id: str,
    key: str,
    status: RunStatus,
    accepted_at: datetime,
    operational_day: str = "2026-11-12",
    finished_at: datetime | None = None,
    error_summary: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                run_id=run_id,
                idempotency_key=key,
                request_fingerprint="b" * 64,
                operational_day=operational_day,
                status=status.value,
                accepted_at_utc=format_utc(accepted_at),
                started_at_utc=None,
                finished_at_utc=format_utc(finished_at) if finished_at else None,
                config_snapshot_json="{}",
                error_summary=error_summary,
                progress_json=None,
                source_summary_json=None,
            )
        )


def _fetch_status(engine: Engine, run_id: str) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(search_runs.c.status).where(search_runs.c.run_id == run_id)
        ).scalar_one()


def _fetch_row(engine: Engine, run_id: str):
    with engine.connect() as connection:
        return (
            connection.execute(sa.select(search_runs).where(search_runs.c.run_id == run_id))
            .mappings()
            .one()
        )


@pytest.mark.parametrize("status", [RunStatus.ACCEPTED, RunStatus.RUNNING])
def test_stale_active_run_becomes_interrupted(
    engine: Engine,
    status: RunStatus,
) -> None:
    accepted_at = wib(2026, 11, 12, 7, 0)
    _insert_run(
        engine,
        run_id="run_stale",
        key="44444444-4444-4444-8444-444444444444",
        status=status,
        accepted_at=accepted_at,
    )
    recovery_time = wib(2026, 11, 12, 8, 0)

    affected = recover_interrupted_runs(engine, now=recovery_time)

    row = _fetch_row(engine, "run_stale")
    assert affected == 1
    assert row["status"] == "interrupted"
    assert row["finished_at_utc"] == format_utc(recovery_time)
    assert row["error_summary"] == DEFAULT_INTERRUPTED_REASON
    assert row["accepted_at_utc"] == format_utc(accepted_at)


def test_terminal_runs_remain_unchanged(engine: Engine) -> None:
    completed_at = wib(2026, 11, 12, 7, 30)
    _insert_run(
        engine,
        run_id="run_completed",
        key="55555555-5555-4555-8555-555555555555",
        status=RunStatus.COMPLETED,
        accepted_at=wib(2026, 11, 12, 7, 0),
        finished_at=completed_at,
        error_summary=None,
    )
    _insert_run(
        engine,
        run_id="run_failed",
        key="66666666-6666-4666-8666-666666666666",
        status=RunStatus.FAILED,
        accepted_at=wib(2026, 11, 12, 7, 0),
        finished_at=completed_at,
        error_summary="connector failed",
    )

    affected = recover_interrupted_runs(engine, now=wib(2026, 11, 12, 8, 0))

    assert affected == 0
    completed = _fetch_row(engine, "run_completed")
    failed = _fetch_row(engine, "run_failed")
    assert completed["status"] == "completed"
    assert completed["finished_at_utc"] == format_utc(completed_at)
    assert completed["error_summary"] is None
    assert failed["status"] == "failed"
    assert failed["error_summary"] == "connector failed"


def test_recovery_does_not_refund_quota(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    _insert_run(
        engine,
        run_id="run_one",
        key="77777777-7777-4777-8777-777777777777",
        status=RunStatus.ACCEPTED,
        accepted_at=wib(2026, 11, 12, 6, 0),
    )
    assert recover_interrupted_runs(engine, now=wib(2026, 11, 12, 6, 45)) == 1
    assert _fetch_status(engine, "run_one") == "interrupted"

    _insert_run(
        engine,
        run_id="run_two",
        key="88888888-8888-4888-8888-888888888888",
        status=RunStatus.RUNNING,
        accepted_at=wib(2026, 11, 12, 6, 30),
    )
    recovery_time = wib(2026, 11, 12, 23, 0)

    affected = recover_interrupted_runs(engine, now=recovery_time)

    assert affected == 1
    assert _fetch_status(engine, "run_one") == "interrupted"
    assert _fetch_status(engine, "run_two") == "interrupted"

    service = SearchReservationService(
        engine,
        clock=clock_factory(recovery_time),
    )

    with pytest.raises(ReservationRejected) as raised:
        service.reserve(ReservationRequest(idempotency_key="99999999-9999-4999-8999-999999999999"))

    assert raised.value.decision.accepted_runs == 2
    assert raised.value.decision.cooldown_remaining_seconds == 0
    assert raised.value.decision.blocking_reasons == (BlockingReason.QUOTA_EXHAUSTED,)


def test_recovery_keeps_original_accepted_time_for_cooldown(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    accepted_at = wib(2026, 11, 12, 23, 0)
    _insert_run(
        engine,
        run_id="run_stale",
        key="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        status=RunStatus.ACCEPTED,
        accepted_at=accepted_at,
        operational_day="2026-11-12",
    )

    recover_interrupted_runs(engine, now=wib(2026, 11, 13, 6, 30))
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 13, 6, 30)),
    )

    with pytest.raises(ReservationRejected) as raised:
        service.reserve(ReservationRequest(idempotency_key="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))

    decision = raised.value.decision
    assert decision.operational_day.isoformat() == "2026-11-13"
    assert decision.accepted_runs == 0
    assert decision.last_accepted_at == accepted_at.astimezone(UTC)
    assert BlockingReason.COOLDOWN_ACTIVE in decision.blocking_reasons
    assert _fetch_status(engine, "run_stale") == "interrupted"
