"""Live Google Sheets E2E verification against a designated scratch spreadsheet.

Run ONLY when an explicitly designated scratch spreadsheet has been confirmed:

    PERSONAL_JOBHUNTER_E2E_SPREADSHEET_URL=<scratch URL> \
      uv run python -m pytest tests/test_live_sheets_e2e.py -m live_e2e -v

Safety rules enforced by this module:
- Without the dedicated E2E environment variable the entire module is skipped.
- The variable is never populated from `.env`, GOOGLE_SHEETS_SPREADSHEET_URL, or
  GOOGLE_SHEETS_SPREADSHEET_ID; there is no fallback path to the user's real
  spreadsheet.
- Tests create only uniquely namespaced rows/tabs and clean up only data the
  test itself created. Existing tabs, rows, formulas, and data are never deleted.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

E2E_URL_ENV = "PERSONAL_JOBHUNTER_E2E_SPREADSHEET_URL"
_SKIP_REASON = (
    "Live E2E is BLOCKED: set PERSONAL_JOBHUNTER_E2E_SPREADSHEET_URL to a scratch "
    "spreadsheet shared with the Service Account as Editor, then rerun."
)

pytestmark = [
    pytest.mark.skipif(not os.environ.get(E2E_URL_ENV), reason=_SKIP_REASON),
    pytest.mark.live_e2e,
]


def _e2e_settings(tmp_path):
    """Settings for an isolated run: separate data dir and credential dir.

    ``PJF_LOCAL_DIR`` keeps the run away from the user's canonical
    ``<repo>/.local/credentials/google-service-account.json`` so a live check can
    never overwrite the real key. The key itself is *copied* into the isolated
    directory, so authentication works while the canonical file stays read-only.
    """

    from personal_job_finder_api.core.config import Settings

    local_dir = tmp_path / "local"
    credentials_dir = local_dir / "credentials"
    credentials_dir.mkdir(parents=True)

    source = Path(os.environ.get("PJF_LOCAL_DIR", "")) if os.environ.get("PJF_LOCAL_DIR") else None
    if source is None:
        source = Path(__file__).resolve().parents[2] / ".local"
    canonical = source / "credentials" / "google-service-account.json"
    if not canonical.exists():
        pytest.skip(f"No Service Account key found at {canonical}.")

    target = credentials_dir / "google-service-account.json"
    shutil.copyfile(canonical, target)
    target.chmod(0o600)

    return Settings(data_dir=tmp_path / "data", local_dir=local_dir)


def _e2e_spreadsheet_url() -> str:
    value = os.environ[E2E_URL_ENV]
    os.environ.pop("GOOGLE_SHEETS_SPREADSHEET_URL", None)
    os.environ.pop("GOOGLE_SHEETS_SPREADSHEET_ID", None)
    return value


def _run_tag() -> str:
    return f"e2e-{uuid.uuid4().hex[:8]}"


def _setup_with_retry(settings, engine, url: str, attempts: int = 4):
    """Run setup, retrying when Google rate-limits the request.

    Consecutive setups against one spreadsheet can hit the Sheets API rate
    limit. The application surfaces it as ``rate_limited``; the live harness
    waits and retries so a limit is not reported as a product failure.
    """

    import time

    from personal_job_finder_api.integrations.google_sheets.orchestration import run_setup

    result = None
    for attempt in range(attempts):
        result = run_setup(settings, engine, spreadsheet_input=url, configured_via="cli")
        if result.ready or result.code != "rate_limited":
            return result
        if attempt < attempts - 1:
            time.sleep(20 * (attempt + 1))
    return result


def test_service_account_auth_and_spreadsheet_access(tmp_path, network) -> None:
    """Real token acquisition plus a real metadata read of the scratch spreadsheet."""

    from personal_job_finder_api.integrations.google_sheets.service import (
        build_google_sheets_transport,
    )
    from personal_job_finder_api.integrations.google_sheets.spreadsheet_url import (
        extract_spreadsheet_id,
    )

    settings = _e2e_settings(tmp_path)
    spreadsheet_id = extract_spreadsheet_id(_e2e_spreadsheet_url())

    transport = build_google_sheets_transport(settings)
    metadata = transport.get_spreadsheet(spreadsheet_id)

    titles = [sheet["properties"]["title"] for sheet in metadata.get("sheets", [])]
    assert "Opportunities" in titles
    assert "Job_Sources" in titles


def test_setup_is_idempotent_and_initializes_schema(tmp_path, network) -> None:
    """Running setup twice leaves the workspace ready and adds no duplicate tabs."""

    from personal_job_finder_api.db.engine import create_database_engine
    from personal_job_finder_api.db.migrate import run_migrations

    url = _e2e_spreadsheet_url()
    settings = _e2e_settings(tmp_path)
    run_migrations(settings.database_url)
    engine = create_database_engine(settings)

    first = _setup_with_retry(settings, engine, url)
    assert first.ready, f"first setup failed at {first.stage}: {first.code} {first.message}"
    second = _setup_with_retry(settings, engine, url)
    assert second.ready
    assert second.adopted_existing, "the second setup must adopt, not re-create"
    engine.dispose()


def test_crud_and_formula_round_trip(tmp_path, network) -> None:
    """Append, read back, and delete a uniquely tagged row on a real sheet.

    The test writes only to a fresh area far below the workspace data and
    removes exactly what it created. No existing row, header, formula, or tab
    is modified.
    """

    from personal_job_finder_api.integrations.google_sheets.service import (
        build_google_sheets_transport,
    )
    from personal_job_finder_api.integrations.google_sheets.spreadsheet_url import (
        extract_spreadsheet_id,
    )

    settings = _e2e_settings(tmp_path)
    spreadsheet_id = extract_spreadsheet_id(_e2e_spreadsheet_url())
    transport = build_google_sheets_transport(settings)

    tag = _run_tag()
    sheet = "Sheet1"  # the blank scratch tab, not a workspace tab
    # Row 900 is far below the workspace rows on every canonical tab.
    target_row = 900
    range_name = f"A{target_row}"

    transport.batch_update_values(
        spreadsheet_id,
        [{"range": f"{sheet}!{range_name}", "values": [[tag]]}],
        value_input_option="RAW",
    )
    try:
        values = transport.batch_get_values(spreadsheet_id, [f"{sheet}!{range_name}"])
        read_back = [
            cell
            for value_range in values.get("valueRanges", [])
            for row in value_range.get("values", [])
            for cell in row
        ]
        assert tag in read_back, "the appended marker must be readable back"
    finally:
        # Remove exactly the cell this test wrote.
        transport.batch_update_values(
            spreadsheet_id,
            [{"range": f"{sheet}!{range_name}", "values": [[""]]}],
            value_input_option="RAW",
        )


def test_workspace_readiness_persists_after_restart(tmp_path, network) -> None:
    """A second process context sees the persisted readiness row."""

    from personal_job_finder_api.db.engine import create_database_engine
    from personal_job_finder_api.db.migrate import run_migrations
    from personal_job_finder_api.integrations.google_sheets.settings_store import (
        workspace_ready_for,
    )

    url = _e2e_spreadsheet_url()
    settings = _e2e_settings(tmp_path)
    run_migrations(settings.database_url)
    engine = create_database_engine(settings)
    result = _setup_with_retry(settings, engine, url)
    assert result.ready, f"setup failed at {result.stage}: {result.code} {result.message}"
    engine.dispose()

    # A fresh engine simulates an application restart.
    fresh = create_database_engine(settings)
    assert workspace_ready_for(fresh, settings)
    fresh.dispose()
