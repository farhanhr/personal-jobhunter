"""Bounded HTTP transport for external job sources.

Connectors never use this module directly for arbitrary URLs: each connector builds
its URL from a fixed origin and validated path segments. This layer only enforces
timeouts, a response-size ceiling, and stable failure codes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

READ_CHUNK_BYTES = 64 * 1024
USER_AGENT = "personal-job-finder/0.1 (local personal use)"

TIMEOUT_CODE = "timeout"
NETWORK_ERROR_CODE = "network_error"
RESPONSE_TOO_LARGE_CODE = "response_too_large"
BLOCKED_REDIRECT_CODE = "blocked_redirect"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    content_type: str | None
    body: bytes
    headers: Mapping[str, str]


class HttpTransportError(Exception):
    """A transport failure with a stable, persistable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse: ...


class SameHostRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only to the same HTTPS host as the original request.

    Connectors request a fixed API origin. Following a redirect to another host
    would let an upstream response steer the application toward an arbitrary
    address, so cross-host and scheme-downgrade redirects are refused.
    """

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        original = urlsplit(req.full_url)
        target = urlsplit(newurl)
        if (
            target.scheme != "https"
            or original.scheme != target.scheme
            or (target.hostname or "").casefold() != (original.hostname or "").casefold()
        ):
            raise HttpTransportError(
                BLOCKED_REDIRECT_CODE,
                "The source redirected to an unapproved host.",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    """Standard-library GET transport with an explicit timeout and size limit."""

    def __init__(self, opener: Callable[[Request, float], Any] | None = None) -> None:
        self._opener = opener or _default_open

    def get(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> HttpResponse:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with self._opener(request, timeout_seconds) as response:
                return _build_response(
                    status_code=int(response.status),
                    headers=response.headers,
                    read=response.read,
                    max_bytes=max_bytes,
                )
        except HTTPError as exc:
            try:
                return _build_response(
                    status_code=int(exc.code),
                    headers=exc.headers,
                    read=exc.read,
                    max_bytes=max_bytes,
                )
            finally:
                exc.close()
        except HttpTransportError:
            raise
        except TimeoutError as exc:
            raise HttpTransportError(TIMEOUT_CODE, "The source request timed out.") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise HttpTransportError(TIMEOUT_CODE, "The source request timed out.") from exc
            raise HttpTransportError(
                NETWORK_ERROR_CODE, "The source could not be reached."
            ) from exc
        except OSError as exc:
            raise HttpTransportError(
                NETWORK_ERROR_CODE, "The source could not be reached."
            ) from exc


_OPENER = build_opener(SameHostRedirectHandler())


def _default_open(request: Request, timeout: float) -> Any:
    # A dedicated opener keeps the same-host redirect policy in force; the
    # connector origin is fixed, so redirects must not escape that host.
    return _OPENER.open(request, timeout=timeout)  # noqa: S310


def _build_response(
    *,
    status_code: int,
    headers: Mapping[str, str] | Any,
    read: Callable[[int], bytes],
    max_bytes: int,
) -> HttpResponse:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HttpTransportError(
                RESPONSE_TOO_LARGE_CODE,
                "The source response exceeded the configured size limit.",
            )
        chunks.append(chunk)

    header_items = headers.items() if hasattr(headers, "items") else ()
    header_map = {str(name): str(value) for name, value in header_items}
    return HttpResponse(
        status_code=status_code,
        content_type=header_value(header_map, "Content-Type"),
        body=b"".join(chunks),
        headers=header_map,
    )


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    """Read a header case-insensitively."""

    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return None
