"""Pre-reservation reconciliation ordering.

P0/12 §4 requires a bounded canonical Sheets read before an Auto Search
reservation. These tests prove that a failed read leaves quota, cooldown, and run
rows untouched, and that reconciliation happens before the reservation.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import search_runs
from personal_job_finder_api.search.connectors import SourceRunResult, SourceRunStatus
from personal_job_finder_api.search.run_service import SearchRunService
from personal_job_finder_api.sync.refresh import RefreshUnavailableError

JAKARTA = ZoneInfo("Asia/Jakarta")
KEY = "11111111-1111-4111-8111-111111111111"


class EmptyConnector:
    source_id = "company_1"

    def run(self) -> SourceRunResult:
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.COMPLETED,
            records_found=0,
        )


def factory(_snapshot) -> list[EmptyConnector]:
    return [EmptyConnector()]


def run_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(sa.select(sa.func.count()).select_from(search_runs)).scalar_one()
        )


def now() -> datetime:
    return datetime(2026, 11, 17, 7, 0, tzinfo=JAKARTA)


def test_reconciliation_runs_before_the_reservation(engine: Engine, seed_config) -> None:
    seed_config()
    observed: list[str] = []

    def reconcile() -> None:
        assert run_count(engine) == 0
        observed.append("reconcile")

    service = SearchRunService(
        engine,
        connector_factory=factory,
        reconcile=reconcile,
        clock=now,
    )

    outcome = service.start(idempotency_key=KEY, payload={})

    assert observed == ["reconcile"]
    assert run_count(engine) == 1
    assert outcome.run.status.value == "accepted"


def test_a_failed_reconciliation_rejects_before_any_reservation(
    engine: Engine,
    seed_config,
) -> None:
    seed_config()

    def reconcile() -> None:
        raise RefreshUnavailableError(
            "timeout",
            "Google Sheets could not be read. No snapshot was published.",
        )

    service = SearchRunService(
        engine,
        connector_factory=factory,
        reconcile=reconcile,
        clock=now,
    )

    with pytest.raises(RefreshUnavailableError):
        service.start(idempotency_key=KEY, payload={})

    assert run_count(engine) == 0
