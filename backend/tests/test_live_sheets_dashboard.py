"""Supervised live check of the Dashboard against a disposable spreadsheet.

Skipped unless explicitly enabled. Enabling it requires three independent
confirmations, because this is the only test in the repository that writes to a
real Google Sheet:

1. ``PJF_LIVE_SHEETS=1``;
2. ``PJF_LIVE_SHEETS_CONFIRM`` equal to the last six characters of the configured
   spreadsheet id, so the run cannot be started by a stale shell by accident;
3. both canonical tabs are header-only, checked against the live values before
   anything is written.

The test snapshots both canonical tabs first, writes one deterministic fixture,
compares every Dashboard metric with the independent oracle, then restores the
snapshot and verifies the restore. It never deletes a tab and never clears a
range it did not capture.

Run it with the scratch configuration, for example::

    PJF_LIVE_SHEETS=1 \\
    PJF_LIVE_SHEETS_CONFIRM=<last 6 chars> \\
    PJF_DATA_DIR=... GOOGLE_KEYRING_SERVICE_NAME=personal-job-finder-scratch \\
    GOOGLE_SHEETS_SPREADSHEET_ID=... \\
    uv run pytest tests/test_live_sheets_dashboard.py -q -s
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets import dashboard_verify as verify
from personal_job_finder_api.integrations.google_sheets import live_check
from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_FIRST_DATA_ROW,
)
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_foundation,
)

JAKARTA = ZoneInfo("Asia/Jakarta")

LIVE_ENABLED = os.environ.get("PJF_LIVE_SHEETS") == "1"


@pytest.fixture(autouse=True)
def block_external_network() -> None:
    """Override the suite-wide guard: this module is the live-opt-in exception.

    Every other test must inject a fake transport. This module talks to the real
    Google Sheets API, and only runs when three explicit opt-ins are present.
    """

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


@pytest.mark.skipif(not LIVE_ENABLED, reason="set PJF_LIVE_SHEETS=1 to run")
def test_the_dashboard_agrees_with_the_oracle_for_a_disposable_fixture() -> None:
    spreadsheet_id = _spreadsheet_id()
    _confirm(spreadsheet_id)

    settings = Settings()
    foundation = build_google_sheets_foundation(settings)
    client = foundation.client
    as_of = datetime.now(UTC).astimezone(JAKARTA).date()

    snapshot = live_check.capture(client, spreadsheet_id)
    artifact = live_check.save_snapshot(snapshot, Path(settings.data_dir) / "live-artifacts")
    print(f"snapshot: {artifact}")

    allow_data = os.environ.get("PJF_LIVE_SHEETS_ALLOW_DATA") == "1"
    live_check.assert_disposable(snapshot, allow_data=allow_data)

    # The KPI strip and the native table are part of what is verified, so apply
    # the same repair `sync` applies before writing the fixture. The restore below
    # restores values only; the presentation layer is intentionally kept.
    foundation.repair_workspace()

    fixture = live_check.build_fixture(as_of=as_of, run_id=snapshot.run_id)
    expected = live_check.expected_metrics(fixture, as_of=as_of)

    try:
        live_check.write_rows(
            client,
            spreadsheet_id,
            "Opportunities",
            fixture.opportunity_rows(),
            start_row=OPPORTUNITY_FIRST_DATA_ROW,
        )
        live_check.write_rows(
            client,
            spreadsheet_id,
            "Job_Sources",
            fixture.source_rows(),
            start_row=2,
        )

        def read() -> tuple[verify.MetricCheck, ...]:
            report = verify.verify_dashboard(client, spreadsheet_id, as_of=as_of)
            return report.checks

        checks, attempts, stable = live_check.wait_until_settled(
            read, is_settled=lambda current: all(check.ok for check in current)
        )

        failures = [check for check in checks if not check.ok]
        assert not failures, failures
        assert stable, f"the Dashboard did not settle after {attempts} read(s)"

        # The oracle's own numbers must match what was compared, so a silently
        # empty expectation cannot make the assertion vacuous.
        compared = {check.address: check.expected for check in checks}
        for address, want in expected.values.items():
            assert compared[address] == want, address
    finally:
        live_check.restore(client, spreadsheet_id, snapshot)

    restored = client.get_values(
        spreadsheet_id,
        ["Opportunities", "Job_Sources"],
        value_render_option="UNFORMATTED_VALUE",
    )
    for title, original in snapshot.tabs.items():
        now = [row for row in restored.get(title, []) if any(cell != "" for cell in row)]
        before = [row for row in original if any(cell != "" for cell in row)]
        assert now == before, title


@pytest.mark.skipif(not LIVE_ENABLED, reason="set PJF_LIVE_SHEETS=1 to run")
def test_every_scenario_agrees_with_the_oracle() -> None:
    """Run the scenario matrix and report every mismatch in one pass.

    Each scenario isolates one behaviour, so a failure names the behaviour. The
    matrix stops at the first failing scenario only when asked to with
    ``PJF_LIVE_SHEETS_FAIL_FAST=1``; by default it completes so one run describes
    the whole Dashboard instead of one symptom.
    """

    spreadsheet_id = _spreadsheet_id()
    _confirm(spreadsheet_id)

    settings = Settings()
    foundation = build_google_sheets_foundation(settings)
    client = foundation.client
    as_of = datetime.now(UTC).astimezone(JAKARTA).date()

    snapshot = live_check.capture(client, spreadsheet_id)
    artifact = live_check.save_snapshot(snapshot, Path(settings.data_dir) / "live-artifacts")
    print(f"snapshot: {artifact}")

    live_check.assert_disposable(snapshot, allow_data=True)
    # Verify against the repaired layout, so the KPI strip is present and its
    # cells are compared rather than reported as missing.
    foundation.repair_workspace()
    scenarios = live_check.build_scenarios(as_of=as_of, run_id=snapshot.run_id)
    fail_fast = os.environ.get("PJF_LIVE_SHEETS_FAIL_FAST") == "1"
    # The Sheets API allows about 60 reads and 60 writes per minute. Pacing keeps
    # a ten-scenario matrix under that instead of provoking a retry storm.
    pace = float(os.environ.get("PJF_LIVE_SHEETS_PACE_SECONDS", "6"))
    metadata = client.get_metadata(spreadsheet_id)

    results: list[live_check.ScenarioRun] = []
    try:
        for name in live_check.SCENARIO_NAMES:
            result = live_check.run_scenario(
                client,
                spreadsheet_id,
                name=name,
                fixture=scenarios[name],
                as_of=as_of,
                metadata=metadata,
            )
            results.append(result)
            print(result.describe())
            if fail_fast and not result.ok:
                break
            if pace > 0 and name != live_check.SCENARIO_NAMES[-1]:
                time.sleep(pace)
    finally:
        live_check.restore(client, spreadsheet_id, snapshot)

    failed = [result for result in results if not result.ok]
    assert not failed, "\n".join(result.describe() for result in failed)
    assert len(results) == len(live_check.SCENARIO_NAMES)

    # The matrix must be non-vacuous: at least one scenario has to produce a
    # non-zero metric, otherwise a broken formula and a working one look alike.
    non_zero = [
        result
        for result in results
        if any(value != 0 for address, value in result.expected.values.items() if address != "B2")
    ]
    assert non_zero, "every scenario expected zero, so the matrix proved nothing"
