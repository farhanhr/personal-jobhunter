"""Atomic settlement of verified outbox writes into the read model.

A verified Google Sheets write and its local read-model update must commit
together. These tests exercise the fenced settlement primitive directly, without
any Google request, including a failed read-model write that must roll the
outbox transition back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import (
    job_sources_read_model,
    opportunities_read_model,
    sync_outbox,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.repositories.read_model import (
    ReadModelChange,
    ReadModelRepository,
    ReadModelSettlementError,
    read_model_change,
)
from personal_job_finder_api.repositories.sync_outbox import OutboxRepository
from personal_job_finder_api.sync.projections import project_job_source, project_opportunity

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
NOW_ISO = "2026-11-17T03:00:00.000000Z"


def seed_outbox(
    engine: Engine,
    *,
    outbox_id: str,
    entity_type: str,
    entity_id: str,
    operation: str = "upsert",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.insert().values(
                outbox_id=outbox_id,
                run_id="run_1",
                destination="google_sheets",
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                payload_json="{}",
                payload_hash=f"hash-{outbox_id}",
                status="pending",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at_utc=None,
                last_error_code=None,
                last_error_at_utc=None,
                created_at_utc="2026-11-17T02:00:00.000000Z",
                updated_at_utc="2026-11-17T02:00:00.000000Z",
                synced_at_utc=None,
            )
        )


def claim_one(engine: Engine):
    repository = OutboxRepository(engine)
    claims = repository.claim_due(now=NOW, limit=1)
    assert len(claims) == 1
    return claims[0]


def outbox_row(engine: Engine, outbox_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(sync_outbox.select().where(sync_outbox.c.outbox_id == outbox_id))
            .mappings()
            .one()
        )


def opportunity_row(**values: Any) -> dict[str, Any]:
    row = dict.fromkeys(OPPORTUNITY_HEADERS)
    row.update(values)
    return row


def source_row(**values: Any) -> dict[str, Any]:
    row = dict.fromkeys(JOB_SOURCE_HEADERS)
    row.update(values)
    return row


def test_settlement_updates_both_outbox_and_read_model(engine: Engine) -> None:
    seed_outbox(
        engine,
        outbox_id="outbox_opp",
        entity_type="opportunity",
        entity_id="opp_settle",
    )
    claim = claim_one(engine)
    projection = project_opportunity(
        {
            "opportunity_id": "opp_settle",
            "company_name": "Acme Example",
            "job_title": "Backend Engineer",
            "location_status": "eligible",
            "role_match_type": "canonical_title",
            "work_mode": "onsite",
            "remote_scope": "not_applicable",
            "availability_status": "unknown",
            "user_status": "new",
            "notes": "",
        }
    )

    settled = OutboxRepository(engine).settle_synced(
        claim,
        synced_at=NOW,
        read_model=read_model_change(
            tab=projection.tab,
            headers=projection.headers,
            values=projection.values,
        ),
    )

    assert settled is True
    assert outbox_row(engine, "outbox_opp")["status"] == "synced"
    with engine.connect() as connection:
        stored = (
            connection.execute(
                sa.select(
                    opportunities_read_model.c.company_name,
                    opportunities_read_model.c.job_title_original,
                    opportunities_read_model.c.user_status,
                    opportunities_read_model.c.notes,
                )
            )
            .mappings()
            .one()
        )
    assert stored["company_name"] == "Acme Example"
    assert stored["job_title_original"] == "Backend Engineer"
    assert stored["user_status"] == "new"
    assert stored["notes"] is None


def test_job_source_settlement_maps_to_the_source_table(engine: Engine) -> None:
    ReadModelRepository(engine).replace_snapshot(
        opportunities=[opportunity_row(opportunity_id="opp_settle")],
        job_sources=[],
        refreshed_at=NOW,
    )
    seed_outbox(
        engine,
        outbox_id="outbox_src",
        entity_type="job_source",
        entity_id="src_settle",
    )
    claim = claim_one(engine)
    projection = project_job_source(
        {
            "source_id": "src_settle",
            "opportunity_id": "opp_settle",
            "source_type": "greenhouse",
            "safe_source_url": "https://boards.greenhouse.io/acme/jobs/1001",
            "external_source_id": "1001",
            "captured_at_utc": NOW_ISO,
            "observation_status": "seen",
        }
    )

    settled = OutboxRepository(engine).settle_synced(
        claim,
        synced_at=NOW,
        read_model=read_model_change(
            tab=projection.tab,
            headers=projection.headers,
            values=projection.values,
        ),
    )

    assert settled is True
    with engine.connect() as connection:
        stored = (
            connection.execute(
                sa.select(
                    job_sources_read_model.c.opportunity_id,
                    job_sources_read_model.c.source_type,
                    job_sources_read_model.c.source_url,
                    job_sources_read_model.c.external_source_id,
                )
            )
            .mappings()
            .one()
        )
    assert stored["opportunity_id"] == "opp_settle"
    assert stored["source_type"] == "greenhouse"
    assert stored["source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert stored["external_source_id"] == "1001"


def test_a_superseded_claim_does_not_touch_the_read_model(engine: Engine) -> None:
    seed_outbox(
        engine,
        outbox_id="outbox_opp",
        entity_type="opportunity",
        entity_id="opp_settle",
    )
    claim = claim_one(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == "outbox_opp")
            .values(status="pending", payload_hash="newer-hash")
        )
    projection = project_opportunity({"opportunity_id": "opp_settle", "company_name": "Stale"})

    settled = OutboxRepository(engine).settle_synced(
        claim,
        synced_at=NOW,
        read_model=read_model_change(
            tab=projection.tab,
            headers=projection.headers,
            values=projection.values,
        ),
    )

    assert settled is False
    row = outbox_row(engine, "outbox_opp")
    assert row["status"] == "pending"
    assert row["payload_hash"] == "newer-hash"
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(opportunities_read_model)
            ).scalar_one()
            == 0
        )


def test_a_newer_attempt_with_the_same_hash_rejects_a_stale_claim(
    engine: Engine,
) -> None:
    seed_outbox(
        engine,
        outbox_id="outbox_opp",
        entity_type="opportunity",
        entity_id="opp_settle",
    )
    claim = claim_one(engine)
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.outbox_id == "outbox_opp")
            .values(status="in_progress", attempt_count=claim.attempt_count + 1)
        )
    projection = project_opportunity(
        {"opportunity_id": "opp_settle", "company_name": "Stale Attempt"}
    )

    settled = OutboxRepository(engine).settle_synced(
        claim,
        synced_at=NOW,
        read_model=read_model_change(
            tab=projection.tab,
            headers=projection.headers,
            values=projection.values,
        ),
    )

    assert settled is False
    row = outbox_row(engine, "outbox_opp")
    assert row["status"] == "in_progress"
    assert row["attempt_count"] == claim.attempt_count + 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(opportunities_read_model)
            ).scalar_one()
            == 0
        )


def test_a_read_model_failure_rolls_back_the_outbox_transition(engine: Engine) -> None:
    seed_outbox(
        engine,
        outbox_id="outbox_src",
        entity_type="job_source",
        entity_id="src_orphan",
    )
    claim = claim_one(engine)
    projection = project_job_source(
        {
            "source_id": "src_orphan",
            "opportunity_id": "opp_missing",
            "source_type": "greenhouse",
            "observation_status": "seen",
        }
    )

    with pytest.raises(ReadModelSettlementError):
        OutboxRepository(engine).settle_synced(
            claim,
            synced_at=NOW,
            read_model=read_model_change(
                tab=projection.tab,
                headers=projection.headers,
                values=projection.values,
            ),
        )

    row = outbox_row(engine, "outbox_src")
    assert row["status"] == "in_progress"
    assert row["synced_at_utc"] is None
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(job_sources_read_model)
            ).scalar_one()
            == 0
        )


def test_a_withdrawal_removes_the_local_row_even_when_the_remote_one_is_absent(
    engine: Engine,
) -> None:
    ReadModelRepository(engine).replace_snapshot(
        opportunities=[opportunity_row(opportunity_id="opp_withdrawn")],
        job_sources=[],
        refreshed_at=NOW,
    )
    seed_outbox(
        engine,
        outbox_id="outbox_opp",
        entity_type="opportunity",
        entity_id="opp_withdrawn",
        operation="withdraw",
    )
    claim = claim_one(engine)

    settled = OutboxRepository(engine).settle_synced(
        claim,
        synced_at=NOW,
        read_model=ReadModelChange(
            tab="Opportunities",
            identity="opp_withdrawn",
            values=None,
        ),
    )

    assert settled is True
    assert outbox_row(engine, "outbox_opp")["status"] == "synced"
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(opportunities_read_model)
            ).scalar_one()
            == 0
        )
