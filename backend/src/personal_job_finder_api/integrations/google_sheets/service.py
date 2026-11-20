"""Composition root for the Google Sheets foundation.

Future sync work should depend on this boundary instead of assembling OAuth,
transport, and preflight in API routes. Transport injection keeps the whole
foundation testable without live Google requests.
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
from personal_job_finder_api.integrations.google_sheets.credentials import (
    CredentialStore,
    build_credential_store,
)
from personal_job_finder_api.integrations.google_sheets.dashboard import (
    DashboardInitResult,
    DashboardInitService,
)
from personal_job_finder_api.integrations.google_sheets.oauth import (
    GoogleSheetsCredentialsProvider,
    build_google_credentials_provider,
)
from personal_job_finder_api.integrations.google_sheets.preflight import (
    PreflightResult,
    SpreadsheetPreflightService,
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

    def initialize_dashboard(self) -> DashboardInitResult:
        """Create and initialize the optional Dashboard tab."""

        return DashboardInitService(self.client, self.config.spreadsheet_id).initialize()


def build_google_sheets_transport(settings: Settings) -> SheetsTransport:
    """Build the production transport from local settings."""

    config = GoogleSheetsConfig.from_settings(settings)
    credentials = build_google_credentials_provider(settings).load()
    service = build_google_sheets_service(
        credentials=credentials,
        timeout_seconds=config.timeout_seconds,
    )
    return GoogleApiSheetsTransport(service)


def build_google_sheets_foundation(
    settings: Settings,
    *,
    transport: SheetsTransport | None = None,
    credential_store: CredentialStore | None = None,
) -> GoogleSheetsFoundation:
    """Build the foundation, using a fake transport when one is supplied."""

    config = GoogleSheetsConfig.from_settings(settings)
    if transport is None:
        store = credential_store or build_credential_store(settings)
        credentials = GoogleSheetsCredentialsProvider(store).load()
        service = build_google_sheets_service(
            credentials=credentials,
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
