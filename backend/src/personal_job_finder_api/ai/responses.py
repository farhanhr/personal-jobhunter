"""Helpers for reading provider completions without trusting the SDK shape."""

from __future__ import annotations

from typing import Any


def completion_content(completion: Any) -> str:
    """Return ``choices[0].message.content``, or an empty string when absent.

    Callers treat an empty result as an invalid response, so a provider that
    answers with no choices or no text produces one clear domain error rather
    than an ``AttributeError`` or ``IndexError``.
    """

    choices = getattr(completion, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""
