"""Validation for untrusted external source URLs.

A source URL comes from a third party and must never be rendered as a clickable
link without verification. Only plain HTTPS URLs with an ASCII, well-formed
hostname are considered safe. The original value is always preserved so
provenance survives even when the URL cannot be rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_IPV4_PATTERN = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

ALLOWED_SCHEME = "https"


@dataclass(frozen=True, slots=True)
class SourceUrlValidation:
    """Result of validating one source URL.

    ``original_url`` is the verbatim source value and is kept for provenance.
    ``safe_url`` is populated only when the URL may be rendered as a link.
    """

    original_url: str | None
    safe_url: str | None
    is_safe: bool


def validate_source_url(value: str | None) -> SourceUrlValidation:
    """Return whether ``value`` may be rendered as an external link."""

    if not isinstance(value, str) or not value:
        return SourceUrlValidation(value, None, False)

    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return SourceUrlValidation(value, None, False)

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing ``port`` raises for malformed port numbers.
        _ = parsed.port
    except ValueError:
        return SourceUrlValidation(value, None, False)

    if parsed.scheme.casefold() != ALLOWED_SCHEME:
        return SourceUrlValidation(value, None, False)
    if parsed.username is not None or parsed.password is not None:
        return SourceUrlValidation(value, None, False)
    if hostname is None or not hostname.isascii():
        return SourceUrlValidation(value, None, False)
    folded_host = hostname.casefold()
    if _IPV4_PATTERN.fullmatch(folded_host) is not None:
        # IP literals would let a rendered link point at loopback or LAN hosts.
        return SourceUrlValidation(value, None, False)
    if _HOSTNAME_PATTERN.fullmatch(folded_host) is None:
        return SourceUrlValidation(value, None, False)

    return SourceUrlValidation(value, value, True)
