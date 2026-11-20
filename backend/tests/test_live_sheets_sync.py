"""Supervised live end-to-end synchronization test.

This is the only test that exercises the *whole* application path against the real
Google Sheets API:

    staging -> durable outbox -> dispatcher -> Google Sheets -> settlement
    -> read model -> refresh -> HTTP API

It is opt-in exactly like the Dashboard live test, because it writes to a real
spreadsheet. It also uses a throwaway SQLite database inside the isolated scratch
data directory, so the user's operational state is never touched.

Run it with the scratch configuration, for example::

    PJF_LIVE_SHEETS=1 PJF_LIVE_SHEETS_CONFIRM=<last 6 chars> \\
    PJF_LIVE_SHEETS_ALLOW_DATA=1 \\
    uv run pytest tests/test_live_sheets_sync.py -q -s
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.db.tables import (
    job_sources_read_model,
    opportunities_read_model,
    sync_outbox,
)
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.integrations.google_sheets import live_check
from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_foundation,
)
from personal_job_finder_api.main import create_app
from personal_job_finder_api.search.staging import OpportunityStagingService
from personal_job_finder_api.sync.dispatcher import SyncDispatcher
from personal_job_finder_api.sync.refresh import (
    RefreshSnapshotError,
    SheetsRefreshService,
)

NOW = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)
ORIGIN = {"Origin": "http://127.0.0.1:5173"}

LIVE_ENABLED = os.environ.get("PJF_LIVE_SHEETS") == "1"


@pytest.fixture(autouse=True)
def block_external_network() -> None:
    """Override the suite-wide guard: this module is the live-opt-in exception."""

    return None


def _spreadsheet_id() -> str:
    value = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not value:
        pytest.skip("GOOGLE_SHEETS_SPREADSHEET_ID is not configured")
    return value


def _confirm(spreadsheet_id: str) -> None:
    expected = os.environ.get("PJF_LIVE_SHEETS_CONFIRM", "")
    if expected != spreadsheet_id[-6:]:
        pytest.skip(
            "Set PJF_LIVE_SHEETS_CONFIRM to the last six characters of the "
            "scratch spreadsheet id to authorize a live write."
        )


def _record(*, external_id: str, source_type: str, title: str = "Backend Engineer"):
    return OpportunityRecord(
        company_id="company_acme",
        company_name="Acme Example",
        job_title=title,
        location=classify_location("Jakarta, Indonesia"),
        description="<p>Build services.</p>",
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type=source_type,
            source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=NOW,
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, None, (ROLE,)),
    )


def _scratch_engine(settings: Settings, run_id: str) -> tuple[Engine, Path]:
    database = Path(settings.data_dir) / f"live-sync-{run_id}.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    url = f"sqlite:///{database}"
    run_migrations(url)
    return create_engine_for_url(url), database


def _as_dict(headers: tuple[str, ...], row: list[str]) -> dict[str, str]:
    """Map a sheet row onto its headers, padding the trailing blanks the API trims."""

    padded = (list(row) + [""] * len(headers))[: len(headers)]
    return dict(zip(headers, padded, strict=True))


def _sheet_rows(client, spreadsheet_id: str) -> dict[str, list[list[str]]]:
    values = client.get_values(
        spreadsheet_id,
        ["Opportunities", "Job_Sources"],
        value_render_option=UNFORMATTED_RENDER_OPTION,
    )
    return {
        title: [row for row in rows[1:] if any(cell != "" for cell in row)]
        for title, rows in values.items()
    }


@pytest.mark.skipif(not LIVE_ENABLED, reason="set PJF_LIVE_SHEETS=1 to run")
def test_staging_to_sheet_to_api_round_trip() -> None:
    spreadsheet_id = _spreadsheet_id()
    _confirm(spreadsheet_id)

    settings = Settings()
    foundation = build_google_sheets_foundation(settings)
    client = foundation.client
    transport = foundation.transport

    snapshot = live_check.capture(client, spreadsheet_id)
    artifact = live_check.save_snapshot(snapshot, Path(settings.data_dir) / "live-artifacts")
    print(f"snapshot: {artifact}")
    live_check.assert_disposable(
        snapshot, allow_data=os.environ.get("PJF_LIVE_SHEETS_ALLOW_DATA") == "1"
    )

    engine, database = _scratch_engine(settings, snapshot.run_id)
    baseline = _sheet_rows(client, spreadsheet_id)

    try:
        # 1. Stage three observations: one job seen twice on the same connector
        #    and once on another. Opportunity identity is
        #    (source_type, company_id, external_source_id), so the duplicate
        #    observation must collapse and the second connector is a second
        #    opportunity, not a second source for the first.
        OpportunityStagingService(engine, clock=lambda: NOW).stage(
            run_id=snapshot.run_id,
            records=[
                _record(external_id="9001", source_type="greenhouse"),
                _record(external_id="9001", source_type="greenhouse"),
                _record(external_id="9001", source_type="lever"),
            ],
        )
        with engine.connect() as connection:
            pending = connection.execute(
                sa.select(sync_outbox.c.entity_type, sync_outbox.c.status)
            ).all()
        print(f"staged outbox rows: {pending}")
        assert pending, "staging produced no outbox work"

        # 2. Dispatch through the real transport.
        outcome = SyncDispatcher(
            engine,
            transport=transport,
            spreadsheet_id=spreadsheet_id,
            clock=lambda: NOW,
        ).dispatch_due()
        print(f"dispatch outcome: {outcome.as_dict()}")
        assert outcome.failed == 0, outcome.as_dict()
        assert outcome.synced == len(pending), outcome.as_dict()

        # 3. Read the rows back from the live sheet and check canonical identity.
        rows = _sheet_rows(client, spreadsheet_id)
        new_opportunities = rows["Opportunities"][len(baseline["Opportunities"]) :]
        new_sources = rows["Job_Sources"][len(baseline["Job_Sources"]) :]
        print(f"opportunities written: {len(new_opportunities)}")
        print(f"sources written: {len(new_sources)}")
        assert len(new_opportunities) == 2, new_opportunities
        assert len(new_sources) == 2, new_sources

        opportunity_rows = [_as_dict(OPPORTUNITY_HEADERS, row) for row in new_opportunities]
        assert len({row["opportunity_id"] for row in opportunity_rows}) == 2
        for opportunity_row in opportunity_rows:
            assert opportunity_row["company_name"] == "Acme Example"
            assert opportunity_row["job_title_original"] == "Backend Engineer"
            assert opportunity_row["location_status"] == "eligible"
            assert opportunity_row["first_seen_at_utc"]
            assert opportunity_row["opportunity_id"]

        source_rows = [_as_dict(JOB_SOURCE_HEADERS, row) for row in new_sources]
        assert {row["source_type"] for row in source_rows} == {"greenhouse", "lever"}
        # Each source row belongs to exactly one of the written opportunities.
        written_ids = {row["opportunity_id"] for row in opportunity_rows}
        assert {row["opportunity_id"] for row in source_rows} == written_ids
        opportunity_row = opportunity_rows[0]

        # 4. Settlement: every outbox row is synced and the read model is populated.
        with engine.connect() as connection:
            statuses = {
                row.status for row in connection.execute(sa.select(sync_outbox.c.status)).all()
            }
            read_opportunities = (
                connection.execute(sa.select(opportunities_read_model.c.opportunity_id))
                .scalars()
                .all()
            )
            read_sources = (
                connection.execute(sa.select(job_sources_read_model.c.source_id)).scalars().all()
            )
        assert statuses == {"synced"}, statuses
        assert written_ids <= set(read_opportunities)
        assert len(read_sources) == 2

        # 5. The canonical refresh validates referential integrity. The captured
        #    baseline holds Job_Sources rows whose opportunity does not exist, so
        #    a refresh must refuse rather than publish an inconsistent snapshot.
        refresh = SheetsRefreshService(
            engine,
            transport_factory=lambda: transport,
            spreadsheet_id=lambda: spreadsheet_id,
            clock=lambda: NOW,
        )
        with pytest.raises(RefreshSnapshotError) as captured:
            refresh.refresh(wait_seconds=5.0)
        print(f"refresh refused the orphan snapshot: {captured.value.code}")
        assert "outside this snapshot" in str(captured.value)

        # 6. Replace the scratch data with only the rows this run wrote, so the
        #    snapshot is internally consistent, then refresh for real.
        live_check.clear_data_rows(client, spreadsheet_id)
        live_check.write_rows(
            client, spreadsheet_id, "Opportunities", new_opportunities, start_row=2
        )
        live_check.write_rows(client, spreadsheet_id, "Job_Sources", new_sources, start_row=2)
        result = refresh.refresh(wait_seconds=5.0)
        print(f"refresh counts: {result.counts}")
        assert result.counts.get("opportunities") == 2, result.counts
        assert result.counts.get("job_sources") == 2, result.counts

        app = create_app(
            app_settings=settings,
            engine=engine,
            clock=lambda: NOW,
            sync_transport=transport,
            sync_spreadsheet_id=spreadsheet_id,
        )
        # The app rejects a foreign Host header, so the client must present the
        # same loopback origin a browser would.
        with TestClient(app, base_url="http://127.0.0.1:8000") as api:
            response = api.get("/api/v1/jobs", headers=ORIGIN)
            assert response.status_code == 200, response.text
            payload = response.json()
            items = payload.get("items") or payload.get("jobs") or []
            ids = {item.get("opportunity_id") for item in items}
            print(f"api returned {len(items)} item(s)")
            assert written_ids <= ids

            detail = api.get(f"/api/v1/jobs/{opportunity_row['opportunity_id']}", headers=ORIGIN)
            assert detail.status_code == 200, detail.text
            body = detail.json()
            # The API contract exposes the normalized column as ``job_title``;
            # ``job_title_original`` is the spreadsheet column name.
            assert body.get("company_name") == "Acme Example"
            assert body.get("job_title") == "Backend Engineer"
            assert body.get("location_status") == "eligible"
    finally:
        live_check.restore(client, spreadsheet_id, snapshot)
        engine.dispose()
        database.unlink(missing_ok=True)

    restored = _sheet_rows(client, spreadsheet_id)
    for title in ("Opportunities", "Job_Sources"):
        before = [
            row for row in snapshot.tabs.get(title, [])[1:] if any(cell != "" for cell in row)
        ]
        assert restored[title] == before, title
    print("restored: both canonical tabs match the captured baseline")


@pytest.mark.skipif(not LIVE_ENABLED, reason="set PJF_LIVE_SHEETS=1 to run")
def test_a_tracker_edit_reaches_the_sheet_and_the_api() -> None:
    """PATCH a tracker field, sync it, and read the same value back through the API.

    This is the user-owned-column path: the edit is local first, becomes durable
    outbox work, and only reaches the sheet on an explicit sync. A successful
    spreadsheet write is therefore checked separately from a successful API read.
    """

    spreadsheet_id = _spreadsheet_id()
    _confirm(spreadsheet_id)

    settings = Settings()
    foundation = build_google_sheets_foundation(settings)
    client = foundation.client
    transport = foundation.transport

    snapshot = live_check.capture(client, spreadsheet_id)
    artifact = live_check.save_snapshot(snapshot, Path(settings.data_dir) / "live-artifacts")
    print(f"snapshot: {artifact}")
    live_check.assert_disposable(
        snapshot, allow_data=os.environ.get("PJF_LIVE_SHEETS_ALLOW_DATA") == "1"
    )

    engine, database = _scratch_engine(settings, snapshot.run_id)
    baseline = _sheet_rows(client, spreadsheet_id)
    baseline_source_ids = {row[0] for row in baseline["Job_Sources"]}

    try:
        OpportunityStagingService(engine, clock=lambda: NOW).stage(
            run_id=snapshot.run_id,
            records=[_record(external_id="9100", source_type="greenhouse")],
        )
        outcome = SyncDispatcher(
            engine,
            transport=transport,
            spreadsheet_id=spreadsheet_id,
            clock=lambda: NOW,
        ).dispatch_due()
        assert outcome.failed == 0, outcome.as_dict()

        app = create_app(
            app_settings=settings,
            engine=engine,
            clock=lambda: NOW,
            sync_transport=transport,
            sync_spreadsheet_id=spreadsheet_id,
        )
        with TestClient(app, base_url="http://127.0.0.1:8000") as api:
            feed = api.get("/api/v1/jobs", headers=ORIGIN)
            assert feed.status_code == 200, feed.text
            items = feed.json().get("items") or []
            assert items, feed.text
            opportunity_id = items[0]["opportunity_id"]

            # 1. PATCH is local only: it queues work and must not touch the sheet.
            patched = api.patch(
                f"/api/v1/opportunities/{opportunity_id}/tracker",
                json={"application_status": "Applied", "date_applied": "2026-11-15"},
                headers=ORIGIN,
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["sync_status"] == "pending"

            sheet_after_patch = _sheet_rows(client, spreadsheet_id)
            row = next(
                candidate
                for candidate in sheet_after_patch["Opportunities"]
                if candidate[0] == opportunity_id
            )
            columns = _as_dict(OPPORTUNITY_HEADERS, row)
            assert columns["application_status"] == "", "PATCH must not write to the sheet"

            # 2. An explicit sync writes the pending edit. There is no
            #    background dispatcher, so this request is what performs it.
            sync = api.post("/api/v1/sync/retry", headers=ORIGIN)
            assert sync.status_code == 200, sync.text
            print(f"sync response: {sync.json()}")

        sheet_after_sync = _sheet_rows(client, spreadsheet_id)
        row = next(
            candidate
            for candidate in sheet_after_sync["Opportunities"]
            if candidate[0] == opportunity_id
        )
        columns = _as_dict(OPPORTUNITY_HEADERS, row)
        assert columns["application_status"] == "Applied", columns["application_status"]
        assert columns["date_applied"] == "2026-11-15", columns["date_applied"]

        # 3. The captured baseline holds Job_Sources rows whose opportunity does
        #    not exist, so the canonical refresh refuses that snapshot. Reduce the
        #    scratch data to only this run's rows before publishing.
        new_sources = [
            row
            for row in sheet_after_sync["Job_Sources"]
            if row and row[0] and row[0] not in baseline_source_ids
        ]
        new_opportunities = [
            row
            for row in sheet_after_sync["Opportunities"]
            if row and row[0] and row[0] == opportunity_id
        ]
        live_check.clear_data_rows(client, spreadsheet_id)
        live_check.write_rows(
            client, spreadsheet_id, "Opportunities", new_opportunities, start_row=2
        )
        live_check.write_rows(client, spreadsheet_id, "Job_Sources", new_sources, start_row=2)

        SheetsRefreshService(
            engine,
            transport_factory=lambda: transport,
            spreadsheet_id=lambda: spreadsheet_id,
            clock=lambda: NOW,
        ).refresh(wait_seconds=5.0)

        with TestClient(
            create_app(
                app_settings=settings,
                engine=engine,
                clock=lambda: NOW,
                sync_transport=transport,
                sync_spreadsheet_id=spreadsheet_id,
            ),
            base_url="http://127.0.0.1:8000",
        ) as api:
            detail = api.get(f"/api/v1/jobs/{opportunity_id}", headers=ORIGIN)
            assert detail.status_code == 200, detail.text
            body = detail.json()
            tracker = body.get("tracker") or body
            assert tracker.get("application_status") == "Applied", body
            assert tracker.get("date_applied") == "2026-11-15", body

            # 4. An explicit null clears a field instead of leaving it unchanged.
            cleared = api.patch(
                f"/api/v1/opportunities/{opportunity_id}/tracker",
                json={"date_applied": None},
                headers=ORIGIN,
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json().get("date_applied") in (None, ""), cleared.text
    finally:
        live_check.restore(client, spreadsheet_id, snapshot)
        engine.dispose()
        database.unlink(missing_ok=True)

    print("restored: both canonical tabs match the captured baseline")
