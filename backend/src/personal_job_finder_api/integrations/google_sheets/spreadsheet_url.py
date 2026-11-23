"""Parse Google Sheets URLs into spreadsheet identifiers.

Errors never echo the submitted URL or the extracted identifier.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from personal_job_finder_api.integrations.google_sheets.config import (
    MAX_SPREADSHEET_ID_LENGTH,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

_ALLOWED_HOST = "docs.google.com"
_PATH = re.compile(rf"^/spreadsheets/d/([A-Za-z0-9_-]{{20,{MAX_SPREADSHEET_ID_LENGTH}}})(?:/.*)?$")


def extract_spreadsheet_id(value: str) -> str:
    """Return the spreadsheet identifier from a full Google Sheets URL."""

    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        raise _invalid()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        raise _invalid() from None
    if parts.scheme != "https" or parts.hostname != _ALLOWED_HOST:
        raise _invalid()
    if parts.username is not None or parts.password is not None or parts.port not in (None, 443):
        raise _invalid()
    match = _PATH.fullmatch(parts.path)
    if match is None:
        raise _invalid()
    return match.group(1)


def _invalid() -> GoogleSheetsError:
    return GoogleSheetsError(
        "spreadsheet_url_invalid",
        "Enter the full Google Sheets address that starts with https://docs.google.com/spreadsheets/d/.",
    )
