"""Tests for atomic outbox claiming, fencing, and recovery.

The outbox is the only durable record of outstanding synchronization work. These
tests cover the claim transaction, conditional status transitions, and restart
recovery without any Google request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import sync_outbox
from personal_job_finder_api.repositories.sync_outbox import (
    OUTBOX_RETRY_WAIT,
    OUTBOX_SYNCED,
    OutboxRepository,
)

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)


def seed(
    engine: Engine,
    *,
    outbox_id: str = "outbox_1",
    entity_type: str = "opportunity",
    status: str = "pending",
    payload_hash: str = "hash_a",
    next_attempt_at_utc: str | None = None,
    attempt_count: int = 0,
    max_attempts: int = 5,
    run_id: str | None = "run_1",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.insert().values(
                outbox_id=outbox_id,
                run_id=run_id,
                destination="google_sheets",
                entity_type=entity_type,
                entity_id=f"entity_{outbox_id}",
                operation="upsert",
                payload_json='{"opportunity_id":"opp_1"}',
                payload_hash=payload_hash,
                status=status,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                next_attempt_at_utc=next_attempt_at_utc,
                last_error_code=None,
                last_error_at_utc=None,
                created_at_utc="2026-11-15T02:00:00.000000Z",
                updated_at_utc="2026-11-15T02:00:00.000000Z",
                synced_at_utc=None,
            )
        )


def row(engine: Engine, outbox_id: str = "outbox_1") -> dict[str, object]:
    with engine.connect() as connection:
        return dict(
            connection.execute(sync_outbox.select().where(sync_outbox.c.outbox_id == outbox_id))
            .mappings()
            .one()
        )


def count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(sync_outbox.select().with_only_columns(sync_outbox.c.outbox_id))
            .all()
            .__len__()
        )


def test_pending_rows_are_claimed_and_marked_in_progress(engine: Engine) -> None:
    seed(engine)

    claims = OutboxRepository(engine).claim_due(now=NOW, limit=10)

    assert [claim.outbox_id for claim in claims] == ["outbox_1"]
    assert claims[0].payload_hash == "hash_a"
    assert claims[0].entity_id == "entity_outbox_1"
    assert row(engine)["status"] == "in_progress"
    assert row(engine)["attempt_count"] == 1


def test_only_due_retry_wait_rows_are_claimed(engine: Engine) -> None:
    seed(
        engine,
        outbox_id="outbox_due",
        status="retry_wait",
        next_attempt_at_utc="2026-11-15T02:59:00.000000Z",
    )
    seed(
        engine,
        outbox_id="outbox_future",
        status="retry_wait",
        next_attempt_at_utc="2026-11-15T04:00:00.000000Z",
    )

    claims = OutboxRepository(engine).claim_due(now=NOW, limit=10)

    assert [claim.outbox_id for claim in claims] == ["outbox_due"]
    assert row(engine, "outbox_future")["status"] == "retry_wait"


def test_blocked_synced_and_in_progress_rows_are_never_claimed(engine: Engine) -> None:
    seed(engine, outbox_id="outbox_blocked", status="blocked")
    seed(engine, outbox_id="outbox_synced", status="synced")
    seed(engine, outbox_id="outbox_busy", status="in_progress")

    claims = OutboxRepository(engine).claim_due(now=NOW, limit=10)

    assert claims == []
    assert row(engine, "outbox_blocked")["status"] == "blocked"
    assert row(engine, "outbox_synced")["status"] == "synced"
    assert row(engine, "outbox_busy")["status"] == "in_progress"


def test_claim_respects_the_limit_and_oldest_first_order(engine: Engine) -> None:
    seed(engine, outbox_id="outbox_b")
    seed(engine, outbox_id="outbox_a")

    claims = OutboxRepository(engine).claim_due(now=NOW, limit=1)

    assert len(claims) == 1
    assert claims[0].outbox_id in {"outbox_a", "outbox_b"}


def test_row_at_max_attempts_is_not_claimed(engine: Engine) -> None:
    seed(
        engine,
        status="retry_wait",
        attempt_count=5,
        max_attempts=5,
        next_attempt_at_utc="2026-11-15T02:00:00.000000Z",
    )

    assert OutboxRepository(engine).claim_due(now=NOW, limit=10) == []


def test_second_claim_does_not_see_already_claimed_rows(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)

    first = repository.claim_due(now=NOW, limit=10)
    second = repository.claim_due(now=NOW, limit=10)

    assert len(first) == 1
    assert second == []


def test_concurrent_claim_grants_each_row_to_exactly_one_caller(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _index: repository.claim_due(now=NOW, limit=10),
                range(4),
            )
        )

    claimed = [claim.outbox_id for batch in results for claim in batch]
    assert claimed == ["outbox_1"]


def test_mark_synced_requires_the_claimed_payload_hash(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]

    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == "outbox_1")
            .values(payload_hash="hash_b", status="pending")
        )

    assert repository.mark_synced(claim, synced_at=NOW) is False
    assert row(engine)["status"] == "pending"
    assert row(engine)["payload_hash"] == "hash_b"


def test_mark_synced_succeeds_only_from_in_progress(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]

    assert repository.mark_synced(claim, synced_at=NOW) is True
    assert row(engine)["status"] == OUTBOX_SYNCED
    assert row(engine)["synced_at_utc"] == "2026-11-15T03:00:00.000000Z"

    assert repository.mark_synced(claim, synced_at=NOW) is False


def test_stale_worker_cannot_overwrite_a_newer_failure_state(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]

    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == "outbox_1")
            .values(status="blocked", last_error_code="permission_denied")
        )

    assert repository.mark_synced(claim, synced_at=NOW) is False
    assert row(engine)["status"] == "blocked"
    assert row(engine)["last_error_code"] == "permission_denied"


def test_mark_retry_wait_schedules_the_next_attempt(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]
    retry_at = NOW + timedelta(seconds=30)

    assert (
        repository.mark_retry_wait(
            claim,
            error_code="timeout",
            next_attempt_at=retry_at,
            updated_at=NOW,
        )
        is True
    )

    stored = row(engine)
    assert stored["status"] == OUTBOX_RETRY_WAIT
    assert stored["last_error_code"] == "timeout"
    assert stored["next_attempt_at_utc"] == "2026-11-15T03:00:30.000000Z"


def test_mark_retry_wait_falls_back_to_blocked_at_max_attempts(engine: Engine) -> None:
    seed(engine, attempt_count=4, max_attempts=5)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]

    assert claim.attempt_count == 5
    assert (
        repository.mark_retry_wait(
            claim,
            error_code="server_error",
            next_attempt_at=NOW + timedelta(seconds=60),
            updated_at=NOW,
        )
        is True
    )

    stored = row(engine)
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "server_error"


def test_mark_blocked_records_a_sanitized_code(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]

    assert (
        repository.mark_blocked(
            claim,
            error_code="reconnect_required",
            updated_at=NOW,
        )
        is True
    )

    stored = row(engine)
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "reconnect_required"


def test_fenced_transitions_reject_a_stale_claim(engine: Engine) -> None:
    seed(engine)
    repository = OutboxRepository(engine)
    claim = repository.claim_due(now=NOW, limit=10)[0]

    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == "outbox_1")
            .values(status="in_progress", payload_hash="hash_b")
        )

    assert (
        repository.mark_retry_wait(
            claim,
            error_code="timeout",
            next_attempt_at=NOW + timedelta(seconds=30),
            updated_at=NOW,
        )
        is False
    )
    assert row(engine)["payload_hash"] == "hash_b"
    assert row(engine)["status"] == "in_progress"


def test_stale_in_progress_rows_are_recovered_for_retry(engine: Engine) -> None:
    seed(engine, status="in_progress")

    recovered = OutboxRepository(engine).recover_stale_in_progress(
        now=NOW,
        stale_before=NOW - timedelta(minutes=15),
    )

    assert recovered == 1
    stored = row(engine)
    assert stored["status"] == OUTBOX_RETRY_WAIT
    assert stored["last_error_code"] == "interrupted"
    assert stored["synced_at_utc"] is None


def test_recovery_leaves_recent_in_progress_rows_alone(engine: Engine) -> None:
    seed(engine, status="in_progress")
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == "outbox_1")
            .values(updated_at_utc="2026-11-15T02:59:00.000000Z")
        )

    recovered = OutboxRepository(engine).recover_stale_in_progress(
        now=NOW,
        stale_before=NOW - timedelta(minutes=15),
    )

    assert recovered == 0
    assert row(engine)["status"] == "in_progress"


def test_recovery_never_marks_work_synced(engine: Engine) -> None:
    seed(engine, status="in_progress")

    OutboxRepository(engine).recover_stale_in_progress(
        now=NOW,
        stale_before=NOW,
    )

    assert row(engine)["status"] != OUTBOX_SYNCED
    assert row(engine)["synced_at_utc"] is None


def test_requeue_blocked_row_makes_it_eligible_again(engine: Engine) -> None:
    seed(engine, status="blocked", attempt_count=5)
    repository = OutboxRepository(engine)

    assert repository.requeue_blocked(outbox_id="outbox_1", now=NOW) is True

    stored = row(engine)
    assert stored["status"] == "pending"
    assert stored["attempt_count"] == 0
    assert stored["last_error_code"] is None
    assert stored["next_attempt_at_utc"] is None


def test_requeue_refuses_a_row_that_is_not_blocked(engine: Engine) -> None:
    seed(engine, status="pending")

    assert OutboxRepository(engine).requeue_blocked(outbox_id="outbox_1", now=NOW) is False
    assert row(engine)["status"] == "pending"


def test_claim_rejects_an_invalid_limit(engine: Engine) -> None:
    with pytest.raises(ValueError):
        OutboxRepository(engine).claim_due(now=NOW, limit=0)
