"""Expected AI-subsystem failures with stable, user-facing codes."""

from __future__ import annotations


class AIServiceError(Exception):
    """A configuration or provider problem the user can act on.

    The message is shown in the Settings UI, so it must never contain the API
    key, the raw provider body, or another secret.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
