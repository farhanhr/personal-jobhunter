"""Pure search-policy domain rules.

This module deliberately has no database, network, or wall-clock access. Callers
inject an aware ``datetime`` so quota and cooldown decisions are deterministic and
easy to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

JAKARTA_TIMEZONE = "Asia/Jakarta"
OPERATIONAL_RESET_HOUR = 6
MAX_RUNS_PER_OPERATIONAL_DAY = 2
COOLDOWN_HOURS = 12
MIN_TARGET_ROLES = 1
MAX_TARGET_ROLES = 3


class RunStatus(StrEnum):
    """Lifecycle states approved for a search run."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    COMPLETED_PENDING_SYNC = "completed_pending_sync"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_active(self) -> bool:
        return self in ACTIVE_RUN_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_RUN_STATUSES


ACTIVE_RUN_STATUSES = frozenset({RunStatus.ACCEPTED, RunStatus.RUNNING})
TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_WARNINGS,
        RunStatus.COMPLETED_PENDING_SYNC,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
    }
)


class BlockingReason(StrEnum):
    """Stable reason codes returned to callers and the dashboard."""

    NO_TARGET_ROLES = "no_target_roles"
    TOO_MANY_TARGET_ROLES = "too_many_target_roles"
    NO_USABLE_SOURCE = "no_usable_source"
    ACTIVE_RUN_EXISTS = "active_run_exists"
    QUOTA_EXHAUSTED = "quota_exhausted"
    COOLDOWN_ACTIVE = "cooldown_active"


@dataclass(frozen=True, slots=True)
class SearchPolicyDecision:
    """Immutable result of evaluating the search policy."""

    allowed: bool
    operational_day: date
    max_runs: int
    accepted_runs: int
    remaining_runs: int
    last_accepted_at: datetime | None
    cooldown_until: datetime | None
    cooldown_remaining_seconds: int
    next_eligible_at: datetime | None
    next_reset_at: datetime
    has_active_run: bool
    blocking_reasons: tuple[BlockingReason, ...]


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _validate_reset_hour(reset_hour: int) -> None:
    if not 0 <= reset_hour <= 23:
        raise ValueError("reset_hour must be between 0 and 23")


def get_operational_day(
    now: datetime,
    timezone: str = JAKARTA_TIMEZONE,
    reset_hour: int = OPERATIONAL_RESET_HOUR,
) -> date:
    """Return the operational date for an aware timestamp.

    The operational day starts at ``reset_hour`` in ``timezone``. A local time
    before the reset belongs to the previous calendar date.
    """

    _validate_reset_hour(reset_hour)
    local_now = _require_aware(now, "now").astimezone(ZoneInfo(timezone))
    return (local_now - timedelta(hours=reset_hour)).date()


def get_next_reset_at(
    now: datetime,
    timezone: str = JAKARTA_TIMEZONE,
    reset_hour: int = OPERATIONAL_RESET_HOUR,
) -> datetime:
    """Return the next operational-day reset as an aware UTC datetime."""

    _validate_reset_hour(reset_hour)
    local_now = _require_aware(now, "now").astimezone(ZoneInfo(timezone))
    next_reset = local_now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if local_now >= next_reset:
        next_reset += timedelta(days=1)
    return next_reset.astimezone(UTC)


def get_cooldown_until(
    last_accepted_at: datetime,
    cooldown_hours: int = COOLDOWN_HOURS,
) -> datetime:
    """Return the first instant at which the cooldown has fully elapsed."""

    if cooldown_hours < 0:
        raise ValueError("cooldown_hours must be non-negative")
    accepted_at_utc = _require_aware(last_accepted_at, "last_accepted_at")
    return accepted_at_utc + timedelta(hours=cooldown_hours)


def get_cooldown_remaining_seconds(
    now: datetime,
    last_accepted_at: datetime | None,
    cooldown_hours: int = COOLDOWN_HOURS,
) -> int:
    """Return whole seconds remaining, rounded up so 0 means eligible."""

    now_utc = _require_aware(now, "now")
    if cooldown_hours < 0:
        raise ValueError("cooldown_hours must be non-negative")
    if last_accepted_at is None:
        return 0

    remaining = get_cooldown_until(last_accepted_at, cooldown_hours) - now_utc
    if remaining <= timedelta(0):
        return 0
    return math.ceil(remaining.total_seconds())


