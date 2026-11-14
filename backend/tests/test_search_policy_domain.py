"""Unit tests for the pure search-policy domain."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from personal_job_finder_api.domain.search_policy import (
    BlockingReason,
    RunStatus,
    evaluate_search_policy,
    get_cooldown_remaining_seconds,
    get_cooldown_until,
    get_next_reset_at,
    get_operational_day,
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


def evaluate(**overrides: object):
    values: dict[str, object] = {
        "now": wib(2026, 11, 12, 12, 0),
        "accepted_runs_today": 0,
        "last_accepted_at": None,
        "active_run_exists": False,
        "usable_source_count": 1,
        "target_role_count": 1,
    }
    values.update(overrides)
    return evaluate_search_policy(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (wib(2026, 11, 12, 5, 59, 59), date(2026, 11, 11)),
        (wib(2026, 11, 12, 6, 0, 0), date(2026, 11, 12)),
        (wib(2026, 11, 12, 23, 59, 59), date(2026, 11, 12)),
        (wib(2026, 11, 13, 5, 59, 59), date(2026, 11, 12)),
        (wib(2026, 11, 13, 6, 0, 0), date(2026, 11, 13)),
    ],
)
def test_operational_day_boundaries(now: datetime, expected: date) -> None:
    assert get_operational_day(now) == expected


def test_operational_day_accepts_utc_input() -> None:
    # 2026-11-11 23:00 UTC is 2026-11-12 06:00 WIB.
    assert get_operational_day(datetime(2026, 11, 11, 23, 0, tzinfo=UTC)) == date(2026, 11, 12)


def test_operational_day_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        get_operational_day(datetime(2026, 11, 12, 6, 0))


def test_policy_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(now=datetime(2026, 11, 12, 12, 0))


def test_policy_rejects_naive_last_accepted_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(last_accepted_at=datetime(2026, 11, 12, 7, 0))


def test_next_reset_at_boundaries() -> None:
    assert get_next_reset_at(wib(2026, 11, 12, 5, 59, 59)) == datetime(
        2026, 11, 11, 23, 0, tzinfo=UTC
    )
    assert get_next_reset_at(wib(2026, 11, 12, 6, 0, 0)) == datetime(
        2026, 11, 12, 23, 0, tzinfo=UTC
    )


def test_allowed_when_no_blockers() -> None:
    now = wib(2026, 11, 12, 12, 0)
    decision = evaluate(now=now)

    assert decision.allowed is True
    assert decision.operational_day == date(2026, 11, 12)
    assert decision.max_runs == 2
    assert decision.accepted_runs == 0
    assert decision.remaining_runs == 2
    assert decision.blocking_reasons == ()
    assert decision.next_eligible_at == now.astimezone(UTC)
    assert decision.next_reset_at == datetime(2026, 11, 12, 23, 0, tzinfo=UTC)


def test_cooldown_is_still_active_one_second_before_twelve_hours() -> None:
    last_accepted_at = wib(2026, 11, 12, 7, 0)
    now = wib(2026, 11, 12, 18, 59, 59)

    decision = evaluate(
        now=now,
        accepted_runs_today=1,
        last_accepted_at=last_accepted_at,
    )

    assert decision.allowed is False
    assert decision.blocking_reasons == (BlockingReason.COOLDOWN_ACTIVE,)
    assert decision.remaining_runs == 1
    assert decision.cooldown_remaining_seconds == 1
    assert decision.cooldown_until == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert decision.next_eligible_at == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def test_cooldown_allows_exactly_twelve_hours() -> None:
    decision = evaluate(
        now=wib(2026, 11, 12, 19, 0),
        accepted_runs_today=1,
        last_accepted_at=wib(2026, 11, 12, 7, 0),
    )

    assert decision.allowed is True
    assert decision.cooldown_remaining_seconds == 0
    assert decision.blocking_reasons == ()


def test_cooldown_allows_more_than_twelve_hours() -> None:
    decision = evaluate(
        now=wib(2026, 11, 12, 19, 0, 1),
        accepted_runs_today=1,
        last_accepted_at=wib(2026, 11, 12, 7, 0),
    )

    assert decision.allowed is True
    assert decision.cooldown_remaining_seconds == 0


def test_cooldown_helpers_use_utc_normalization() -> None:
    last_accepted_at = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
    assert get_cooldown_until(last_accepted_at) == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert (
        get_cooldown_remaining_seconds(
            datetime(2026, 11, 12, 11, 59, 59, tzinfo=UTC), last_accepted_at
        )
        == 1
    )


def test_cooldown_remaining_validates_arguments_without_previous_run() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        get_cooldown_remaining_seconds(datetime(2026, 11, 12, 12, 0), None)

    with pytest.raises(ValueError, match="non-negative"):
        get_cooldown_remaining_seconds(wib(2026, 11, 12, 12, 0), None, cooldown_hours=-1)


def test_quota_first_and_second_run_are_allowed() -> None:
    first = evaluate(accepted_runs_today=0)
    second = evaluate(accepted_runs_today=1)

    assert first.allowed is True
    assert first.remaining_runs == 2
    assert second.allowed is True
    assert second.remaining_runs == 1


def test_quota_third_run_is_rejected_until_next_reset() -> None:
    decision = evaluate(
        now=wib(2026, 11, 12, 12, 0),
        accepted_runs_today=2,
        last_accepted_at=wib(2026, 11, 11, 12, 0),
    )

    assert decision.allowed is False
    assert decision.remaining_runs == 0
    assert decision.blocking_reasons == (BlockingReason.QUOTA_EXHAUSTED,)
    assert decision.next_eligible_at == datetime(2026, 11, 12, 23, 0, tzinfo=UTC)


def test_daily_reset_does_not_reset_cooldown() -> None:
    decision = evaluate(
        now=wib(2026, 11, 13, 6, 30),
        accepted_runs_today=0,
        last_accepted_at=wib(2026, 11, 12, 23, 0),
    )

    assert decision.operational_day == date(2026, 11, 13)
    assert decision.accepted_runs == 0
    assert decision.remaining_runs == 2
    assert decision.blocking_reasons == (BlockingReason.COOLDOWN_ACTIVE,)
    assert decision.next_eligible_at == datetime(2026, 11, 13, 4, 0, tzinfo=UTC)


def test_active_run_blocks_and_has_unknown_next_eligible_time() -> None:
    decision = evaluate(active_run_exists=True)

    assert decision.allowed is False
    assert decision.has_active_run is True
    assert decision.blocking_reasons == (BlockingReason.ACTIVE_RUN_EXISTS,)
    assert decision.next_eligible_at is None


def test_target_role_count_must_be_between_one_and_three() -> None:
    no_roles = evaluate(target_role_count=0)
    too_many = evaluate(target_role_count=4)

    assert no_roles.blocking_reasons == (BlockingReason.NO_TARGET_ROLES,)
    assert too_many.blocking_reasons == (BlockingReason.TOO_MANY_TARGET_ROLES,)


def test_at_least_one_usable_source_is_required() -> None:
    decision = evaluate(usable_source_count=0)

    assert decision.allowed is False
    assert decision.blocking_reasons == (BlockingReason.NO_USABLE_SOURCE,)


def test_all_blocking_reasons_are_reported_in_stable_order() -> None:
    decision = evaluate(
        now=wib(2026, 11, 12, 12, 0),
        accepted_runs_today=2,
        last_accepted_at=wib(2026, 11, 12, 11, 0),
        active_run_exists=True,
        usable_source_count=0,
        target_role_count=0,
    )

    assert decision.blocking_reasons == (
        BlockingReason.NO_TARGET_ROLES,
        BlockingReason.NO_USABLE_SOURCE,
        BlockingReason.ACTIVE_RUN_EXISTS,
        BlockingReason.QUOTA_EXHAUSTED,
        BlockingReason.COOLDOWN_ACTIVE,
    )


def test_time_blockers_choose_the_later_eligible_time() -> None:
    # Quota resets at 06:00 WIB, but cooldown runs until 11:00 WIB.
    decision = evaluate(
        now=wib(2026, 11, 13, 5, 0),
        accepted_runs_today=2,
        last_accepted_at=wib(2026, 11, 12, 23, 0),
    )

    assert decision.blocking_reasons == (
        BlockingReason.QUOTA_EXHAUSTED,
        BlockingReason.COOLDOWN_ACTIVE,
    )
    assert decision.next_eligible_at == datetime(2026, 11, 13, 4, 0, tzinfo=UTC)


def test_negative_counts_are_rejected() -> None:
    with pytest.raises(ValueError):
        evaluate(accepted_runs_today=-1)
    with pytest.raises(ValueError):
        evaluate(usable_source_count=-1)
    with pytest.raises(ValueError):
        evaluate(target_role_count=-1)


def test_run_status_sets_match_approved_lifecycle() -> None:
    assert RunStatus.ACCEPTED.is_active is True
    assert RunStatus.RUNNING.is_active is True
    assert RunStatus.COMPLETED.is_terminal is True
    assert RunStatus.COMPLETED_WITH_WARNINGS.is_terminal is True
    assert RunStatus.COMPLETED_PENDING_SYNC.is_terminal is True
    assert RunStatus.FAILED.is_terminal is True
    assert RunStatus.INTERRUPTED.is_terminal is True
