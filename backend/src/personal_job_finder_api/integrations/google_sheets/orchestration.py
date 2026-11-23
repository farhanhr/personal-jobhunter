"""Shared Google Sheets setup orchestration for the Settings UI and ``job-hunter setup``.

Both entry points call :func:`run_setup`. The function runs stages in order and
stops at the first failure, reporting which stage failed, what was preserved, and
whether a retry is safe. It never deletes tabs or overwrites incompatible data:
schema initialization only adds missing tabs and canonical headers, and an
unrecognized spreadsheet is refused before any write.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_foundation,
)
from personal_job_finder_api.integrations.google_sheets.service_account import (
    parse_service_account_key,
)
from personal_job_finder_api.integrations.google_sheets.service_account_store import (
    load_service_account_text,
)
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    GoogleSheetsSettingsStore,
    SettingsSource,
    environment_spreadsheet_id,
    resolve_effective_spreadsheet,
)
from personal_job_finder_api.integrations.google_sheets.spreadsheet_url import (
    extract_spreadsheet_id,
)
from personal_job_finder_api.integrations.google_sheets.workspace_adoption import (
    WorkspaceKind,
    inspect_workspace,
)
from personal_job_finder_api.sync.coordination import (
    SheetsOperationBusy,
    acquire_sheets_io,
    release_sheets_io,
)

logger = logging.getLogger(__name__)

Stage = Literal["credentials", "spreadsheet", "access", "workspace", "schema"]
ConfiguredVia = Literal["ui", "cli", "environment"]
Refresh = Callable[[str], dict[str, int] | None]


@dataclass(frozen=True, slots=True)
class SetupResult:
    ready: bool
    stage: Stage | None
    code: str
    message: str
    service_account_email: str | None = None
    source: SettingsSource = "none"
    retry_safe: bool = True
    preserved: str = "Nothing was changed."
    optional_warning: str | None = None
    workspace_ready: bool = False
    adopted_existing: bool = False
    refresh_warning: str | None = None
    counts: dict[str, int] | None = None


def _failure(
    stage: Stage,
    error: GoogleSheetsError,
    *,
    email: str | None,
    retry_safe: bool = True,
    preserved: str = "Nothing was changed.",
) -> SetupResult:
    return SetupResult(
        ready=False,
        stage=stage,
        code=error.code,
        message=str(error),
        service_account_email=email,
        retry_safe=retry_safe,
        preserved=preserved,
    )


def run_setup(
    settings: Settings,
    engine: Engine,
    *,
    spreadsheet_input: str | None = None,
    configured_via: ConfiguredVia = "ui",
    refresh: Refresh | None = None,
) -> SetupResult:
    """Validate credentials and access, initialize the workspace, then persist readiness.

    ``spreadsheet_input`` is a full Google Sheets URL from the UI or CLI. When omitted,
    the effective configuration (saved value, else ``.env``) is validated.
    """

    store = GoogleSheetsSettingsStore(engine)

    # Stage 1: credentials. Only public fields leave this function.
    try:
        raw_text = load_service_account_text(settings)
        if raw_text is None:
            raise GoogleSheetsError(
                "credentials_missing",
                "No Service Account key is installed. Place the downloaded JSON key at "
                ".local/credentials/google-service-account.json and try again.",
            )
        key = parse_service_account_key(raw_text.encode("utf-8"))
    except GoogleSheetsError as error:
        return _failure("credentials", error, email=None)
    email = key.client_email

    # Stage 2: spreadsheet identity.
    try:
        if spreadsheet_input is not None:
            spreadsheet_id = extract_spreadsheet_id(spreadsheet_input)
            source: SettingsSource = configured_via
        else:
            state = store.load_state()
            effective = resolve_effective_spreadsheet(
                saved_id=None if state is None else state.spreadsheet_id,
                environment_id=environment_spreadsheet_id(settings),
                saved_source=None if state is None else state.configured_via,
            )
            if effective.spreadsheet_id is None:
                raise GoogleSheetsError(
                    "spreadsheet_id_missing",
                    "Enter the Google Spreadsheet URL in Settings, or set "
                    "GOOGLE_SHEETS_SPREADSHEET_URL in .env.",
                )
            spreadsheet_id = effective.spreadsheet_id
            source = effective.source
    except GoogleSheetsError as error:
        return _failure("spreadsheet", error, email=email)

    scoped = dataclasses.replace(settings, google_sheets_spreadsheet_id=spreadsheet_id)

    try:
        acquire_sheets_io()
    except SheetsOperationBusy:
        return _failure(
            "workspace",
            GoogleSheetsError(
                "workspace_busy",
                "A Google Sheets refresh or synchronization is already running. "
                "Wait for it to finish, then retry.",
            ),
            email=email,
            retry_safe=True,
        )

    try:
        result = _run_locked(
            scoped,
            engine,
            store=store,
            spreadsheet_id=spreadsheet_id,
            source=source,
            email=email,
        )
    finally:
        release_sheets_io()

    if not result.ready or refresh is None:
        return result

    try:
        counts = refresh(spreadsheet_id)
    except GoogleSheetsError as error:
        return dataclasses.replace(
            result,
            refresh_warning=(
                f"The workspace is ready, but the local data could not be refreshed "
                f"({error.code}). Cached data is still available; retry the refresh."
            ),
        )
    except Exception:
        logger.exception("sheets_setup_refresh_failed")
        return dataclasses.replace(
            result,
            refresh_warning=(
                "The workspace is ready, but the local data could not be refreshed. "
                "Cached data is still available; retry the refresh."
            ),
        )
    return dataclasses.replace(result, counts=counts)


def _run_locked(
    scoped: Settings,
    engine: Engine,
    *,
    store: GoogleSheetsSettingsStore,
    spreadsheet_id: str,
    source: SettingsSource,
    email: str,
) -> SetupResult:
    # Stages 3 and 4: access, inspection, then schema. All are read-first or additive.
    try:
        foundation = build_google_sheets_foundation(scoped)
        foundation.check()
    except GoogleSheetsError as error:
        stage: Stage = "access"
        if error.code == "spreadsheet_id_invalid":
            stage = "spreadsheet"
        message_error = error
        if error.code in {"permission_denied", "spreadsheet_not_found", "reconnect_required"}:
            message_error = GoogleSheetsError(
                error.code,
                f"{error} Share the spreadsheet with {email} as Editor, then retry.",
            )
        return _failure(stage, message_error, email=email)

    try:
        inspection = inspect_workspace(foundation.client, spreadsheet_id)
    except GoogleSheetsError as error:
        return _failure("access", error, email=email)

    if inspection.kind is WorkspaceKind.INCOMPATIBLE:
        return _failure(
            "workspace",
            GoogleSheetsError(
                "workspace_incompatible",
                "This spreadsheet contains data that is not a Personal JobHunter "
                "workspace. Nothing was changed. Use an empty spreadsheet or one this "
                "application previously created.",
            ),
            email=email,
            preserved="Nothing was changed in the spreadsheet or the active configuration.",
        )

    try:
        foundation.repair_workspace()
    except GoogleSheetsError as error:
        return _failure(
            "schema",
            error,
            email=email,
            preserved="Existing tabs and data were preserved; only missing tabs or empty "
            "headers were eligible for creation.",
        )

    # Stage 5: optional Metrics board. Failure leaves the required tabs usable.
    optional_warning: str | None = None
    try:
        foundation.initialize_dashboard()
    except GoogleSheetsError as error:
        optional_warning = (
            f"The optional Metrics board is unavailable ({error.code}). "
            "Required tabs are ready; run the setup again after fixing the cause."
        )

    # Stage 6: persist. Only a fully validated workspace is saved.
    now = utc_now()
    store.save_spreadsheet_id(
        spreadsheet_id,
        workspace_ready_at=now,
        configured_via=source if source != "none" else "ui",
    )

    return SetupResult(
        ready=True,
        stage=None,
        code="ready",
        message="Google Sheets is connected and ready.",
        service_account_email=email,
        source=source,
        optional_warning=optional_warning,
        workspace_ready=True,
        adopted_existing=inspection.kind is WorkspaceKind.COMPATIBLE,
    )