def evaluate_search_policy(
    *,
    now: datetime,
    accepted_runs_today: int,
    last_accepted_at: datetime | None,
    active_run_exists: bool,
    usable_source_count: int,
    target_role_count: int,
) -> SearchPolicyDecision:
    """Evaluate all eligibility conditions without performing I/O.

    ``accepted_runs_today`` counts every run accepted for the current
    operational day, including failed and interrupted runs. Rejected requests are
    not rows and therefore never contribute to this count.
    """

    if accepted_runs_today < 0:
        raise ValueError("accepted_runs_today must be non-negative")
    if usable_source_count < 0:
        raise ValueError("usable_source_count must be non-negative")
    if target_role_count < 0:
        raise ValueError("target_role_count must be non-negative")

    now_utc = _require_aware(now, "now")
    last_accepted_utc = (
        _require_aware(last_accepted_at, "last_accepted_at")
        if last_accepted_at is not None
        else None
    )

    operational_day = get_operational_day(now_utc)
    remaining_runs = max(0, MAX_RUNS_PER_OPERATIONAL_DAY - accepted_runs_today)
    cooldown_until = (
        get_cooldown_until(last_accepted_utc) if last_accepted_utc is not None else None
    )
    cooldown_remaining_seconds = get_cooldown_remaining_seconds(now_utc, last_accepted_utc)
    next_reset_at = get_next_reset_at(now_utc)

    blocking_reasons: list[BlockingReason] = []

    if target_role_count < MIN_TARGET_ROLES:
        blocking_reasons.append(BlockingReason.NO_TARGET_ROLES)
    elif target_role_count > MAX_TARGET_ROLES:
        blocking_reasons.append(BlockingReason.TOO_MANY_TARGET_ROLES)

    if usable_source_count < 1:
        blocking_reasons.append(BlockingReason.NO_USABLE_SOURCE)

    if active_run_exists:
        blocking_reasons.append(BlockingReason.ACTIVE_RUN_EXISTS)

    if remaining_runs == 0:
        blocking_reasons.append(BlockingReason.QUOTA_EXHAUSTED)

    if cooldown_remaining_seconds > 0:
        blocking_reasons.append(BlockingReason.COOLDOWN_ACTIVE)

    allowed = not blocking_reasons
    next_eligible_at = _calculate_next_eligible_at(
        now_utc=now_utc,
        next_reset_at=next_reset_at,
        cooldown_until=cooldown_until,
        blocking_reasons=blocking_reasons,
    )

    return SearchPolicyDecision(
        allowed=allowed,
        operational_day=operational_day,
        max_runs=MAX_RUNS_PER_OPERATIONAL_DAY,
        accepted_runs=accepted_runs_today,
        remaining_runs=remaining_runs,
        last_accepted_at=last_accepted_utc,
        cooldown_until=cooldown_until,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
        next_eligible_at=next_eligible_at,
        next_reset_at=next_reset_at,
        has_active_run=active_run_exists,
        blocking_reasons=tuple(blocking_reasons),
    )


def _calculate_next_eligible_at(
    *,
    now_utc: datetime,
    next_reset_at: datetime,
    cooldown_until: datetime | None,
    blocking_reasons: list[BlockingReason],
) -> datetime | None:
    if not blocking_reasons:
        return now_utc

    # An active run has an unknown completion time.
    if BlockingReason.ACTIVE_RUN_EXISTS in blocking_reasons:
        return None

    configuration_reasons = {
        BlockingReason.NO_TARGET_ROLES,
        BlockingReason.TOO_MANY_TARGET_ROLES,
        BlockingReason.NO_USABLE_SOURCE,
    }
    if any(reason in configuration_reasons for reason in blocking_reasons):
        return None

    candidates: list[datetime] = []
    if BlockingReason.QUOTA_EXHAUSTED in blocking_reasons:
        candidates.append(next_reset_at)
    if BlockingReason.COOLDOWN_ACTIVE in blocking_reasons and cooldown_until is not None:
        candidates.append(cooldown_until)

    return max(candidates) if candidates else None
