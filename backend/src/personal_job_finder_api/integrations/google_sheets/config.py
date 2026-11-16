"""Local Google Sheets configuration and the accepted spreadsheet schema.

Spreadsheet IDs, OAuth client files, and token locations are local private
settings. They are never committed and never included in public errors.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

SHEETS_SCOPE = ("https://www.googleapis.com/auth/spreadsheets",)
SHEETS_API_ORIGIN = "https://sheets.googleapis.com"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BATCH_RANGES = 100
DEFAULT_BATCH_SIZE = 25
MAX_SPREADSHEET_ID_LENGTH = 200

_SPREADSHEET_ID = re.compile(r"^[A-Za-z0-9_-]{20,200}$")
_PLACEHOLDER_PREFIXES = ("replace_with", "your_", "changeme")

# These columns follow docs/p0/12-data-and-sync-contract.md. The first column is
# the stable identity used for future idempotent upserts; the writer must map
# outbox payloads onto these names in M4B2.
OPPORTUNITY_HEADERS = (
    "opportunity_id",
    "company_name",
    "job_title_original",
    "job_title_normalized",
    "target_role_id",
    "role_match_type",
    "role_match_reason",
    "location_raw",
    "country",
    "province",
    "city",
    "location_status",
    "work_mode",
    "remote_scope",
    "employment_type",
    "experience_level",
    "description",
    "required_skills_json",
    "posted_at",
    "deadline",
    "availability_status",
    "user_status",
    "first_seen_at_utc",
    "last_seen_at_utc",
    "created_at_utc",
    "updated_at_utc",
    # Application-tracking fields belong to the user, not the connector. They are
    # appended so every existing discovery column keeps its position.
    "job_type",
    "date_applied",
    "application_status",
    "follow_up_date",
    "interview_date",
    "vacancy_url",
    "notes",
)

OPPORTUNITY_TRACKER_HEADERS = (
    "job_type",
    "date_applied",
    "application_status",
    "follow_up_date",
    "interview_date",
    "vacancy_url",
    "notes",
)

# Columns the user owns in the canonical Opportunities tab. A connector update
# must copy these values from the existing sheet row, never derive them.
OPPORTUNITY_USER_OWNED_HEADERS = ("user_status", *OPPORTUNITY_TRACKER_HEADERS)

# Reserved outbox payload key listing user-owned columns with a pending local
# edit. ``upsert_outbox`` merges these values across discovery re-ingestion so a
# connector refresh cannot discard an unsynced tracker change.
PENDING_USER_FIELDS_KEY = "_pending_user_fields"

JOB_SOURCE_HEADERS = (
    "source_id",
    "opportunity_id",
    "source_type",
    "source_url",
    "external_source_id",
    "author_name",
    "author_role",
    "source_text_excerpt",
    "posted_at",
    "captured_at_utc",
    "last_seen_at_utc",
    "provenance_method",
    "observation_status",
)

REQUIRED_TABS: Mapping[str, tuple[str, ...]] = {
    "Opportunities": OPPORTUNITY_HEADERS,
    "Job_Sources": JOB_SOURCE_HEADERS,
}


@dataclass(frozen=True, slots=True)
class GoogleSheetsConfig:
    """Validated local spreadsheet settings."""

    spreadsheet_id: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_batch_ranges: int = DEFAULT_MAX_BATCH_RANGES
    batch_size: int = DEFAULT_BATCH_SIZE

    def __post_init__(self) -> None:
        _validate_spreadsheet_id(self.spreadsheet_id)
        if self.timeout_seconds <= 0:
            raise GoogleSheetsError(
                "timeout_invalid",
                "The Google Sheets timeout must be greater than zero.",
            )
        if self.max_batch_ranges <= 0:
            raise GoogleSheetsError(
                "batch_limit_invalid",
                "The Google Sheets batch limit must be greater than zero.",
            )
        if self.batch_size <= 0:
            raise GoogleSheetsError(
                "batch_size_invalid",
                "The Google Sheets batch size must be greater than zero.",
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> GoogleSheetsConfig:
        return cls(
            spreadsheet_id=settings.google_sheets_spreadsheet_id or "",
            timeout_seconds=settings.google_sheets_timeout_seconds,
            max_batch_ranges=settings.google_sheets_max_batch_ranges,
            batch_size=settings.google_sheets_batch_size,
        )


def _validate_spreadsheet_id(value: str) -> None:
    candidate = value.strip()
    if not candidate:
        raise GoogleSheetsError(
            "spreadsheet_id_missing",
            "Set GOOGLE_SHEETS_SPREADSHEET_ID to the local spreadsheet identifier.",
        )
    lowered = candidate.casefold()
    if any(
        lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES
    ) or not _SPREADSHEET_ID.fullmatch(candidate):
        raise GoogleSheetsError(
            "spreadsheet_id_invalid",
            "The configured Google Sheets spreadsheet identifier is invalid.",
        )


def load_oauth_client_config(path: Path) -> dict[str, Any]:
    """Read a desktop OAuth client file without exposing its contents."""

    try:
        raw = path.read_text(encoding="utf-8")
        config = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise GoogleSheetsError(
            "oauth_configuration_invalid",
            "The Google OAuth client configuration could not be read. "
            "Download a Desktop app client JSON file and set "
            "GOOGLE_OAUTH_CLIENT_FILE.",
            reconnect_required=True,
        ) from None

    installed = config.get("installed") if isinstance(config, dict) else None
    if (
        not isinstance(installed, dict)
        or not isinstance(installed.get("client_id"), str)
        or not installed["client_id"].strip()
        or not isinstance(installed.get("client_secret"), str)
        or not installed["client_secret"].strip()
    ):
        raise GoogleSheetsError(
            "oauth_configuration_invalid",
            "The Google OAuth client configuration must describe a Desktop app "
            "client with an installed application section.",
            reconnect_required=True,
        )
    return config
