"""Deterministic identity for staged source records.

A job is identified by the source that published it, never by its title: two
companies can publish the same title, and a company can rename an existing post.
The identity therefore hashes the source triple
``(source_type, company_id, external_source_id)``.

Parts are joined with an explicit length prefix so a value containing the
separator cannot collide with a different split of the same string.
"""

from __future__ import annotations

import hashlib

OPPORTUNITY_ID_PREFIX = "opp_"
SOURCE_ID_PREFIX = "src_"
_ID_DIGEST_LENGTH = 32


def opportunity_id_for(
    *,
    source_type: str,
    company_id: str,
    external_source_id: str,
) -> str:
    """Return the stable opportunity id for one source record."""

    return OPPORTUNITY_ID_PREFIX + _identity_digest(
        source_type=source_type,
        company_id=company_id,
        external_source_id=external_source_id,
    )


def source_id_for(
    *,
    source_type: str,
    company_id: str,
    external_source_id: str,
) -> str:
    """Return the stable provenance row id for one source record."""

    return SOURCE_ID_PREFIX + _identity_digest(
        source_type=source_type,
        company_id=company_id,
        external_source_id=external_source_id,
    )


def _identity_digest(
    *,
    source_type: str,
    company_id: str,
    external_source_id: str,
) -> str:
    parts = tuple(
        _normalize_part(value, name)
        for value, name in (
            (source_type, "source_type"),
            (company_id, "company_id"),
            (external_source_id, "external_source_id"),
        )
    )
    payload = "\x1f".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_ID_DIGEST_LENGTH]


def _normalize_part(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized
