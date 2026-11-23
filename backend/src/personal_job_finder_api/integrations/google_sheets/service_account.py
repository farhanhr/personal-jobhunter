"""Validation of Google Cloud Service Account JSON keys.

Only the public ``client_email`` and ``project_id`` may leave this module. The
private key is kept in memory for credential construction and must never appear
in API responses, logs, or error messages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

SERVICE_ACCOUNT_TYPE = "service_account"
MAX_KEY_BYTES = 64 * 1024
_REQUIRED_FIELDS = ("project_id", "private_key_id", "private_key", "client_email", "token_uri")
_EMAIL_SUFFIXES = (".iam.gserviceaccount.com", "@developer.gserviceaccount.com")
_OAUTH_CLIENT_SECTIONS = ("installed", "web")


@dataclass(frozen=True, slots=True)
class ServiceAccountKey:
    """A validated Service Account key. ``info`` contains secret material."""

    client_email: str
    project_id: str
    info: dict[str, Any] = field(repr=False)

    def public_summary(self) -> dict[str, str]:
        return {"client_email": self.client_email, "project_id": self.project_id}


def parse_service_account_key(raw: bytes) -> ServiceAccountKey:
    """Validate uploaded bytes as a Service Account key without echoing them."""

    if len(raw) > MAX_KEY_BYTES:
        raise GoogleSheetsError(
            "key_too_large",
            "The uploaded file is larger than the 64 KB limit for a Service Account key.",
        )
    try:
        info = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GoogleSheetsError(
            "key_invalid_json",
            "The uploaded file is not valid JSON. Download the Service Account key again.",
        ) from None
    if not isinstance(info, dict):
        raise GoogleSheetsError(
            "key_invalid_json",
            "The uploaded file is not a Service Account key object.",
        )

    if any(section in info for section in _OAUTH_CLIENT_SECTIONS):
        raise GoogleSheetsError(
            "oauth_client_rejected",
            "This is a Google OAuth client file, not a Service Account key. In Google "
            "Cloud Console open IAM & Admin → Service Accounts, create a service account, "
            "then use Keys → Add key → Create new key → JSON.",
        )
    if info.get("type") != SERVICE_ACCOUNT_TYPE:
        raise GoogleSheetsError(
            "key_type_unsupported",
            "The uploaded JSON is not a Google Cloud Service Account key (type must be "
            "service_account).",
        )

    for name in _REQUIRED_FIELDS:
        value = info.get(name)
        if not isinstance(value, str) or not value.strip():
            raise GoogleSheetsError(
                "key_incomplete",
                "The Service Account key is incomplete. Download a new JSON key from "
                "Google Cloud Console.",
            )

    client_email = info["client_email"].strip()
    if not client_email.endswith(_EMAIL_SUFFIXES):
        raise GoogleSheetsError(
            "key_incomplete",
            "The Service Account key does not contain a valid service account email.",
        )
    if not info["private_key"].lstrip().startswith("-----BEGIN"):
        raise GoogleSheetsError(
            "key_incomplete",
            "The Service Account key does not contain a usable private key.",
        )

    return ServiceAccountKey(
        client_email=client_email,
        project_id=info["project_id"].strip(),
        info=info,
    )
