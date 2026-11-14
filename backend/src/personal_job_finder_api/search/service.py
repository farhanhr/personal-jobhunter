"""Read-only search-policy evaluation service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, utc_now
from personal_job_finder_api.domain.search_policy import (
    SearchPolicyDecision,
    evaluate_search_policy,
    get_operational_day,
)
from personal_job_finder_api.repositories.search_policy import SearchPolicyRepository
from personal_job_finder_api.search.connectors import ConnectorFactory, connector_snapshot


class SearchPolicyService:
    """Load persisted state and evaluate the pure policy."""

    def __init__(
        self,
        engine: Engine,
        *,
        connector_factory: ConnectorFactory,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = SearchPolicyRepository()
        self._connector_factory = connector_factory

    def evaluate(self, now: datetime | None = None) -> SearchPolicyDecision:
        evaluation_time = ensure_aware_utc(now or self._clock(), "now")
        operational_day = get_operational_day(evaluation_time)

        with self._engine.connect() as connection, connection.begin():
            state = self._repository.load_policy_state(connection, operational_day)
            snapshot = (
                self._repository.load_configuration_snapshot(connection)
                if state.usable_source_count
                else None
            )

        usable_source_count = (
            self._available_source_count(connector_snapshot(snapshot))
            if snapshot is not None
            else 0
        )
        return evaluate_search_policy(
            now=evaluation_time,
            accepted_runs_today=state.accepted_runs_for_day,
            last_accepted_at=state.last_accepted_at,
            active_run_exists=state.active_run_exists,
            usable_source_count=usable_source_count,
            target_role_count=state.target_role_count,
        )

    def _available_source_count(self, snapshot: Mapping[str, Any]) -> int:
        try:
            return sum(1 for _ in self._connector_factory(snapshot))
        except Exception:
            # Connector factories must not fail during a read-only eligibility
            # check. Treat an unavailable implementation as no usable source.
            return 0
