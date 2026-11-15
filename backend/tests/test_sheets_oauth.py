"""Tests for the Google desktop OAuth flow and credential refresh."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from google.auth.exceptions import RefreshError

from personal_job_finder_api.integrations.google_sheets.config import SHEETS_SCOPE
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.oauth import (
    GoogleOAuthAuthorizer,
    GoogleSheetsCredentialsProvider,
    validate_oauth_callback,
)


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def load(self) -> str | None:
        return self.value

    def save(self, value: str) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = None


class FakeCredentials:
    def __init__(
        self,
        *,
        expired: bool = False,
        valid: bool = True,
        refresh_token: str | None = "synthetic-refresh",
        error: Exception | None = None,
    ) -> None:
        self.expired = expired
        self.valid = valid
        self.refresh_token = refresh_token
        self.error = error
        self.refreshed = 0

    def refresh(self, request: object) -> None:
        self.refreshed += 1
        if self.error is not None:
            raise self.error
        self.expired = False
        self.valid = True

    def to_json(self) -> str:
        return '{"token":"refreshed-synthetic"}'


class FakeFlow:
    def __init__(self, credentials: FakeCredentials | None = None) -> None:
        self.redirect_uri: str | None = None
        self.credentials = credentials or FakeCredentials()
        self.authorization_kwargs: dict[str, Any] = {}
        self.authorization_url_value = "https://accounts.example.test/o/oauth2/auth"
        self.code: str | None = None
        self.fetch_error: Exception | None = None

    def authorization_url(self, **kwargs: Any) -> tuple[str, str]:
        self.authorization_kwargs = kwargs
        return self.authorization_url_value, str(kwargs["state"])

    def fetch_token(self, code: str) -> None:
        if self.fetch_error is not None:
            raise self.fetch_error
        self.code = code


class FakeCallbackServer:
    def __init__(self, query: Mapping[str, Sequence[str]]) -> None:
        self.redirect_uri = "http://127.0.0.1:54321/oauth2callback"
        self.query = query
        self.closed = False
        self.timeout: float | None = None

    def wait_for_callback(self, *, timeout_seconds: float) -> Mapping[str, Sequence[str]]:
        self.timeout = timeout_seconds
        return self.query

    def close(self) -> None:
        self.closed = True


def test_callback_accepts_matching_state_and_returns_the_code() -> None:
    code = validate_oauth_callback(
        {"state": ["expected-state"], "code": ["authorization-code"]},
        "expected-state",
    )

    assert code == "authorization-code"


def test_callback_rejects_state_mismatch() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        validate_oauth_callback(
            {"state": ["wrong-state"], "code": ["authorization-code"]},
            "expected-state",
        )

    assert captured.value.code == "oauth_state_mismatch"


def test_callback_rejects_missing_state() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        validate_oauth_callback({"code": ["authorization-code"]}, "expected-state")

    assert captured.value.code == "oauth_state_mismatch"


def test_callback_rejects_provider_denial_without_echoing_details() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        validate_oauth_callback(
            {
                "error": ["access_denied"],
                "error_description": ["private account detail"],
            },
            "expected-state",
        )

    assert captured.value.code == "oauth_denied"
    assert "private account detail" not in str(captured.value)


def test_callback_rejects_missing_code() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        validate_oauth_callback({"state": ["expected-state"]}, "expected-state")

    assert captured.value.code == "oauth_missing_code"


def test_authorizer_opens_browser_with_loopback_and_offline_consent() -> None:
    store = MemoryCredentialStore()
    flow = FakeFlow()
    server = FakeCallbackServer({"state": ["expected-state"], "code": ["authorization-code"]})
    opened: list[str] = []
    scopes_seen: list[Sequence[str]] = []

    def flow_factory(config: Mapping[str, Any], scopes: Sequence[str]) -> FakeFlow:
        scopes_seen.append(scopes)
        return flow

    authorizer = GoogleOAuthAuthorizer(
        client_config_loader=lambda: {"installed": {"client_id": "synthetic"}},
        flow_factory=flow_factory,
        credential_store=store,
        callback_server_factory=lambda: server,
        browser_opener=lambda url: opened.append(url) or True,
        state_factory=lambda: "expected-state",
        timeout_seconds=30,
    )

    authorizer.authorize()

    assert flow.redirect_uri == server.redirect_uri
    assert server.redirect_uri.startswith("http://127.0.0.1:")
    assert server.redirect_uri.endswith("/oauth2callback")
    assert opened == [flow.authorization_url_value]
    assert flow.authorization_kwargs["state"] == "expected-state"
    assert flow.authorization_kwargs["access_type"] == "offline"
    assert flow.authorization_kwargs["prompt"] == "consent"
    assert flow.code == "authorization-code"
    assert store.value == flow.credentials.to_json()
    assert server.timeout == 30
    assert server.closed
    assert scopes_seen == [SHEETS_SCOPE]


def test_authorizer_does_not_exchange_a_code_for_a_mismatched_state() -> None:
    store = MemoryCredentialStore()
    flow = FakeFlow()
    server = FakeCallbackServer({"state": ["wrong"], "code": ["authorization-code"]})

    authorizer = GoogleOAuthAuthorizer(
        client_config_loader=lambda: {"installed": {"client_id": "synthetic"}},
        flow_factory=lambda config, scopes: flow,
        credential_store=store,
        callback_server_factory=lambda: server,
        browser_opener=lambda url: True,
        state_factory=lambda: "expected-state",
    )

    with pytest.raises(GoogleSheetsError) as captured:
        authorizer.authorize()

    assert captured.value.code == "oauth_state_mismatch"
    assert flow.code is None
    assert store.value is None
    assert server.closed


def test_authorizer_does_not_exchange_a_denied_request() -> None:
    store = MemoryCredentialStore()
    flow = FakeFlow()
    server = FakeCallbackServer({"state": ["expected-state"], "error": ["access_denied"]})

    authorizer = GoogleOAuthAuthorizer(
        client_config_loader=lambda: {"installed": {"client_id": "synthetic"}},
        flow_factory=lambda config, scopes: flow,
        credential_store=store,
        callback_server_factory=lambda: server,
        browser_opener=lambda url: True,
        state_factory=lambda: "expected-state",
    )

    with pytest.raises(GoogleSheetsError) as captured:
        authorizer.authorize()

    assert captured.value.code == "oauth_denied"
    assert flow.code is None
    assert store.value is None


def provider(
    store: MemoryCredentialStore,
    credentials: FakeCredentials,
    *,
    loader_error: Exception | None = None,
) -> GoogleSheetsCredentialsProvider:
    def load(info: Mapping[str, Any], scopes: Sequence[str]) -> FakeCredentials:
        if loader_error is not None:
            raise loader_error
        return credentials

    return GoogleSheetsCredentialsProvider(
        store,
        credentials_loader=load,
        request_factory=object,
    )


def test_credentials_provider_requires_authorization() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        provider(MemoryCredentialStore(), FakeCredentials()).load()

    assert captured.value.code == "credentials_missing"


def test_credentials_provider_refreshes_expired_credentials_and_saves_them() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')
    credentials = FakeCredentials(expired=True, valid=True)

    loaded = provider(store, credentials).load()

    assert loaded is credentials
    assert credentials.refreshed == 1
    assert store.value == '{"token":"refreshed-synthetic"}'


def test_credentials_provider_does_not_refresh_valid_credentials() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')
    credentials = FakeCredentials(expired=False, valid=True)

    assert provider(store, credentials).load() is credentials
    assert credentials.refreshed == 0


def test_revoked_credentials_require_reconnection_without_leaking_details() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')
    credentials = FakeCredentials(
        expired=True,
        error=RefreshError("invalid_grant token=super-secret"),
    )

    with pytest.raises(GoogleSheetsError) as captured:
        provider(store, credentials).load()

    assert captured.value.code == "reconnect_required"
    assert captured.value.reconnect_required is True
    assert "super-secret" not in str(captured.value)


def test_expired_credentials_without_a_refresh_token_require_reconnection() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')

    with pytest.raises(GoogleSheetsError) as captured:
        provider(
            store,
            FakeCredentials(expired=True, valid=False, refresh_token=None),
        ).load()

    assert captured.value.code == "reconnect_required"


def test_malformed_stored_credentials_require_reconnection() -> None:
    store = MemoryCredentialStore()
    store.save("{not-json")

    with pytest.raises(GoogleSheetsError) as captured:
        provider(store, FakeCredentials()).load()

    assert captured.value.code == "credentials_invalid"


def test_credentials_loader_failure_requires_reconnection() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')

    with pytest.raises(GoogleSheetsError) as captured:
        provider(
            store,
            FakeCredentials(),
            loader_error=ValueError("private token detail"),
        ).load()

    assert captured.value.code == "credentials_invalid"
    assert "private token detail" not in str(captured.value)


def test_authorizer_sanitizes_a_failed_code_exchange() -> None:
    store = MemoryCredentialStore()
    flow = FakeFlow()
    flow.fetch_error = RuntimeError('token endpoint body: {"access_token":"super-secret"}')
    server = FakeCallbackServer({"state": ["expected-state"], "code": ["authorization-code"]})

    authorizer = GoogleOAuthAuthorizer(
        client_config_loader=lambda: {"installed": {"client_id": "synthetic"}},
        flow_factory=lambda config, scopes: flow,
        credential_store=store,
        callback_server_factory=lambda: server,
        browser_opener=lambda url: True,
        state_factory=lambda: "expected-state",
    )

    with pytest.raises(GoogleSheetsError) as captured:
        authorizer.authorize()

    assert captured.value.code == "oauth_exchange_failed"
    assert "super-secret" not in str(captured.value)
    assert store.value is None
    assert server.closed


def test_authorizer_reports_an_unusable_browser() -> None:
    store = MemoryCredentialStore()
    flow = FakeFlow()
    server = FakeCallbackServer({"state": ["expected-state"], "code": ["code"]})

    authorizer = GoogleOAuthAuthorizer(
        client_config_loader=lambda: {"installed": {"client_id": "synthetic"}},
        flow_factory=lambda config, scopes: flow,
        credential_store=store,
        callback_server_factory=lambda: server,
        browser_opener=lambda url: False,
        state_factory=lambda: "expected-state",
    )

    with pytest.raises(GoogleSheetsError) as captured:
        authorizer.authorize()

    assert captured.value.code == "oauth_browser_unavailable"
    assert flow.code is None
    assert server.closed


def test_revoked_credentials_are_removed_from_the_store() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')
    credentials = FakeCredentials(
        expired=True,
        error=RefreshError("invalid_grant"),
    )

    with pytest.raises(GoogleSheetsError):
        provider(store, credentials).load()

    assert store.value is None


def test_transient_refresh_failure_keeps_the_stored_credential() -> None:
    store = MemoryCredentialStore()
    store.save('{"refresh_token":"synthetic"}')
    credentials = FakeCredentials(
        expired=True,
        error=ConnectionError("network down"),
    )

    with pytest.raises(GoogleSheetsError) as captured:
        provider(store, credentials).load()

    assert captured.value.code == "reconnect_required"
    assert store.value == '{"refresh_token":"synthetic"}'
