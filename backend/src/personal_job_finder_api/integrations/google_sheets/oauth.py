"""Google desktop OAuth flow for a single local user.

Only the Google Sheets scope is requested. Authorization opens in the user's
system browser and returns through a loopback callback on ``127.0.0.1``. The
resulting credentials are stored through the private credential store, never in
SQLite or application logs.
"""

from __future__ import annotations

import hmac
import json
import secrets
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.config import (
    SHEETS_SCOPE,
    load_oauth_client_config,
)
from personal_job_finder_api.integrations.google_sheets.credentials import (
    CredentialStore,
    CredentialStoreError,
    build_credential_store,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

DEFAULT_OAUTH_TIMEOUT_SECONDS = 300.0
CALLBACK_PATH = "/oauth2callback"


class OAuthCallbackServer(Protocol):
    """Loopback server waiting for the Google authorization response."""

    redirect_uri: str

    def wait_for_callback(
        self,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Sequence[str]]: ...

    def close(self) -> None: ...


class OAuthFlow(Protocol):
    """Subset of ``google_auth_oauthlib.flow.Flow`` used by the authorizer."""

    redirect_uri: str | None
    credentials: Any

    def authorization_url(self, **kwargs: Any) -> tuple[str, str]: ...

    def fetch_token(self, code: str) -> None: ...


def validate_oauth_callback(
    query: Mapping[str, Sequence[str]],
    expected_state: str,
) -> str:
    """Validate a callback and return the authorization code.

    Provider denial details are deliberately discarded because they can contain
    account-specific information.
    """

    if _first_value(query, "error") is not None:
        raise GoogleSheetsError(
            "oauth_denied",
            "Google authorization was denied. Run the authorization command "
            "again and grant access to the spreadsheet.",
            reconnect_required=True,
        )

    actual_state = _first_value(query, "state")
    if actual_state is None or not hmac.compare_digest(actual_state, expected_state):
        raise GoogleSheetsError(
            "oauth_state_mismatch",
            "The Google authorization response did not match this request. "
            "Start authorization again.",
            reconnect_required=True,
        )

    code = _first_value(query, "code")
    if not code:
        raise GoogleSheetsError(
            "oauth_missing_code",
            "The Google authorization response did not include a code. Start authorization again.",
            reconnect_required=True,
        )
    return code


def _first_value(
    query: Mapping[str, Sequence[str]],
    key: str,
) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


class _OAuthLoopbackHttpServer(HTTPServer):
    received_query: Mapping[str, Sequence[str]] | None = None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: _OAuthLoopbackHttpServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlsplit(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        # Ignore unrelated local requests (browser prefetch, port scanners). Only
        # a response carrying an authorization code or an explicit denial can
        # complete or abort the flow.
        if not query.get("code") and not query.get("error"):
            self.send_error(400)
            return
        self.server.received_query = query
        body = b"Authorization received. You can close this window."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Never log callback queries: they contain an authorization code.
        return


class LoopbackCallbackServer:
    """One-shot HTTP server bound to an ephemeral loopback port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = _OAuthLoopbackHttpServer((host, port), _OAuthCallbackHandler)
        self._server.timeout = 1.0
        self.redirect_uri = f"http://{host}:{self._server.server_port}{CALLBACK_PATH}"

    def wait_for_callback(
        self,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Sequence[str]]:
        deadline = monotonic() + max(timeout_seconds, 0.1)
        while monotonic() < deadline:
            self._server.timeout = max(0.1, deadline - monotonic())
            self._server.handle_request()
            if self._server.received_query is not None:
                return self._server.received_query
        raise GoogleSheetsError(
            "oauth_timeout",
            "Google authorization timed out. Start authorization again.",
            reconnect_required=True,
        )

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> LoopbackCallbackServer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _installed_app_flow_factory(
    client_config: Mapping[str, Any],
    scopes: Sequence[str],
) -> OAuthFlow:
    return InstalledAppFlow.from_client_config(
        dict(client_config),
        list(scopes),
    )


class GoogleOAuthAuthorizer:
    """Run the desktop authorization flow and persist the resulting token."""

    def __init__(
        self,
        *,
        client_config_loader: Callable[[], Mapping[str, Any]],
        credential_store: CredentialStore,
        flow_factory: Callable[
            [Mapping[str, Any], Sequence[str]],
            OAuthFlow,
        ] = _installed_app_flow_factory,
        callback_server_factory: Callable[[], OAuthCallbackServer] = (LoopbackCallbackServer),
        browser_opener: Callable[[str], bool] = webbrowser.open,
        state_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        timeout_seconds: float = DEFAULT_OAUTH_TIMEOUT_SECONDS,
    ) -> None:
        self._client_config_loader = client_config_loader
        self._credential_store = credential_store
        self._flow_factory = flow_factory
        self._callback_server_factory = callback_server_factory
        self._browser_opener = browser_opener
        self._state_factory = state_factory
        self._timeout_seconds = timeout_seconds

    def authorize(self) -> None:
        """Authorize in the system browser and save the refresh token."""

        client_config = self._client_config_loader()
        server = self._callback_server_factory()
        try:
            flow = self._flow_factory(client_config, SHEETS_SCOPE)
            flow.redirect_uri = server.redirect_uri
            state = self._state_factory()
            authorization_url, _ = flow.authorization_url(
                state=state,
                access_type="offline",
                prompt="consent",
            )
            if not self._browser_opener(authorization_url):
                raise GoogleSheetsError(
                    "oauth_browser_unavailable",
                    "The system browser could not be opened. Start authorization "
                    "again from a desktop session with a default browser.",
                    reconnect_required=True,
                )
            query = server.wait_for_callback(
                timeout_seconds=self._timeout_seconds,
            )
            code = validate_oauth_callback(query, state)
            try:
                flow.fetch_token(code=code)
            except GoogleSheetsError:
                raise
            except Exception:
                # The token endpoint response can contain access/refresh tokens
                # and account details. Never let it reach a traceback or log.
                raise GoogleSheetsError(
                    "oauth_exchange_failed",
                    "The Google authorization could not be completed. Start authorization again.",
                    reconnect_required=True,
                ) from None
            try:
                self._credential_store.save(flow.credentials.to_json())
            except CredentialStoreError:
                raise GoogleSheetsError(
                    "credentials_storage_failed",
                    "Google authorization succeeded, but the credential could "
                    "not be stored securely. Check the OS keyring or private "
                    "data directory.",
                    reconnect_required=True,
                ) from None
        finally:
            server.close()


class GoogleSheetsCredentialsProvider:
    """Load, refresh, and persist Google credentials for API calls."""

    def __init__(
        self,
        credential_store: CredentialStore,
        *,
        credentials_loader: Callable[
            [Mapping[str, Any], Sequence[str]],
            Any,
        ] = lambda info, scopes: Credentials.from_authorized_user_info(
            dict(info),
            scopes=list(scopes),
        ),
        request_factory: Callable[[], Any] = Request,
    ) -> None:
        self._credential_store = credential_store
        self._credentials_loader = credentials_loader
        self._request_factory = request_factory

    def load(self) -> Any:
        """Return usable credentials or raise a reconnect-required error."""

        try:
            stored = self._credential_store.load()
        except CredentialStoreError:
            raise GoogleSheetsError(
                "credentials_invalid",
                "Stored Google credentials could not be read. Reconnect the Google account.",
                reconnect_required=True,
            ) from None

        if stored is None:
            raise GoogleSheetsError(
                "credentials_missing",
                "No Google account is connected. Run the Google Sheets "
                "authorization command first.",
                reconnect_required=True,
            )

        try:
            info = json.loads(stored)
        except (TypeError, ValueError):
            raise GoogleSheetsError(
                "credentials_invalid",
                "Stored Google credentials are invalid. Reconnect the Google account.",
                reconnect_required=True,
            ) from None
        if not isinstance(info, dict):
            raise GoogleSheetsError(
                "credentials_invalid",
                "Stored Google credentials are invalid. Reconnect the Google account.",
                reconnect_required=True,
            )

        try:
            credentials = self._credentials_loader(info, SHEETS_SCOPE)
        except Exception:
            raise GoogleSheetsError(
                "credentials_invalid",
                "Stored Google credentials are invalid. Reconnect the Google account.",
                reconnect_required=True,
            ) from None

        if getattr(credentials, "expired", False):
            if not getattr(credentials, "refresh_token", None):
                raise GoogleSheetsError(
                    "reconnect_required",
                    "Google authorization expired and cannot be refreshed. "
                    "Reconnect the Google account.",
                    reconnect_required=True,
                )
            try:
                credentials.refresh(self._request_factory())
            except RefreshError:
                # A revoked or expired grant cannot recover without user action.
                # Remove the unusable credential so the next attempt reports
                # "not connected" instead of repeating a doomed refresh.
                self._discard_stored_credential()
                raise GoogleSheetsError(
                    "reconnect_required",
                    "Google authorization is no longer valid. Reconnect the Google account.",
                    reconnect_required=True,
                ) from None
            except Exception:
                raise GoogleSheetsError(
                    "reconnect_required",
                    "Google authorization could not be refreshed. Reconnect the Google account.",
                    reconnect_required=True,
                ) from None
            try:
                self._credential_store.save(credentials.to_json())
            except CredentialStoreError:
                raise GoogleSheetsError(
                    "credentials_storage_failed",
                    "Google credentials were refreshed but could not be stored securely.",
                    reconnect_required=True,
                ) from None

        if not getattr(credentials, "valid", False):
            raise GoogleSheetsError(
                "reconnect_required",
                "Google authorization is no longer valid. Reconnect the Google account.",
                reconnect_required=True,
            )
        return credentials

    def _discard_stored_credential(self) -> None:
        """Best-effort removal of a credential that cannot be used anymore."""

        try:
            self._credential_store.delete()
        except CredentialStoreError:
            return


def build_google_oauth_authorizer(
    settings: Settings,
    *,
    credential_store: CredentialStore | None = None,
) -> GoogleOAuthAuthorizer:
    """Build the production desktop authorizer from local settings."""

    store = credential_store or build_credential_store(settings)
    client_file = settings.google_oauth_client_file
    return GoogleOAuthAuthorizer(
        client_config_loader=lambda: load_oauth_client_config(client_file),
        credential_store=store,
        timeout_seconds=settings.google_oauth_timeout_seconds,
    )


def build_google_credentials_provider(
    settings: Settings,
    *,
    credential_store: CredentialStore | None = None,
) -> GoogleSheetsCredentialsProvider:
    """Build the production credential provider from local settings."""

    return GoogleSheetsCredentialsProvider(credential_store or build_credential_store(settings))
