"""Integration tests for atomic reservation, quota, cooldown, and idempotency."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.tables import companies, search_runs, target_roles
from personal_job_finder_api.domain.search_policy import BlockingReason, RunStatus
from personal_job_finder_api.repositories.search_policy import SearchPolicyRepository
from personal_job_finder_api.search.reservation import (
    IdempotencyKeyConflict,
    InvalidIdempotencyKey,
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


def _request(
    key: str | None = None,
    payload: dict[str, object] | None = None,
) -> ReservationRequest:
    return ReservationRequest(
        idempotency_key=key if key is not None else str(uuid.uuid4()),
        payload=payload or {"client": "test"},
    )


def _set_status(engine: Engine, run_id: str, status: RunStatus) -> None:
    with engine.begin() as connection:
        result = connection.execute(
            search_runs.update().where(search_runs.c.run_id == run_id).values(status=status.value)
        )
        assert result.rowcount == 1


def _count_runs(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(sa.select(sa.func.count()).select_from(search_runs)).scalar_one()
        )


def test_first_reservation_accepts_and_persists_utc_snapshot(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    service = SearchReservationService(engine, clock=clock)
    key = str(uuid.uuid4())

    outcome = service.reserve(_request(key=key))

    assert outcome.replayed is False
    assert outcome.remaining_runs == 1
    assert outcome.run.status is RunStatus.ACCEPTED
    assert outcome.run.operational_day.isoformat() == "2026-11-12"
    assert outcome.run.accepted_at == wib(2026, 11, 12, 7, 0).astimezone(UTC)
    assert outcome.run.idempotency_key == key
    assert len(outcome.run.request_fingerprint) == 64
    assert outcome.run.config_snapshot["schema_version"] == 1
    assert outcome.run.config_snapshot["target_roles"][0]["canonical_name"] == (
        "Backend Engineer 1"
    )
    assert outcome.run.config_snapshot["companies"][0]["company_name"] == "Example Company 1"

    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(search_runs).where(search_runs.c.run_id == outcome.run.run_id)
            )
            .mappings()
            .one()
        )
    assert row["accepted_at_utc"].endswith("Z")
    assert row["status"] == "accepted"
    assert _count_runs(engine) == 1


def test_same_key_and_payload_replays_existing_run(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 12, 7, 0)),
    )
    key = str(uuid.uuid4())
    first = service.reserve(_request(key=key))
    second = service.reserve(_request(key=key))

    assert second.replayed is True
    assert second.run.run_id == first.run.run_id
    assert second.remaining_runs == 1
    assert _count_runs(engine) == 1


def test_same_key_with_different_payload_conflicts(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 12, 7, 0)),
    )
    key = str(uuid.uuid4())
    first = service.reserve(_request(key=key, payload={"version": 1}))

    with pytest.raises(IdempotencyKeyConflict) as raised:
        service.reserve(_request(key=key, payload={"version": 2}))

    assert raised.value.existing_run_id == first.run.run_id
    assert _count_runs(engine) == 1


@pytest.mark.parametrize("key", ["", "   ", "not-a-uuid"])
def test_invalid_idempotency_key_is_rejected(
    engine: Engine,
    seed_config,
    clock_factory,
    key: str,
) -> None:
    seed_config()
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 12, 7, 0)),
    )

    with pytest.raises(InvalidIdempotencyKey):
        service.reserve(_request(key=key))


def test_second_run_after_twelve_hours_and_third_run_rejected(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    service = SearchReservationService(engine, clock=clock)

    first = service.reserve(_request())
    _set_status(engine, first.run.run_id, RunStatus.COMPLETED)

    clock.now = wib(2026, 11, 12, 19, 0)
    second = service.reserve(_request())
    assert second.run.operational_day.isoformat() == "2026-11-12"
    assert second.remaining_runs == 0

    _set_status(engine, second.run.run_id, RunStatus.COMPLETED)
    clock.now = wib(2026, 11, 12, 20, 0)

    with pytest.raises(ReservationRejected) as raised:
        service.reserve(_request())

    assert BlockingReason.QUOTA_EXHAUSTED in raised.value.decision.blocking_reasons
    assert raised.value.decision.remaining_runs == 0


def test_failed_runs_still_count_toward_quota(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    service = SearchReservationService(engine, clock=clock)

    first = service.reserve(_request())
    _set_status(engine, first.run.run_id, RunStatus.FAILED)
    clock.now = wib(2026, 11, 12, 19, 0)
    second = service.reserve(_request())
    _set_status(engine, second.run.run_id, RunStatus.FAILED)
    clock.now = wib(2026, 11, 12, 20, 0)

    with pytest.raises(ReservationRejected) as raised:
        service.reserve(_request())

    assert raised.value.decision.accepted_runs == 2
    assert raised.value.decision.remaining_runs == 0
    assert BlockingReason.QUOTA_EXHAUSTED in raised.value.decision.blocking_reasons


def test_accepted_and_running_runs_block_new_reservations(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    service = SearchReservationService(engine, clock=clock)

    first = service.reserve(_request())

    with pytest.raises(ReservationRejected) as accepted_block:
        service.reserve(_request())

    assert BlockingReason.ACTIVE_RUN_EXISTS in accepted_block.value.decision.blocking_reasons

    _set_status(engine, first.run.run_id, RunStatus.RUNNING)

    with pytest.raises(ReservationRejected) as running_block:
        service.reserve(_request())

    assert BlockingReason.ACTIVE_RUN_EXISTS in running_block.value.decision.blocking_reasons


def test_terminal_run_does_not_block_after_cooldown(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    service = SearchReservationService(engine, clock=clock)

    first = service.reserve(_request())
    _set_status(engine, first.run.run_id, RunStatus.COMPLETED)
    clock.now = wib(2026, 11, 12, 19, 0)

    second = service.reserve(_request())

    assert second.run.status is RunStatus.ACCEPTED
    assert second.replayed is False


@pytest.mark.parametrize(
    ("roles", "companies_count", "company_enabled", "ats_type", "career_url", "expected"),
    [
        (0, 1, True, "greenhouse", None, BlockingReason.NO_TARGET_ROLES),
        (4, 1, True, "greenhouse", None, BlockingReason.TOO_MANY_TARGET_ROLES),
        (1, 0, True, "greenhouse", None, BlockingReason.NO_USABLE_SOURCE),
        (1, 1, False, "greenhouse", None, BlockingReason.NO_USABLE_SOURCE),
        (1, 1, True, "custom", None, BlockingReason.NO_USABLE_SOURCE),
        (1, 1, True, "none", None, BlockingReason.NO_USABLE_SOURCE),
    ],
)
def test_configuration_validation_rejects_before_reservation(
    engine: Engine,
    seed_config,
    clock_factory,
    roles: int,
    companies_count: int,
    company_enabled: bool,
    ats_type: str,
    career_url: str | None,
    expected: BlockingReason,
) -> None:
    seed_config(
        roles=roles,
        companies_count=companies_count,
        company_enabled=company_enabled,
        ats_type=ats_type,
        career_url=career_url,
    )
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 12, 7, 0)),
    )

    with pytest.raises(ReservationRejected) as raised:
        service.reserve(_request())

    assert expected in raised.value.decision.blocking_reasons
    assert _count_runs(engine) == 0


def test_custom_career_url_is_a_usable_source(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(
        ats_type="custom",
        career_url="https://careers.example.test/jobs",
    )
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 12, 7, 0)),
    )

    outcome = service.reserve(_request())

    assert outcome.run.status is RunStatus.ACCEPTED


@pytest.mark.parametrize("policy_status", ["unreviewed", "blocked"])
def test_source_must_be_explicitly_permitted(
    engine: Engine,
    seed_config,
    clock_factory,
    policy_status: str,
) -> None:
    seed_config(policy_status=policy_status)
    service = SearchReservationService(
        engine,
        clock=clock_factory(wib(2026, 11, 12, 7, 0)),
    )

    with pytest.raises(ReservationRejected) as raised:
        service.reserve(_request())

    assert raised.value.decision.blocking_reasons == (BlockingReason.NO_USABLE_SOURCE,)
    assert _count_runs(engine) == 0


def test_configuration_snapshot_is_immutable_after_configuration_change(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    service = SearchReservationService(engine, clock=clock)
    first = service.reserve(_request())
    original_snapshot = first.run.config_snapshot

    with engine.begin() as connection:
        connection.execute(target_roles.update().values(canonical_name="Changed Role"))
        connection.execute(companies.update().values(company_name="Changed Company"))

    repository = SearchPolicyRepository()
    with engine.connect() as connection:
        stored = repository.find_by_idempotency_key(connection, first.run.idempotency_key)

    assert stored is not None
    assert stored.config_snapshot == original_snapshot
    assert stored.config_snapshot["target_roles"][0]["canonical_name"] == ("Backend Engineer 1")
    assert stored.config_snapshot["companies"][0]["company_name"] == "Example Company 1"


def test_two_concurrent_requests_with_different_keys_accept_only_one(
    engine: Engine,
    database_url: str,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    second_engine = create_engine_for_url(database_url)
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    services = [
        SearchReservationService(engine, clock=clock),
        SearchReservationService(second_engine, clock=clock),
    ]
    barrier = threading.Barrier(2)

    def attempt(service: SearchReservationService):
        barrier.wait(timeout=5)
        try:
            return service.reserve(_request())
        except ReservationRejected as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, services))
    finally:
        second_engine.dispose()

    accepted = [result for result in results if not isinstance(result, ReservationRejected)]
    rejected = [result for result in results if isinstance(result, ReservationRejected)]

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert BlockingReason.ACTIVE_RUN_EXISTS in rejected[0].decision.blocking_reasons
    assert _count_runs(engine) == 1


def test_two_concurrent_requests_with_same_key_replay_one_run(
    engine: Engine,
    database_url: str,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    second_engine = create_engine_for_url(database_url)
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    services = [
        SearchReservationService(engine, clock=clock),
        SearchReservationService(second_engine, clock=clock),
    ]
    barrier = threading.Barrier(2)
    key = str(uuid.uuid4())

    def attempt(service: SearchReservationService):
        barrier.wait(timeout=5)
        return service.reserve(_request(key=key))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, services))
    finally:
        second_engine.dispose()

    assert outcomes[0].run.run_id == outcomes[1].run.run_id
    assert sum(1 for outcome in outcomes if outcome.replayed) == 1
    assert _count_runs(engine) == 1


def test_two_concurrent_requests_with_same_key_and_different_payload_conflict(
    engine: Engine,
    database_url: str,
    seed_config,
    clock_factory,
) -> None:
    seed_config()
    second_engine = create_engine_for_url(database_url)
    clock = clock_factory(wib(2026, 11, 12, 7, 0))
    services = [
        SearchReservationService(engine, clock=clock),
        SearchReservationService(second_engine, clock=clock),
    ]
    barrier = threading.Barrier(2)
    key = str(uuid.uuid4())
    attempts = [
        (services[0], {"version": 1}),
        (services[1], {"version": 2}),
    ]

    def attempt(item):
        service, payload = item
        barrier.wait(timeout=5)
        try:
            return service.reserve(_request(key=key, payload=payload))
        except IdempotencyKeyConflict as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, attempts))
    finally:
        second_engine.dispose()

    conflicts = [result for result in results if isinstance(result, IdempotencyKeyConflict)]
    accepted = [result for result in results if not isinstance(result, IdempotencyKeyConflict)]

    assert len(accepted) == 1
    assert len(conflicts) == 1
    assert conflicts[0].existing_run_id == accepted[0].run.run_id
    assert _count_runs(engine) == 1
