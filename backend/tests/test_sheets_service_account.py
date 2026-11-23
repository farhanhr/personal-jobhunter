"""Offline tests for Service Account key validation using synthetic data only."""

from __future__ import annotations

import json

import pytest

from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.service_account import (
    parse_service_account_key,
)

FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake-not-a-real-key\n-----END PRIVATE KEY-----\n"


def _service_account(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "type": "service_account",
        "project_id": "pjf-test-project",
        "private_key_id": "test-key-id",
        "private_key": FAKE_KEY,
        "client_email": "pjf-reader@pjf-test-project.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_valid_service_account_key_exposes_only_public_fields() -> None:
    key = parse_service_account_key(_service_account())

    assert key.client_email == "pjf-reader@pjf-test-project.iam.gserviceaccount.com"
    assert key.public_summary() == {
        "client_email": "pjf-reader@pjf-test-project.iam.gserviceaccount.com",
        "project_id": "pjf-test-project",
    }
    assert "PRIVATE KEY" not in repr(key)
    assert "PRIVATE KEY" not in str(key.public_summary())


def test_oauth_client_file_is_rejected_with_actionable_message() -> None:
    raw = json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}).encode()

    with pytest.raises(GoogleSheetsError) as caught:
        parse_service_account_key(raw)

    assert caught.value.code == "oauth_client_rejected"
    assert "Service Accounts" in str(caught.value)


def test_non_service_account_type_is_rejected() -> None:
    with pytest.raises(GoogleSheetsError) as caught:
        parse_service_account_key(_service_account(type="authorized_user"))

    assert caught.value.code == "key_type_unsupported"


@pytest.mark.parametrize("field_name", ["project_id", "private_key", "client_email"])
def test_missing_required_field_is_rejected(field_name: str) -> None:
    raw = json.loads(_service_account())
    del raw[field_name]

    with pytest.raises(GoogleSheetsError) as caught:
        parse_service_account_key(json.dumps(raw).encode())

    assert caught.value.code == "key_incomplete"


def test_malformed_json_error_does_not_echo_content() -> None:
    with pytest.raises(GoogleSheetsError) as caught:
        parse_service_account_key(b'{"private_key": "-----BEGIN PRIVATE KEY-----')

    assert caught.value.code == "key_invalid_json"
    assert "BEGIN" not in str(caught.value)


def test_oversized_upload_is_rejected_before_parsing() -> None:
    with pytest.raises(GoogleSheetsError) as caught:
        parse_service_account_key(b" " * (64 * 1024 + 1))

    assert caught.value.code == "key_too_large"


def test_non_service_account_email_is_rejected() -> None:
    with pytest.raises(GoogleSheetsError) as caught:
        parse_service_account_key(_service_account(client_email="someone@example.test"))

    assert caught.value.code == "key_incomplete"
