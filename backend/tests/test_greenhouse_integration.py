"""Integration tests for the Greenhouse connector inside a search run.

The connector is production code; only the HTTP transport is faked. No test opens
a network connection.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.tables import companies, search_runs, target_roles
from personal_job_finder_api.domain.opportunities import TargetRoleSpec
from personal_job_finder_api.domain.search_policy import BlockingReason, RunStatus
from personal_job_finder_api.search.connectors import (
    connector_snapshot,
)
from personal_job_finder_api.search.greenhouse import GreenhouseConnector
from personal_job_finder_api.search.http import HttpResponse
from personal_job_finder_api.search.orchestrator import SearchOrchestrator
from personal_job_finder_api.search.reservation import (
    ReservationRejected,
    ReservationRequest,
    SearchReservationService,
)

JAKARTA = ZoneInfo("Asia/Jakarta")


def wib(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JAKARTA)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Integration tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class RecordingTransport:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self._status = status
        self.urls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HttpResponse:
        self.urls.append(url)
        return HttpResponse(
            status_code=self._status,
            content_type="application/json",
            body=json.dumps(self._payload).encode("utf-8"),
            headers={},
        )


def seed_source(
    engine: Engine,
    *,
    company_id: str = "company_acme",
    company_name: str = "Acme Example",
    board_token: str = "acme-example",
    policy_status: str = "permitted",
    enabled: bool = True,
    ats_type: str = "greenhouse",
) -> None:
    now = format_utc(wib(2026, 11, 1, 8, 0))
    with engine.begin() as connection:
        connection.execute(
            companies.insert().values(
                company_id=company_id,
                company_name=company_name,
                company_website="https://example.test",
                career_url=None,
                ats_type=ats_type,
                ats_board_token=board_token,
                enabled=enabled,
                policy_status=policy_status,
                access_notes=None,
                created_at_utc=now,
                updated_at_utc=now,
            )
        )


def seed_role(
    engine: Engine,
    *,
    role_id: str = "role_backend",
    canonical_name: str = "Backend Engineer",
    aliases: list[str] | None = None,
) -> None:
    now = format_utc(wib(2026, 11, 1, 8, 0))
    with engine.begin() as connection:
        connection.execute(
            target_roles.insert().values(
                role_id=role_id,
                canonical_name=canonical_name,
                aliases_json=json.dumps(aliases or ["Backend Developer"]),
                excluded_terms_json="[]",
                seniority_json=None,
                enabled=True,
                created_at_utc=now,
                updated_at_utc=now,
            )
        )


def accept_run(engine: Engine, clock) -> str:
    outcome = SearchReservationService(engine, clock=clock).reserve(
        ReservationRequest(idempotency_key="11111111-1111-4111-8111-111111111111")
    )
    return outcome.run.run_id


def run_row(engine: Engine, run_id: str):
    with engine.connect() as connection:
        return (
            connection.execute(sa.select(search_runs).where(search_runs.c.run_id == run_id))
            .mappings()
            .one()
        )


def transport_factory(transport: RecordingTransport, *, target_roles=None):
    """Build production connectors for a snapshot but inject the fake transport."""

    def factory(snapshot):
        filtered = connector_snapshot(snapshot)
        roles = tuple(target_roles or ())
        connectors = []
        for company in filtered["companies"]:
            if company["ats_type"] != "greenhouse":
                continue
            connectors.append(
                GreenhouseConnector(
                    company_id=company["company_id"],
                    company_name=company["company_name"],
                    board_token=company["ats_board_token"],
                    target_roles=roles,
                    transport=transport,
                    clock=lambda: wib(2026, 11, 15, 7, 0),
                    sleep=lambda _seconds: None,
                )
            )
        return connectors

    return factory


BOARD_PAYLOAD = {
    "jobs": [
        {
            "id": 1001,
            "title": "Backend Engineer",
            "location": {"name": "Jakarta, Indonesia"},
            "content": "<p>Build services.</p>",
            "updated_at": "2026-11-10T10:00:00Z",
            "absolute_url": "https://boards.greenhouse.io/acme-example/jobs/1001",
        },
        {
            "id": 1002,
            "title": "Accountant",
            "location": {"name": "Jakarta, Indonesia"},
            "content": "<p>Own the ledger.</p>",
            "updated_at": "2026-11-10T10:00:00Z",
            "absolute_url": "https://boards.greenhouse.io/acme-example/jobs/1002",
        },
        {
            "id": 1003,
            "title": "Backend Engineer",
            "location": {"name": "Singapore"},
            "content": "<p>Build services.</p>",
            "updated_at": "2026-11-10T10:00:00Z",
            "absolute_url": "https://boards.greenhouse.io/acme-example/jobs/1003",
        },
    ],
    "meta": {"total": 3},
}


def test_permitted_company_produces_a_completed_run_with_source_counters(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    transport = RecordingTransport(BOARD_PAYLOAD)

    result = SearchOrchestrator(
        engine,
        connector_factory=transport_factory(
            transport,
            target_roles=(
                TargetRoleSpec(
                    role_id="role_backend",
                    canonical_name="Backend Engineer",
                    aliases=("Backend Developer",),
                ),
            ),
        ),
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.COMPLETED_PENDING_SYNC
    assert row["status"] == "completed_pending_sync"
    assert transport.urls == [
        "https://boards-api.greenhouse.io/v1/boards/acme-example/jobs?content=true"
    ]
    assert json.loads(row["source_summary_json"]) == [
        {
            "source_id": "company_acme",
            "status": "completed",
            "records_found": 3,
            "error_code": None,
            "records_relevant": 1,
            "records_filtered": 2,
            "records_invalid": 0,
        }
    ]


def test_unreviewed_company_blocks_the_run_before_reservation(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine, policy_status="unreviewed")
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    transport = RecordingTransport(BOARD_PAYLOAD)

    with pytest.raises(ReservationRejected) as raised:
        accept_run(engine, clock)

    assert raised.value.decision.blocking_reasons == (BlockingReason.NO_USABLE_SOURCE,)
    assert transport.urls == []
    with engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(search_runs)).scalar_one()
            == 0
        )


def test_blocked_company_blocks_the_run_before_reservation(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine, policy_status="blocked")
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    transport = RecordingTransport(BOARD_PAYLOAD)

    with pytest.raises(ReservationRejected) as raised:
        accept_run(engine, clock)

    assert raised.value.decision.blocking_reasons == (BlockingReason.NO_USABLE_SOURCE,)
    assert transport.urls == []


def test_disabled_company_blocks_the_run_before_reservation(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine, enabled=False)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    transport = RecordingTransport(BOARD_PAYLOAD)

    with pytest.raises(ReservationRejected) as raised:
        accept_run(engine, clock)

    assert raised.value.decision.blocking_reasons == (BlockingReason.NO_USABLE_SOURCE,)
    assert transport.urls == []


def test_orchestrator_uses_the_production_factory_end_to_end(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    """The real factory must pass company identity, token, and roles through."""

    seed_config(roles=0, companies_count=0)
    seed_role(engine, canonical_name="Backend Engineer", aliases=["Backend Developer"])
    seed_source(engine)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    transport = RecordingTransport(BOARD_PAYLOAD)

    from personal_job_finder_api.search.greenhouse import build_connectors_from_snapshot

    def factory(snapshot):
        return build_connectors_from_snapshot(snapshot, transport=transport)

    result = SearchOrchestrator(
        engine,
        connector_factory=factory,
        clock=clock,
    ).run(run_id)

    summary = json.loads(run_row(engine, run_id)["source_summary_json"])[0]
    assert result is RunStatus.COMPLETED_PENDING_SYNC
    assert transport.urls == [
        "https://boards-api.greenhouse.io/v1/boards/acme-example/jobs?content=true"
    ]
    # Roles came from the persisted snapshot, not from test-side wiring.
    assert summary["records_relevant"] == 1
    assert summary["records_found"] == 3


def test_snapshot_remains_immutable_when_a_source_is_blocked_after_acceptance(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    """A run keeps the configuration it was accepted with (AGENTS.md §10)."""

    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    transport = RecordingTransport(BOARD_PAYLOAD)

    # The source is blocked after the run snapshot was captured.
    with engine.begin() as connection:
        connection.execute(
            companies.update()
            .where(companies.c.company_id == "company_acme")
            .values(policy_status="blocked")
        )

    SearchOrchestrator(
        engine,
        connector_factory=transport_factory(transport),
        clock=clock,
    ).run(run_id)

    # The immutable snapshot still holds the permitted state, so the run uses the
    # configuration it was accepted with. The block applies to the next run.
    assert len(transport.urls) == 1


def test_connector_failure_marks_run_failed_and_keeps_quota_consumed(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    transport = RecordingTransport({}, status=500)

    result = SearchOrchestrator(
        engine,
        connector_factory=transport_factory(transport),
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.FAILED
    assert row["status"] == "failed"
    assert json.loads(row["source_summary_json"])[0]["error_code"] == "http_5xx"
    assert json.loads(row["progress_json"])["stage"] == "completed"

    with engine.connect() as connection:
        accepted_runs = connection.execute(
            sa.select(sa.func.count()).select_from(search_runs)
        ).scalar_one()
    assert accepted_runs == 1


def test_zero_job_board_completes_without_warnings(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    seed_source(engine)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    transport = RecordingTransport({"jobs": [], "meta": {"total": 0}})

    result = SearchOrchestrator(
        engine,
        connector_factory=transport_factory(transport),
        clock=clock,
    ).run(run_id)

    row = run_row(engine, run_id)
    assert result is RunStatus.COMPLETED
    assert row["error_summary"] is None
    assert json.loads(row["source_summary_json"])[0]["records_found"] == 0


def test_target_role_snapshot_from_the_database_drives_matching(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine, canonical_name="Data Analyst", aliases=["BI Analyst"])
    seed_source(engine)
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    transport = RecordingTransport(BOARD_PAYLOAD)

    SearchOrchestrator(
        engine,
        connector_factory=transport_factory(transport),
        clock=clock,
    ).run(run_id)

    summary = json.loads(run_row(engine, run_id)["source_summary_json"])[0]
    # The board has one Backend Engineer and one Accountant; neither matches the
    # configured Data Analyst role, so nothing is relevant.
    assert summary["records_found"] == 3
    assert summary["records_relevant"] == 0


def test_snapshot_without_a_permitted_source_yields_no_connectors(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(roles=1, companies_count=1, policy_status="unreviewed")

    from personal_job_finder_api.repositories.search_policy import SearchPolicyRepository

    with engine.connect() as connection:
        snapshot = SearchPolicyRepository().load_configuration_snapshot(connection)

    assert connector_snapshot(snapshot)["companies"] == []


class ConcurrencyTracker:
    def __init__(self) -> None:
        import threading

        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def exit(self) -> None:
        with self.lock:
            self.active -= 1


class TrackingTransport:
    """Fake transport that records how many connector requests run at once."""

    def __init__(self, tracker: ConcurrencyTracker, payload: object) -> None:
        self._tracker = tracker
        self._payload = payload
        self.urls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HttpResponse:
        import time

        self.urls.append(url)
        self._tracker.enter()
        try:
            time.sleep(0.05)
        finally:
            self._tracker.exit()
        return HttpResponse(
            status_code=200,
            content_type="application/json",
            body=json.dumps(self._payload).encode("utf-8"),
            headers={},
        )


def test_greenhouse_connectors_respect_the_two_request_limit(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0, companies_count=0)
    seed_role(engine)
    for index in range(3):
        seed_source(
            engine,
            company_id=f"company_{index}",
            company_name=f"Example Company {index}",
            board_token=f"example-board-{index}",
        )
    clock = clock_factory(wib(2026, 11, 15, 7, 0))
    run_id = accept_run(engine, clock)
    tracker = ConcurrencyTracker()
    transport = TrackingTransport(tracker, {"jobs": [], "meta": {"total": 0}})

    result = SearchOrchestrator(
        engine,
        connector_factory=transport_factory(transport),
        clock=clock,
    ).run(run_id)

    assert result is RunStatus.COMPLETED
    assert tracker.max_active == 2
    assert len(transport.urls) == 3
    summaries = json.loads(run_row(engine, run_id)["source_summary_json"])
    assert len(summaries) == 3
