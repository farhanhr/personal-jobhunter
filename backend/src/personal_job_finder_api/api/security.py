"""Local browser request hardening for settings endpoints."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from personal_job_finder_api.api.errors import ApiError

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def validate_local_request(request: Request) -> None:
    """Reject DNS-rebinding hosts and untrusted browser origins.

    Every settings read validates ``Host``; state-changing requests also require
    an explicit allowed ``Origin``. Non-browser clients must send the same Origin
    header that the local frontend sends.
    """

    settings = request.app.state.settings
    _validate_host(request, settings.allowed_hosts)
    if request.method.upper() in STATE_CHANGING_METHODS:
        _validate_origin(request, settings.allowed_origins)


def _validate_host(request: Request, allowed_hosts: tuple[str, ...]) -> None:
    host_header = request.headers.get("host")
    hostname = _parse_host(host_header)
    allowed = {host.casefold().rstrip(".") for host in allowed_hosts}
    if hostname is None or hostname not in allowed:
        raise ApiError(
            403,
            "invalid_host",
            "Request Host is not allowed for this local application.",
        )


def _parse_host(host_header: str | None) -> str | None:
    if not host_header or "@" in host_header or "," in host_header or "://" in host_header:
        return None
    try:
        parsed = urlsplit(f"//{host_header}")
    except ValueError:
        return None
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return None
    if parsed.hostname is None:
        return None
    return parsed.hostname.casefold().rstrip(".")


def _validate_origin(request: Request, allowed_origins: tuple[str, ...]) -> None:
    origin = request.headers.get("origin")
    if not origin or not _origin_is_allowed(origin, allowed_origins):
        raise ApiError(
            403,
            "invalid_origin",
            "State-changing requests must include an allowed local Origin header.",
        )


def _origin_is_allowed(origin: str, allowed_origins: tuple[str, ...]) -> bool:
    parsed = _parse_origin(origin)
    if parsed is None:
        return False
    scheme, hostname, port = parsed
    for allowed in allowed_origins:
        allowed_parsed = _parse_origin(allowed)
        if allowed_parsed is not None and allowed_parsed == (scheme, hostname, port):
            return True
    return False


def _parse_origin(origin: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"}:
            return None
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.casefold().rstrip("."), port
