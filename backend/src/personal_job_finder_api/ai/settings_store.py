"""Persisted AI provider settings and API-key masking.

The plaintext key exists only inside SQLite and inside the request that set it.
Every read path returns :func:`mask_api_key`, and every write path resolves a
submitted value through :func:`resolve_api_key_update`, so a browser that sends
back the mask cannot overwrite the real key with ``sk-...1234``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.ai.providers import PROVIDER_PRESETS
from personal_job_finder_api.core.time import format_utc, parse_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import ai_settings

SETTINGS_ROW_ID = 1
MASK_SEPARATOR = "..."
_MASK_PREFIX_LENGTH = 3
_MASK_SUFFIX_LENGTH = 4
_MIN_MASKABLE_LENGTH = 8
_FULLY_HIDDEN = "***"


def mask_api_key(api_key: str | None) -> str | None:
    """Return a recognisable hint such as ``sk-...1234`` for display only.

    The mask is intentionally idempotent: masking an already masked value
    returns the same string, which is what lets the write path detect that the
    browser sent the mask back instead of a new key.
    """

    if not api_key:
        return None
    if len(api_key) < _MIN_MASKABLE_LENGTH:
        return _FULLY_HIDDEN
    return f"{api_key[:_MASK_PREFIX_LENGTH]}{MASK_SEPARATOR}{api_key[-_MASK_SUFFIX_LENGTH:]}"


def is_masked_api_key(value: str) -> bool:
    """True when ``value`` is a display mask rather than a usable key."""

    return bool(value) and value == mask_api_key(value)


def resolve_api_key_update(submitted: str | None, existing: str | None) -> str | None:
    """Decide which key to store after a settings update.

    An omitted, blank, or masked value means "leave the stored key alone". That
    covers the normal case where the user edits the model without retyping the
    key, and the case where the browser posts back ``sk-...1234``.
    """

    if submitted is None:
        return existing
    candidate = submitted.strip()
    if not candidate or is_masked_api_key(candidate):
        return existing
    return candidate


def compute_is_configured(
    *,
    provider: str,
    base_url: str,
    api_key: str | None,
    model: str,
) -> bool:
    """True when the stored settings are complete enough to attempt a call.

    This is a configuration flag, not a connectivity flag: a revoked key keeps
    ``is_configured`` true until the user changes the settings.
    """

    if not base_url.strip() or not model.strip():
        return False
    preset = PROVIDER_PRESETS.get(provider)
    requires_api_key = True if preset is None else preset.requires_api_key
    return bool(api_key) or not requires_api_key


@dataclass(frozen=True, slots=True)
class AISettings:
    provider: str
    base_url: str
    api_key: str | None
    model: str
    is_configured: bool
    updated_at: datetime | None


class AISettingsStore:
    """Single-row store for the active AI endpoint (``id = 1``)."""

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def load(self) -> AISettings | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(
                    ai_settings.c.provider,
                    ai_settings.c.base_url,
                    ai_settings.c.api_key,
                    ai_settings.c.model,
                    ai_settings.c.is_configured,
                    ai_settings.c.updated_at_utc,
                ).where(ai_settings.c.id == SETTINGS_ROW_ID)
            ).first()
        if row is None:
            return None
        updated_raw = row[5]
        return AISettings(
            provider=str(row[0]),
            base_url=str(row[1]),
            api_key=None if row[2] is None else str(row[2]),
            model=str(row[3]),
            is_configured=bool(row[4]),
            updated_at=None if updated_raw is None else parse_utc(str(updated_raw)),
        )

    def save(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        model: str,
        is_configured: bool,
    ) -> AISettings:
        now = self._clock()
        updated_at = format_utc(now)
        with immediate_transaction(self._engine) as connection:
            exists = connection.execute(
                sa.select(ai_settings.c.id).where(ai_settings.c.id == SETTINGS_ROW_ID)
            ).first()
            values = {
                "provider": provider,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "is_configured": is_configured,
                "updated_at_utc": updated_at,
            }
            if exists is None:
                connection.execute(ai_settings.insert().values(id=SETTINGS_ROW_ID, **values))
            else:
                connection.execute(
                    ai_settings.update().where(ai_settings.c.id == SETTINGS_ROW_ID).values(**values)
                )
        return AISettings(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            is_configured=is_configured,
            updated_at=now,
        )
