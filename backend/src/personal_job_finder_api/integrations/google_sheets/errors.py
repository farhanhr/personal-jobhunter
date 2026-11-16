"""Stable, sanitized errors for Google Sheets operations.

Provider response bodies, authorization codes, spreadsheet identifiers, client
secrets, and tokens must never appear in these messages or in logs.
"""

from __future__ import annotations

import socket

from google.auth.exceptions import RefreshError
from google.auth.exceptions import TimeoutError as GoogleAuthTimeoutError
from httplib2 import HttpLib2Error


class GoogleSheetsError(Exception):
    """A user-actionable Google Sheets failure with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        reconnect_required: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reconnect_required = reconnect_required
        self.retry_after_seconds = retry_after_seconds


def translate_http_status(
    status: int,
    *,
    retry_after_seconds: int | None = None,
) -> GoogleSheetsError:
    """Map an HTTP status to a sanitized error without reading the body."""

    if status == 401:
        return GoogleSheetsError(
            "reconnect_required",
            "Google authorization is no longer valid. Reconnect the account.",
            reconnect_required=True,
        )
    if status == 403:
        return GoogleSheetsError(
            "permission_denied",
            "Google denied access to the spreadsheet. Check the account, scope, "
            "and sharing permissions, then reconnect if needed.",
            reconnect_required=True,
        )
    if status == 404:
        return GoogleSheetsError(
            "spreadsheet_not_found",
            "The configured spreadsheet could not be found.",
        )
    if status == 429:
        return GoogleSheetsError(
            "rate_limited",
            "Google Sheets rate-limited the request. Try again later.",
            retry_after_seconds=retry_after_seconds,
        )
    if status in {400, 409, 422}:
        return GoogleSheetsError(
            "invalid_request",
            "Google Sheets rejected the request. Check the spreadsheet schema "
            "and the configured tabs.",
        )
    if status >= 500:
        return GoogleSheetsError(
            "server_error",
            "Google Sheets is temporarily unavailable. Try again later.",
            retry_after_seconds=retry_after_seconds,
        )
    return GoogleSheetsError(
        "api_error",
        "The Google Sheets request failed.",
    )


def translate_transport_exception(exc: BaseException) -> GoogleSheetsError:
    """Translate transport failures without echoing their raw text."""

    if isinstance(exc, RefreshError):
        return GoogleSheetsError(
            "reconnect_required",
            "Google authorization is no longer valid. Reconnect the account.",
            reconnect_required=True,
        )
    if isinstance(exc, (TimeoutError, socket.timeout, GoogleAuthTimeoutError)):
        return GoogleSheetsError(
            "timeout",
            "The Google Sheets request timed out.",
        )
    if isinstance(exc, (ConnectionError, OSError, HttpLib2Error)):
        return GoogleSheetsError(
            "network_error",
            "The Google Sheets service could not be reached.",
        )
    return GoogleSheetsError(
        "api_error",
        "The Google Sheets request failed.",
    )
