"""Read-only search-policy evaluation service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, utc_now
from personal_job_finder_api.domain.search_policy import (
    SearchPolicyDecision,
    evaluate_search_policy,
    get_operational_day,
)
from personal_job_finder_api.repositories.search_policy import SearchPolicyRepository


class SearchPolicyService:
    """Load persisted state and evaluate the pure policy."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = SearchPolicyRepository()

    def evaluate(self, now: datetime | None = None) -> SearchPolicyDecision:
        evaluation_time = ensure_aware_utc(now or self._clock(), "now")
        operational_day = get_operational_day(evaluation_time)

        with self._engine.connect() as connection, connection.begin():
            state = self._repository.load_policy_state(connection, operational_day)

        return evaluate_search_policy(
            now=evaluation_time,
            accepted_runs_today=state.accepted_runs_for_day,
            last_accepted_at=state.last_accepted_at,
            active_run_exists=state.active_run_exists,
            usable_source_count=state.usable_source_count,
            target_role_count=state.target_role_count,
        )
