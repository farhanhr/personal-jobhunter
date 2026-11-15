"""Validation for user-registered company sources.

Board tokens are path identifiers, never free-form URLs. Anything that could
change the request path, host, or scheme is rejected before it reaches a connector.
"""

from __future__ import annotations

import re
from typing import Any

MAX_COMPANY_NAME_LENGTH = 200
MAX_BOARD_TOKEN_LENGTH = 64

_BOARD_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")


class CompanyValidationError(ValueError):
    """A stable, machine-readable company-source validation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def is_valid_board_token(value: object) -> bool:
    """True only for a single, safe path segment."""

    if not isinstance(value, str):
        return False
    if value != value.strip():
        return False
    if not value or len(value) > MAX_BOARD_TOKEN_LENGTH:
        return False
    return _BOARD_TOKEN_PATTERN.fullmatch(value) is not None


def validate_board_token(raw_value: str) -> str:
    """Return the token unchanged or raise ``invalid_board_token``."""

    if not is_valid_board_token(raw_value):
        raise CompanyValidationError(
            "invalid_board_token",
            (
                "The Greenhouse board token must be a single path identifier using "
                "letters, digits, underscores, or hyphens."
            ),
            {"board_token": raw_value if isinstance(raw_value, str) else None},
        )
    return raw_value


def validate_company_name(raw_value: str) -> str:
    """Normalize a display name and reject empty or control-character input."""

    if not isinstance(raw_value, str):
        raise CompanyValidationError(
            "invalid_company_name",
            "Company name must be a string.",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
        raise CompanyValidationError(
            "invalid_company_name",
            "Company name must not contain control characters.",
        )

    value = " ".join(raw_value.split())
    if not value or len(value) > MAX_COMPANY_NAME_LENGTH:
        raise CompanyValidationError(
            "invalid_company_name",
            "Company name must be 1-200 characters.",
        )
    return value
