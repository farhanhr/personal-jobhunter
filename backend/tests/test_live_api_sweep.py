"""Opt-in live API sweep: every endpoint against the disposable scratch sheet.

The other live modules check the Dashboard and the synchronization path in depth.
This one walks the whole HTTP surface once, so a contract regression in an endpoint
that has nothing to do with Google Sheets still fails loudly against a real
application instance.

It is skipped unless ``PJF_LIVE_SHEETS=1`` and the confirmation token match, and it
restores the spreadsheet from a snapshot before it exits. It uses a throwaway
SQLite database inside the isolated scratch data directory.

The Sheets API allows roughly 60 reads and 60 writes per minute per user, and the
application deliberately does **not** retry a throttled read: it refuses the request
and consumes no quota. Run this module on its own rather than in the same minute as
the other live modules, or the sweep will observe a genuine ``rate_limited`` refusal
and report it as a failure.

Run it with::

    PJF_LIVE_SHEETS=1 PJF_LIVE_SHEETS_CONFIRM=<last 6 chars> \\
    PJF_LIVE_SHEETS_ALLOW_DATA=1 \\
    uv run pytest tests/test_live_api_sweep.py -q -s
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.integrations.google_sheets import live_check
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_foundation,
)
from personal_job_finder_api.main import create_app
from personal_job_finder_api.search.connectors import SourceRunResult, SourceRunStatus

NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
ORIGIN = {"Origin": "http://127.0.0.1:5173"}
ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)
TARGET_ROLES = {
    "target_roles": [{"canonical_name": "Backend Engineer", "aliases": ["Backend Developer"]}]
}

LIVE_ENABLED = os.environ.get("PJF_LIVE_SHEETS") == "1"


@pytest.fixture(autouse=True)
def block_external_network() -> None:
    """Override the suite-wide guard: this module is the live-opt-in exception."""

    return None


class StubConnector:
    """Deterministic connector: no network access, one eligible opportunity."""

    source_id = "source_stub"

    def run(self) -> SourceRunResult:
        record = OpportunityRecord(
            company_id="company_acme",
            company_name="Acme Example",
            job_title="Backend Engineer",
            location=classify_location("Jakarta, Indonesia"),
            description="<p>Build services.</p>",
            posted_at=None,
            source_updated_at=None,
            source=JobSourceRef(
                source_type="greenhouse",
                source_url="https://boards.greenhouse.io/acme/jobs/qa-1",
                external_source_id="qa-1",
                captured_at=NOW,
                provenance_note="Synthetic QA record; no network access.",
            ),
            role_match=match_target_role("Backend Engineer", None, (ROLE,)),
        )
        return SourceRunResult(
            source_id=self.source_id,
            status=SourceRunStatus.COMPLETED,
            records_found=1,
            records=(record,),
        )


def _stub_factory(_snapshot: Any) -> list[StubConnector]:
    return [StubConnector()]


def _spreadsheet_id() -> str:
    value = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not value:
        pytest.skip("GOOGLE_SHEETS_SPREADSHEET_ID is not configured")
    return value


def _confirm(spreadsheet_id: str) -> None:
    if os.environ.get("PJF_LIVE_SHEETS_CONFIRM", "") != spreadsheet_id[-6:]:
        pytest.skip(
            "Set PJF_LIVE_SHEETS_CONFIRM to the last six characters of the "
            "scratch spreadsheet id to authorize a live write."
        )


@pytest.mark.skipif(not LIVE_ENABLED, reason="set PJF_LIVE_SHEETS=1 to run")
def test_every_endpoint_behaves_as_documented() -> None:
    spreadsheet_id = _spreadsheet_id()
    _confirm(spreadsheet_id)

    settings = Settings()
    foundation = build_google_sheets_foundation(settings)
    client = foundation.client
    transport = foundation.transport

    run_id = datetime.now(UTC).strftime("qa-%Y%m%dT%H%M%SZ")
    snapshot = live_check.capture(client, spreadsheet_id, run_id=run_id)
    print(f"snapshot: {run_id}")
    live_check.assert_disposable(
        snapshot, allow_data=os.environ.get("PJF_LIVE_SHEETS_ALLOW_DATA") == "1"
    )
    # The captured baseline may hold rows whose opportunity is absent, which the
    # canonical refresh refuses. Blank the data rows for the sweep and restore
    # them from the snapshot afterwards.
    live_check.clear_data_rows(client, spreadsheet_id)

    database = Path(settings.data_dir) / f"{run_id}.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    url = f"sqlite:///{database}"
    run_migrations(url)
    engine = create_engine_for_url(url)

    failures: list[str] = []

    def check(name: str, condition: bool, detail: Any = "") -> None:
        if condition:
            print(f"  PASS  {name}")
        else:
            failures.append(f"{name}: {detail}")
            print(f"  FAIL  {name}  {detail}")

    try:
        app = create_app(
            app_settings=settings,
            engine=engine,
            clock=lambda: NOW,
            connector_factory=_stub_factory,
            sync_transport=transport,
            sync_spreadsheet_id=spreadsheet_id,
        )
        with TestClient(app, base_url="http://127.0.0.1:8000") as api:
            _sweep(api, check)
    finally:
        live_check.restore(client, spreadsheet_id, snapshot)
        engine.dispose()
        database.unlink(missing_ok=True)

    assert not failures, "\n".join(failures)


def _sweep(api: TestClient, check: Any) -> None:
    _health_and_guards(api, check)
    _roles(api, check)
    _companies(api, check)
    _run_and_sync(api, check)
    _feed_and_tracker(api, check)
    _dashboard(api, check)


def _health_and_guards(api: TestClient, check: Any) -> None:
    print("== health and local guards ==")
    response = api.get("/api/v1/health")
    check("health returns 200", response.status_code == 200, response.text)
    check("health reports ok", response.json().get("status") == "ok", response.text)

    # Reads are guarded by Host, which is what stops DNS rebinding; Origin is
    # required only for state-changing methods.
    foreign_host = api.get("/api/v1/jobs", headers={**ORIGIN, "Host": "evil.example"})
    check("a foreign Host is rejected", foreign_host.status_code == 403, foreign_host.text)
    check(
        "the rejection uses a stable code",
        foreign_host.json().get("error", {}).get("code") == "invalid_host",
        foreign_host.text,
    )
    check(
        "the rejection does not echo the host",
        "evil.example" not in foreign_host.text,
        foreign_host.text,
    )
    foreign_origin = api.post("/api/v1/sync/retry", headers={"Origin": "http://evil.example"})
    check(
        "a foreign Origin is rejected on a write",
        foreign_origin.status_code == 403,
        foreign_origin.text,
    )
    check(
        "a loopback Origin is accepted",
        api.get("/api/v1/jobs", headers=ORIGIN).status_code == 200,
        "",
    )


def _roles(api: TestClient, check: Any) -> None:
    print("== search policy and target roles ==")
    policy = api.get("/api/v1/search-policy", headers=ORIGIN).json()
    check("the daily limit is fixed at two", policy.get("max_runs") == 2, str(policy))
    check(
        "an unconfigured app cannot start",
        policy.get("can_start") is False,
        str(policy.get("blocking_reasons")),
    )

    settings = api.get("/api/v1/settings/search", headers=ORIGIN)
    check("GET settings returns 200", settings.status_code == 200, settings.text)
    check("settings start with no roles", settings.json().get("target_roles") == [], settings.text)
    check(
        "settings cap roles at three", settings.json().get("max_target_roles") == 3, settings.text
    )

    saved = api.put("/api/v1/settings/search", json=TARGET_ROLES, headers=ORIGIN)
    check("a role can be saved", saved.status_code == 200, saved.text)
    check(
        "the saved role keeps its alias",
        saved.json()["target_roles"][0]["aliases"] == ["Backend Developer"],
        saved.text,
    )

    too_many = api.put(
        "/api/v1/settings/search",
        json={"target_roles": [{"canonical_name": f"Role {index}"} for index in range(4)]},
        headers=ORIGIN,
    )
    check("more than three roles is refused", too_many.status_code == 422, too_many.text)

    cleared = api.put("/api/v1/settings/search", json={"target_roles": []}, headers=ORIGIN)
    check("roles may be cleared", cleared.status_code == 200, cleared.text)
    check(
        "clearing roles blocks a search",
        api.get("/api/v1/search-policy", headers=ORIGIN).json().get("can_start") is False,
        "",
    )
    api.put("/api/v1/settings/search", json=TARGET_ROLES, headers=ORIGIN)


def _companies(api: TestClient, check: Any) -> None:
    print("== company sources ==")
    body = {"company_name": "Acme Example", "board_token": "acmeexample"}
    created = api.post("/api/v1/companies", json=body, headers=ORIGIN)
    check("a company can be registered", created.status_code == 201, created.text)
    check(
        "a new source is never auto-approved",
        created.json().get("policy_status") != "permitted",
        created.text,
    )
    duplicate = api.post("/api/v1/companies", json=body, headers=ORIGIN)
    check("a duplicate board is refused", duplicate.status_code == 409, duplicate.text)
    bad_token = api.post(
        "/api/v1/companies",
        json={"company_name": "Bad", "board_token": "not a token!"},
        headers=ORIGIN,
    )
    check("an invalid board token is refused", bad_token.status_code == 422, bad_token.text)

    listed = api.get("/api/v1/companies", headers=ORIGIN)
    check("GET companies returns 200", listed.status_code == 200, listed.text)
    companies = listed.json().get("companies", [])
    check("the registered company is listed", len(companies) == 1, str(len(companies)))
    if not companies:
        return

    company_id = companies[0]["company_id"]
    permitted = api.patch(
        f"/api/v1/companies/{company_id}",
        json={"policy_status": "permitted", "enabled": True},
        headers=ORIGIN,
    )
    check("a company can be permitted", permitted.status_code == 200, permitted.text)
    check(
        "a permitted source may be fetched",
        permitted.json().get("can_fetch") is True,
        permitted.text,
    )
    check(
        "patching an unknown company returns 404",
        api.patch(
            "/api/v1/companies/company_missing", json={"enabled": True}, headers=ORIGIN
        ).status_code
        == 404,
        "",
    )


def _run_and_sync(api: TestClient, check: Any) -> None:
    print("== search run, refresh, and sync ==")
    check(
        "a run without Idempotency-Key is refused",
        api.post("/api/v1/search-runs", json={}, headers=ORIGIN).status_code == 400,
        "",
    )
    check(
        "a non-UUID key is refused",
        api.post(
            "/api/v1/search-runs",
            json={},
            headers={**ORIGIN, "Idempotency-Key": "not-a-uuid"},
        ).status_code
        == 400,
        "",
    )

    key = str(uuid.uuid4())
    accepted = api.post("/api/v1/search-runs", json={}, headers={**ORIGIN, "Idempotency-Key": key})
    check("a valid run is accepted", accepted.status_code == 202, accepted.text)
    if accepted.status_code != 202:
        return
    run_id = accepted.json().get("run_id")

    replay = api.post("/api/v1/search-runs", json={}, headers={**ORIGIN, "Idempotency-Key": key})
    check("replaying the same key is accepted", replay.status_code == 202, replay.text)
    check("the replay reuses the run id", replay.json().get("run_id") == run_id, replay.text)

    latest = api.get("/api/v1/search-runs/latest", headers=ORIGIN)
    check("GET latest run returns 200", latest.status_code == 200, latest.text)
    check("the latest run is the accepted one", latest.json().get("run_id") == run_id, latest.text)

    detail = api.get(f"/api/v1/search-runs/{run_id}", headers=ORIGIN)
    check("GET run detail returns 200", detail.status_code == 200, detail.text)
    check(
        "the run reports pending sync before a manual dispatch",
        detail.json().get("status") == "completed_pending_sync",
        str(detail.json().get("status")),
    )
    check(
        "GET an unknown run returns 404",
        api.get("/api/v1/search-runs/run_missing", headers=ORIGIN).status_code == 404,
        "",
    )

    status = api.get("/api/v1/sync/status", headers=ORIGIN)
    check("GET sync status returns 200", status.status_code == 200, status.text)
    check(
        "the run queued outbox work",
        status.json().get("totals", {}).get("pending", 0) >= 1,
        str(status.json().get("totals")),
    )
    check(
        "POST sheets/refresh returns 200",
        api.post("/api/v1/sheets/refresh", headers=ORIGIN).status_code == 200,
        "",
    )
    dispatched = api.post("/api/v1/sync/retry", headers=ORIGIN)
    check("POST sync/retry returns 200", dispatched.status_code == 200, dispatched.text)
    check("the dispatch synced work", dispatched.json().get("synced", 0) >= 1, dispatched.text)
    check(
        "nothing is left pending",
        api.get("/api/v1/sync/status", headers=ORIGIN).json().get("totals", {}).get("pending", 0)
        == 0,
        "",
    )
    check(
        "the run leaves pending-sync once everything is synced",
        api.get("/api/v1/search-runs/latest", headers=ORIGIN).json().get("status")
        in {"completed", "completed_with_warnings"},
        "",
    )
    check(
        "requeueing an unknown outbox row returns 404",
        api.post("/api/v1/sync/outbox/outbox_missing/requeue", headers=ORIGIN).status_code == 404,
        "",
    )

    policy = api.get("/api/v1/search-policy", headers=ORIGIN).json()
    check("one run was consumed", policy.get("accepted_runs") == 1, str(policy))
    check("one run remains", policy.get("remaining_runs") == 1, str(policy))
    check(
        "a cooldown is now active",
        policy.get("cooldown_remaining_seconds", 0) > 0,
        str(policy.get("cooldown_remaining_seconds")),
    )
    blocked = api.post(
        "/api/v1/search-runs",
        json={},
        headers={**ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
    )
    check("a second run inside the cooldown is rejected", blocked.status_code == 429, blocked.text)
    check(
        "the rejection names the cooldown",
        blocked.json().get("error", {}).get("code") == "search_cooldown_active",
        blocked.text,
    )
    check(
        "the rejected run consumed no quota",
        api.get("/api/v1/search-policy", headers=ORIGIN).json().get("accepted_runs") == 1,
        "",
    )


def _feed_and_tracker(api: TestClient, check: Any) -> None:
    print("== opportunities feed and tracker ==")
    # The dispatch in the previous section already settled the read model, so no
    # extra refresh is needed here; each one costs Sheets quota.
    feed = api.get("/api/v1/jobs", headers=ORIGIN)
    check("GET jobs returns 200", feed.status_code == 200, feed.text)
    items = feed.json().get("items", [])
    check("the synchronized opportunity is in the feed", len(items) >= 1, str(len(items)))
    if not items:
        return
    opportunity_id = items[0]["opportunity_id"]

    detail = api.get(f"/api/v1/jobs/{opportunity_id}", headers=ORIGIN)
    check("GET job detail returns 200", detail.status_code == 200, detail.text)
    body = detail.json()
    check(
        "detail reports the discovered title",
        body.get("job_title") == "Backend Engineer",
        str(body.get("job_title")),
    )
    check(
        "detail reports source provenance",
        isinstance(body.get("sources"), list) and len(body["sources"]) >= 1,
        str(body.get("sources"))[:120],
    )
    check(
        "GET an unknown job returns 404",
        api.get("/api/v1/jobs/opp_missing", headers=ORIGIN).status_code == 404,
        "",
    )

    patched = api.patch(
        f"/api/v1/opportunities/{opportunity_id}/tracker",
        json={"application_status": "Applied", "date_applied": "2026-11-20"},
        headers=ORIGIN,
    )
    check("PATCH tracker returns 200", patched.status_code == 200, patched.text)
    check(
        "a tracker edit is queued, not written through",
        patched.json().get("sync_status") == "pending",
        patched.text,
    )
    check(
        "an over-long note is refused",
        api.patch(
            f"/api/v1/opportunities/{opportunity_id}/tracker",
            json={"notes": "x" * 10_001},
            headers=ORIGIN,
        ).status_code
        == 422,
        "",
    )
    check(
        "a discovery-owned field is refused",
        api.patch(
            f"/api/v1/opportunities/{opportunity_id}/tracker",
            json={"user_status": "hidden"},
            headers=ORIGIN,
        ).status_code
        == 422,
        "",
    )
    check(
        "an empty patch is refused",
        api.patch(
            f"/api/v1/opportunities/{opportunity_id}/tracker", json={}, headers=ORIGIN
        ).status_code
        == 422,
        "",
    )
    cleared = api.patch(
        f"/api/v1/opportunities/{opportunity_id}/tracker",
        json={"date_applied": None},
        headers=ORIGIN,
    )
    check("an explicit null clears a field", cleared.status_code == 200, cleared.text)
    check(
        "the cleared field is empty",
        cleared.json().get("date_applied") in (None, ""),
        cleared.text,
    )


def _dashboard(api: TestClient, check: Any) -> None:
    print("== dashboard summary ==")
    response = api.get("/api/v1/dashboard/summary", headers=ORIGIN)
    check("GET dashboard summary returns 200", response.status_code == 200, response.text)
    body = response.json()
    check(
        "the summary reports the Jakarta as-of date",
        body.get("as_of_date_jkt") == "2026-11-20",
        str(body.get("as_of_date_jkt")),
    )
    check(
        "the summary counts the synchronized opportunity",
        body.get("total_opportunities", 0) >= 1,
        str(body.get("total_opportunities")),
    )
    window = body.get("applications_window", {})
    check(
        "the applications window is the inclusive last seven Jakarta dates",
        window.get("start") == "2026-11-14" and window.get("end") == "2026-11-20",
        str(window),
    )
    check(
        "the summary reports source groups",
        isinstance(body.get("opportunities_by_source"), list),
        str(type(body.get("opportunities_by_source"))),
    )


class FailingConnector:
    """A connector whose source is unreachable; it raises instead of returning."""

    source_id = "source_failing"

    def run(self) -> SourceRunResult:
        raise RuntimeError("simulated source outage")


def _failing_factory(_snapshot: Any) -> list[FailingConnector]:
    return [FailingConnector()]


@pytest.mark.skipif(not LIVE_ENABLED, reason="set PJF_LIVE_SHEETS=1 to run")
def test_a_run_whose_only_source_fails_still_consumes_the_quota() -> None:
    """P0/9: a failed run is failed, but the accepted reservation is not refunded."""

    spreadsheet_id = _spreadsheet_id()
    _confirm(spreadsheet_id)

    settings = Settings()
    foundation = build_google_sheets_foundation(settings)
    client = foundation.client
    transport = foundation.transport

    run_id = datetime.now(UTC).strftime("qaf-%Y%m%dT%H%M%SZ")
    snapshot = live_check.capture(client, spreadsheet_id, run_id=run_id)
    print(f"snapshot: {run_id}")
    live_check.assert_disposable(
        snapshot, allow_data=os.environ.get("PJF_LIVE_SHEETS_ALLOW_DATA") == "1"
    )
    live_check.clear_data_rows(client, spreadsheet_id)

    database = Path(settings.data_dir) / f"{run_id}.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    url = f"sqlite:///{database}"
    run_migrations(url)
    engine = create_engine_for_url(url)

    failures: list[str] = []

    def check(name: str, condition: bool, detail: Any = "") -> None:
        if condition:
            print(f"  PASS  {name}")
        else:
            failures.append(f"{name}: {detail}")
            print(f"  FAIL  {name}  {detail}")

    try:
        app = create_app(
            app_settings=settings,
            engine=engine,
            clock=lambda: NOW,
            connector_factory=_failing_factory,
            sync_transport=transport,
            sync_spreadsheet_id=spreadsheet_id,
        )
        with TestClient(app, base_url="http://127.0.0.1:8000") as api:
            api.put("/api/v1/settings/search", json=TARGET_ROLES, headers=ORIGIN)
            created = api.post(
                "/api/v1/companies",
                json={"company_name": "Acme Example", "board_token": "acmeexample"},
                headers=ORIGIN,
            )
            company_id = created.json().get("company_id")
            api.patch(
                f"/api/v1/companies/{company_id}",
                json={"policy_status": "permitted", "enabled": True},
                headers=ORIGIN,
            )

            accepted = api.post(
                "/api/v1/search-runs",
                json={},
                headers={**ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
            )
            check("the run is accepted", accepted.status_code == 202, accepted.text)
            if accepted.status_code != 202:
                return
            run = accepted.json().get("run_id")

            detail = api.get(f"/api/v1/search-runs/{run}", headers=ORIGIN).json()
            check(
                "a run whose only source failed is failed",
                detail.get("status") == "failed",
                str(detail.get("status")),
            )
            sources = detail.get("source_results") or []
            check(
                "the failure is reported per source",
                any(
                    source.get("status") == "failed"
                    and source.get("error_code") == "connector_error"
                    for source in sources
                ),
                str(sources),
            )

            policy = api.get("/api/v1/search-policy", headers=ORIGIN).json()
            check(
                "the accepted reservation is not refunded",
                policy.get("accepted_runs") == 1,
                str(policy.get("accepted_runs")),
            )
            check(
                "the cooldown still applies",
                policy.get("cooldown_remaining_seconds", 0) > 0,
                str(policy.get("cooldown_remaining_seconds")),
            )
            sync = api.get("/api/v1/sync/status", headers=ORIGIN).json()
            check(
                "a failed run queues no spreadsheet work",
                sync.get("totals", {}).get("outstanding", 0) == 0,
                str(sync.get("totals")),
            )
    finally:
        live_check.restore(client, spreadsheet_id, snapshot)
        engine.dispose()
        database.unlink(missing_ok=True)

    assert not failures, "\n".join(failures)
