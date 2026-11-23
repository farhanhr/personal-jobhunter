"""Private local storage and credential construction for the Service Account key.

There is exactly one location: ``Settings.google_service_account_file``, which is
``<repository>/.local/credentials/google-service-account.json``. Storage uses the
restrictive-permission file store (``0600`` file, ``0700`` directory, atomic
replacement, symlinks refused).
"""

from __future__ import annotations

import json
from typing import Any

from google.oauth2 import service_account

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.config import SHEETS_SCOPE
from personal_job_finder_api.integrations.google_sheets.credentials import (
    CredentialStoreError,
    FileCredentialStore,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.service_account import (
    ServiceAccountKey,
    parse_service_account_key,
)


def build_service_account_store(settings: Settings) -> FileCredentialStore:
    return FileCredentialStore(settings.google_service_account_file)


def save_service_account_key(settings: Settings, key: ServiceAccountKey) -> None:
    """Persist the canonical JSON form of a validated key atomically."""

    try:
        build_service_account_store(settings).save(json.dumps(key.info, indent=2, sort_keys=True))
    except CredentialStoreError as exc:
        raise GoogleSheetsError(
            "credentials_storage_failed",
            "The Service Account key could not be stored securely. Check the local "
            "credentials directory permissions and try again.",
        ) from exc


def load_service_account_text(settings: Settings) -> str | None:
    """Return the stored key text. Callers must never expose it."""

    try:
        return build_service_account_store(settings).load()
    except CredentialStoreError as exc:
        raise GoogleSheetsError(
            "credentials_invalid",
            "The stored Service Account key could not be read. Upload it again.",
        ) from exc


def load_service_account_credentials(settings: Settings) -> Any:
    """Build Google credentials from the stored key, for API calls only."""

    text = load_service_account_text(settings)
    if text is None:
        raise GoogleSheetsError(
            "credentials_missing",
            "No Service Account key is configured. Upload the key in Settings first.",
        )
    key = parse_service_account_key(text.encode("utf-8"))
    return service_account.Credentials.from_service_account_info(
        key.info,
        scopes=list(SHEETS_SCOPE),
    )


def delete_service_account_key(settings: Settings) -> None:
    try:
        build_service_account_store(settings).delete()
    except CredentialStoreError as exc:
        raise GoogleSheetsError(
            "credentials_storage_failed",
            "The stored Service Account key could not be removed.",
        ) from exc
