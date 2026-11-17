"""Application service for starting and tracking manual search runs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.repositories.search_policy import (
    PersistedSearchRun,
    SearchPolicyRepository,
)
from personal_job_finder_api.search.connectors import ConnectorFactory
from personal_job_finder_api.search.orchestrator import SearchOrchestrator
from personal_job_finder_api.search.reservation import (
    IdempotencyKeyConflict,
    ReservationOutcome,
    ReservationRejected,
    ReservationRequest,
    SearchReservationService,
    fingerprint_payload,
    normalize_idempotency_key,
)
from personal_job_finder_api.search.service import SearchPolicyService

logger = logging.getLogger(__name__)


class SearchRunService:
    """Coordinate policy preflight, Sheets reconciliation, and reservation.

    The service never holds a database transaction while connectors or Google
    Sheets run. The reservation service remains the single authority for quota,
    cooldown, active-run, and idempotency decisions. Reconciliation runs before
    reservation, so an unreadable canonical snapshot rejects the start without
    consuming quota.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        connector_factory: ConnectorFactory,
        reconcile: Callable[[], None],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._reconcile = reconcile
        self._repository = SearchPolicyRepository()
        self._reservation = SearchReservationService(engine, clock=clock)
        self._policy = SearchPolicyService(
            engine,
            connector_factory=connector_factory,
            clock=clock,
        )
        self._orchestrator = SearchOrchestrator(
            engine,
            connector_factory=connector_factory,
            clock=clock,
        )

    def start(
        self,
        *,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> ReservationOutcome:
        """Return the original run for a replay, or reserve a new accepted run."""

        normalized_key = normalize_idempotency_key(idempotency_key)
        request_fingerprint = fingerprint_payload(payload)

        with self._engine.connect() as connection, connection.begin():
            existing = self._repository.find_by_idempotency_key(connection, normalized_key)

        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise IdempotencyKeyConflict(normalized_key, existing.run_id)
        else:
            # Connector availability is part of eligibility in M2B: without an
            # implemented source, rejecting before reservation avoids consuming
            # quota for a run that cannot do any external work.
            decision = self._policy.evaluate()
            if not decision.allowed:
                raise ReservationRejected(decision)

            # P0/12 §4: a bounded canonical read must precede reservation. A
            # failure propagates before the reservation transaction starts, so
            # no quota, cooldown, or run row is created by a failed preflight.
            self._reconcile()

        return self._reservation.reserve(
            ReservationRequest(idempotency_key=normalized_key, payload=payload)
        )

    def get_run(self, run_id: str) -> PersistedSearchRun | None:
        with self._engine.connect() as connection, connection.begin():
            return self._repository.get_run(connection, run_id)

    def execute(self, run_id: str) -> None:
        """Run orchestration without letting an internal failure escape the task."""

        try:
            self._orchestrator.run(run_id)
        except Exception:
            logger.warning(
                "Search run %s did not reach a terminal state; "
                "startup recovery will mark it interrupted.",
                run_id,
            )
