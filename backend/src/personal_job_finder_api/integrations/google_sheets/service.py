"""Composition root for the Google Sheets integration.

Every Sheets operation goes through this boundary. Credentials are the Service
Account key at the canonical local path; there is no interactive user auth in V1.
Transport injection keeps the whole foundation testable without live requests.
"""

from __future__ import annotations

from dataclasses import dataclass

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleApiSheetsTransport,
    GoogleSheetsClient,
    SheetsTransport,
    build_google_sheets_service,
)
from personal_job_finder_api.integrations.google_sheets.config import GoogleSheetsConfig
from personal_job_finder_api.integrations.google_sheets.dashboard import (
    MetricsInitResult,
    MetricsInitService,
)
from personal_job_finder_api.integrations.google_sheets.dashboard_cleanup import (
    DashboardRemovalService,
)
from personal_job_finder_api.integrations.google_sheets.preflight import (
    PreflightResult,
    SpreadsheetPreflightService,
)
from personal_job_finder_api.integrations.google_sheets.service_account_store import (
    load_service_account_credentials,
)
from personal_job_finder_api.integrations.google_sheets.workspace_repair import (
    RepairOutcome,
    WorkspaceRepairService,
)


@dataclass(frozen=True, slots=True)
class GoogleSheetsFoundation:
    """Validated configuration plus the client and preflight services."""

    config: GoogleSheetsConfig
    client: GoogleSheetsClient
    preflight: SpreadsheetPreflightService
    transport: SheetsTransport

    def check(self) -> PreflightResult:
        return self.preflight.check(self.config.spreadsheet_id)

    def initialize(self) -> PreflightResult:
        return self.preflight.initialize(self.config.spreadsheet_id)

    def initialize_dashboard(self, *, force: bool = False) -> MetricsInitResult:
        """Create and initialize the optional metric board (``Metrics``)."""

        return MetricsInitService(self.client, self.config.spreadsheet_id).initialize(force=force)

    def remove_dashboard(self) -> str:
        """Delete the retired ``Dashboard`` cards tab when this project owns it."""

        return DashboardRemovalService(self.client, self.config.spreadsheet_id).remove()

    def repair_workspace(self, *, force: bool = False) -> RepairOutcome:
        """Repair workbook structure without touching user data."""

        return WorkspaceRepairService(self.client, self.config.spreadsheet_id).repair(force=force)


def build_service_account_sheets_service(settings: Settings, *, timeout_seconds: float):
    credentials = load_service_account_credentials(settings)
    return build_google_sheets_service(credentials=credentials, timeout_seconds=timeout_seconds)


def build_google_sheets_transport(settings: Settings) -> SheetsTransport:
    """Build the production transport. The spreadsheet ID is passed per call, so
    the transport does not require one to exist (UI-configured installs)."""

    service = build_service_account_sheets_service(
        settings,
        timeout_seconds=settings.google_sheets_timeout_seconds,
    )
    return GoogleApiSheetsTransport(service)


def build_google_sheets_foundation(
    settings: Settings,
    *,
    transport: SheetsTransport | None = None,
) -> GoogleSheetsFoundation:
    """Build the foundation, using a fake transport when one is supplied."""

    config = GoogleSheetsConfig.from_settings(settings)
    if transport is None:
        service = build_service_account_sheets_service(
            settings,
            timeout_seconds=config.timeout_seconds,
        )
        transport = GoogleApiSheetsTransport(service)

    client = GoogleSheetsClient(
        transport,
        max_batch_ranges=config.max_batch_ranges,
    )
    return GoogleSheetsFoundation(
        config=config,
        client=client,
        preflight=SpreadsheetPreflightService(transport, client=client),
        transport=transport,
    )
