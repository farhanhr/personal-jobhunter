"""Atomic search-run reservation with idempotency protection."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.domain.search_policy import (
    RunStatus,
    SearchPolicyDecision,
    evaluate_search_policy,
    get_operational_day,
)
from personal_job_finder_api.repositories.search_policy import (
    PersistedSearchRun,
    SearchPolicyRepository,
)


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    idempotency_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    run: PersistedSearchRun
    decision: SearchPolicyDecision
    replayed: bool
    remaining_runs: int


class InvalidIdempotencyKey(ValueError):
    """Raised when a start request does not carry a valid UUID key."""


class IdempotencyKeyConflict(Exception):
    """Raised when the same key is reused for a different request payload."""

    def __init__(self, idempotency_key: str, existing_run_id: str) -> None:
        self.idempotency_key = idempotency_key
        self.existing_run_id = existing_run_id
        super().__init__(
            f"idempotency key {idempotency_key} is already bound to run {existing_run_id}"
        )


class ReservationRejected(Exception):
    """Raised when the policy blocks a new reservation."""

    def __init__(self, decision: SearchPolicyDecision) -> None:
        self.decision = decision
        reasons = ", ".join(reason.value for reason in decision.blocking_reasons)
        super().__init__(f"search run is not eligible: {reasons}")


class SearchReservationService:
    """Reserve quota and persist an accepted run in one SQLite transaction."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = SearchPolicyRepository()
        self._run_id_factory = run_id_factory or (lambda: f"run_{uuid.uuid4().hex}")

    def reserve(self, request: ReservationRequest) -> ReservationOutcome:
        idempotency_key = normalize_idempotency_key(request.idempotency_key)
        request_fingerprint = fingerprint_payload(request.payload)
        now = ensure_aware_utc(self._clock(), "clock")

        with immediate_transaction(self._engine) as connection:
            existing = self._repository.find_by_idempotency_key(connection, idempotency_key)
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise IdempotencyKeyConflict(idempotency_key, existing.run_id)
                decision = self._evaluate(connection, now)
                return ReservationOutcome(
                    run=existing,
                    decision=decision,
                    replayed=True,
                    remaining_runs=decision.remaining_runs,
                )

            decision = self._evaluate(connection, now)
            if not decision.allowed:
                raise ReservationRejected(decision)

            snapshot = self._repository.load_configuration_snapshot(connection)
            run = PersistedSearchRun(
                run_id=self._run_id_factory(),
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                operational_day=decision.operational_day,
                status=RunStatus.ACCEPTED,
                accepted_at=now,
                started_at=None,
                finished_at=None,
                config_snapshot_json=json.dumps(
                    snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                error_summary=None,
                progress_json=None,
                source_summary_json=None,
            )
            self._repository.insert_run(connection, run)

            return ReservationOutcome(
                run=run,
                decision=decision,
                replayed=False,
                remaining_runs=max(0, decision.remaining_runs - 1),
            )

    def _evaluate(self, connection, now: datetime) -> SearchPolicyDecision:
        operational_day = get_operational_day(now)
        state = self._repository.load_policy_state(connection, operational_day)
        return evaluate_search_policy(
            now=now,
            accepted_runs_today=state.accepted_runs_for_day,
            last_accepted_at=state.last_accepted_at,
            active_run_exists=state.active_run_exists,
            usable_source_count=state.usable_source_count,
            target_role_count=state.target_role_count,
        )


def normalize_idempotency_key(value: str) -> str:
    if not value or not value.strip():
        raise InvalidIdempotencyKey("idempotency key is required")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise InvalidIdempotencyKey("idempotency key must be a UUID") from exc
    return str(parsed)


def fingerprint_payload(payload: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("request payload must be JSON-serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
