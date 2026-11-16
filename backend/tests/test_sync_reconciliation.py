"""Tests for run-status reconciliation after outbox work finishes.

A run may only leave ``completed_pending_sync`` once every projection it observed
has reached the canonical spreadsheet, including Job_Sources projections. Source
connector failures must survive reconciliation so the run becomes
``completed_with_warnings`` rather than a clean ``completed``.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import (
    opportunities,
    opportunity_run_refs,
    search_runs,
    sync_outbox,
)
from personal_job_finder_api.domain.run_reconciliation import reconcile_run_status
from personal_job_finder_api.domain.search_policy import RunStatus

FIXED = "2026-11-15T03:00:00.000000Z"


def seed_run(
    engine: Engine,
    *,
    run_id: str,
    status: str = "completed_pending_sync",
    source_summary: list[dict[str, object]] | None = None,
) -> None:
    summaries = source_summary or [
        {"source_id": "company_acme", "status": "completed"},
        {"source_id": "company_other", "status": "failed", "error_code": "http_503"},
    ]
    with engine.begin() as connection:
        connection.execute(
            search_runs.insert().values(
                run_id=run_id,
                idempotency_key=f"key-{run_id}",
                request_fingerprint="fingerprint",
                operational_day="2026-11-15",
                status=status,
                accepted_at_utc=FIXED,
                started_at_utc=FIXED,
                finished_at_utc=FIXED,
                config_snapshot_json="{}",
                error_summary=None,
                progress_json="{}",
                source_summary_json=json.dumps(summaries),
            )
        )


def seed_projection(
    engine: Engine,
    *,
    run_id: str,
    opportunity_id: str = "opp_1",
    source_id: str = "src_1",
    status: str = "synced",
    entity_type: str = "opportunity",
    outbox_id: str | None = None,
) -> None:
    entity_id = opportunity_id if entity_type == "opportunity" else source_id
    with engine.begin() as connection:
        exists = connection.execute(
            sa.select(opportunities.c.opportunity_id).where(
                opportunities.c.opportunity_id == opportunity_id
            )
        ).first()
        if exists is None:
            connection.execute(
                opportunities.insert().values(
                    opportunity_id=opportunity_id,
                    company_id="company_acme",
                    company_name="Acme Example",
                    job_title="Backend Engineer",
                    location_status="eligible",
                    work_mode="onsite",
                    remote_scope="not_applicable",
                    first_seen_at_utc=FIXED,
                    last_seen_at_utc=FIXED,
                    availability_status="unknown",
                    feed_bucket="main",
                    content_hash="hash",
                    created_at_utc=FIXED,
                    updated_at_utc=FIXED,
                )
            )
        ref_exists = connection.execute(
            sa.select(opportunity_run_refs.c.opportunity_id).where(
                opportunity_run_refs.c.opportunity_id == opportunity_id,
                opportunity_run_refs.c.run_id == run_id,
                opportunity_run_refs.c.source_id == source_id,
            )
        ).first()
        if ref_exists is None:
            connection.execute(
                opportunity_run_refs.insert().values(
                    opportunity_id=opportunity_id,
                    run_id=run_id,
                    source_id=source_id,
                    first_seen_at_utc=FIXED,
                    last_seen_at_utc=FIXED,
                )
            )
        connection.execute(
            sync_outbox.insert().values(
                outbox_id=outbox_id or f"outbox_{entity_type}_{entity_id}",
                run_id=run_id,
                destination="google_sheets",
                entity_type=entity_type,
                entity_id=entity_id,
                operation="upsert",
                payload_json="{}",
                payload_hash="hash",
                status=status,
                attempt_count=0,
                max_attempts=5,
                next_attempt_at_utc=None,
                last_error_code=None,
                last_error_at_utc=None,
                created_at_utc=FIXED,
                updated_at_utc=FIXED,
                synced_at_utc=FIXED if status == "synced" else None,
            )
        )


def status_of(engine: Engine, run_id: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                sa.select(search_runs.c.status).where(search_runs.c.run_id == run_id)
            ).scalar_one()
        )


def test_run_stays_pending_while_any_projection_is_outstanding(engine: Engine) -> None:
    seed_run(engine, run_id="run_1")
    seed_projection(engine, run_id="run_1", status="synced")
    seed_projection(
        engine,
        run_id="run_1",
        status="pending",
        entity_type="job_source",
    )

    reconciled = reconcile_run_status(engine, run_ids=["run_1"])

    assert reconciled == ()
    assert status_of(engine, "run_1") == "completed_pending_sync"


def test_run_becomes_completed_with_warnings_when_a_connector_failed(
    engine: Engine,
) -> None:
    seed_run(engine, run_id="run_1")
    seed_projection(engine, run_id="run_1", status="synced")
    seed_projection(engine, run_id="run_1", status="synced", entity_type="job_source")

    reconciled = reconcile_run_status(engine, run_ids=["run_1"])

    assert reconciled == (("run_1", RunStatus.COMPLETED_WITH_WARNINGS),)
    assert status_of(engine, "run_1") == "completed_with_warnings"


def test_run_becomes_completed_when_every_connector_succeeded(engine: Engine) -> None:
    seed_run(
        engine,
        run_id="run_1",
        source_summary=[{"source_id": "company_acme", "status": "completed"}],
    )
    seed_projection(engine, run_id="run_1", status="synced")

    reconcile_run_status(engine, run_ids=["run_1"])

    assert status_of(engine, "run_1") == "completed"


def test_terminal_failed_run_is_never_rewritten(engine: Engine) -> None:
    seed_run(engine, run_id="run_1", status="failed")
    seed_projection(engine, run_id="run_1", status="synced")

    reconciled = reconcile_run_status(engine, run_ids=["run_1"])

    assert reconciled == ()
    assert status_of(engine, "run_1") == "failed"


def test_reconciliation_preserves_the_connector_failure_summary(engine: Engine) -> None:
    seed_run(engine, run_id="run_1")
    seed_projection(engine, run_id="run_1", status="synced")

    reconcile_run_status(engine, run_ids=["run_1"])

    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(search_runs.c.source_summary_json).where(search_runs.c.run_id == "run_1")
        ).scalar_one()
    summaries = json.loads(stored)
    assert summaries[1]["status"] == "failed"
    assert summaries[1]["error_code"] == "http_503"


def test_later_run_projection_does_not_clear_an_earlier_run_obligation(
    engine: Engine,
) -> None:
    seed_run(engine, run_id="run_1")
    seed_run(engine, run_id="run_2")
    seed_projection(engine, run_id="run_1", opportunity_id="opp_1", status="pending")
    seed_projection(engine, run_id="run_2", opportunity_id="opp_2", status="synced")

    reconcile_run_status(engine, run_ids=["run_2"])

    assert status_of(engine, "run_2") == "completed_with_warnings"
    assert status_of(engine, "run_1") == "completed_pending_sync"


def test_reconciliation_reports_only_runs_that_changed(engine: Engine) -> None:
    seed_run(engine, run_id="run_1")
    seed_projection(engine, run_id="run_1", status="synced")
    seed_run(engine, run_id="run_2")
    seed_projection(engine, run_id="run_2", opportunity_id="opp_2", status="pending")

    reconciled = reconcile_run_status(engine, run_ids=["run_1", "run_2"])

    assert reconciled == (("run_1", RunStatus.COMPLETED_WITH_WARNINGS),)


def test_reconciliation_ignores_unknown_run_ids(engine: Engine) -> None:
    assert reconcile_run_status(engine, run_ids=["run_missing"]) == ()
