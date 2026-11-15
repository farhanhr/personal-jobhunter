"""Small text helpers shared by deterministic matching rules."""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_words(value: str) -> str:
    """Casefold text and reduce it to space-separated alphanumeric words."""

    return " ".join(_NON_WORD.sub(" ", value.casefold()).split())


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """Return True when ``phrase`` appears as whole words in normalized text."""

    normalized_phrase = normalize_words(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_text} "
