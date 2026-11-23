"""Offline tests for Service Account key storage.

Each test overrides the credential file to a temporary path. The real key under
``.local/credentials/`` is never read or written by the test suite.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.service_account import (
    parse_service_account_key,
)
from personal_job_finder_api.integrations.google_sheets.service_account_store import (
    delete_service_account_key,
    load_service_account_text,
    save_service_account_key,
)

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake-not-a-real-key\n-----END PRIVATE KEY-----\n"


def _key():
    raw = json.dumps(
        {
            "type": "service_account",
            "project_id": "pjf-test-project",
            "private_key_id": "test-key-id",
            "private_key": FAKE_KEY,
            "client_email": "pjf-reader@pjf-test-project.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ).encode()
    return parse_service_account_key(raw)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    base = Settings(data_dir=tmp_path)
    target = tmp_path / "local" / "credentials" / "google-service-account.json"
    monkeypatch.setattr(
        Settings,
        "google_service_account_file",
        property(lambda self: target),
    )
    return base


def test_save_round_trip_uses_private_permissions(settings: Settings) -> None:
    save_service_account_key(settings, _key())

    path = settings.google_service_account_file
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stored = json.loads(load_service_account_text(settings) or "{}")
    assert stored["client_email"].endswith(".iam.gserviceaccount.com")


def test_save_replaces_existing_key_without_leftovers(settings: Settings) -> None:
    save_service_account_key(settings, _key())
    save_service_account_key(settings, _key())

    names = [p.name for p in settings.google_service_account_file.parent.iterdir()]
    assert names == ["google-service-account.json"]


def test_symlinked_credential_file_is_refused(settings: Settings, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    path = settings.google_service_account_file
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    with pytest.raises(GoogleSheetsError) as caught:
        save_service_account_key(settings, _key())

    assert caught.value.code == "credentials_storage_failed"


def test_delete_removes_the_key_and_is_idempotent(settings: Settings) -> None:
    save_service_account_key(settings, _key())

    delete_service_account_key(settings)
    delete_service_account_key(settings)

    assert load_service_account_text(settings) is None
