"""Persisted Google Sheets spreadsheet selection and the single effective resolver.

Precedence is deterministic: a saved Settings UI value wins over
``GOOGLE_SHEETS_SPREADSHEET_URL`` which wins over ``GOOGLE_SHEETS_SPREADSHEET_ID``.
Every consumer (setup, sync, refresh, search runs, API) must resolve through
:func:`effective_spreadsheet` so they always agree. ``.env`` is never rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.core.time import format_utc, parse_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.db.tables import google_sheets_settings
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.spreadsheet_url import (
    extract_spreadsheet_id,
)

SettingsSource = Literal["ui", "cli", "environment", "none"]


@dataclass(frozen=True, slots=True)
class EffectiveSpreadsheet:
    spreadsheet_id: str | None = None
    source: SettingsSource = "none"


@dataclass(frozen=True, slots=True)
class SavedSpreadsheetState:
    spreadsheet_id: str
    workspace_ready_at: datetime | None
    configured_via: SettingsSource | None


def resolve_effective_spreadsheet(
    *,
    saved_id: str | None,
    environment_id: str | None,
    saved_source: SettingsSource | None = None,
) -> EffectiveSpreadsheet:
    """Resolve the active spreadsheet, keeping ``.env`` authoritative for env setups.

    A row written by a ``.env`` setup carries ``configured_via="environment"``. That
    row is only a record of the validated sheet, not a user override, so a later
    ``.env`` edit must take effect; otherwise the file would silently stop mattering
    while the status still reported ``environment``. A Settings or CLI value is a
    deliberate override and keeps winning.
    """

    if saved_source == "environment" and environment_id:
        return EffectiveSpreadsheet(spreadsheet_id=environment_id, source="environment")
    if saved_id:
        source: SettingsSource = saved_source or "ui"
        return EffectiveSpreadsheet(spreadsheet_id=saved_id, source=source)
    if environment_id:
        return EffectiveSpreadsheet(spreadsheet_id=environment_id, source="environment")
    return EffectiveSpreadsheet()


def environment_spreadsheet_id(settings: Settings) -> str | None:
    """The ``.env`` spreadsheet identifier, preferring the URL form."""

    if settings.google_sheets_spreadsheet_url:
        return extract_spreadsheet_id(settings.google_sheets_spreadsheet_url)
    return settings.google_sheets_spreadsheet_id or None


def effective_spreadsheet(engine: Engine, settings: Settings) -> EffectiveSpreadsheet:
    """Single source of truth for which spreadsheet the application uses."""

    state = GoogleSheetsSettingsStore(engine).load_state()
    return resolve_effective_spreadsheet(
        saved_id=None if state is None else state.spreadsheet_id,
        environment_id=environment_spreadsheet_id(settings),
        saved_source=None if state is None else state.configured_via,
    )


def workspace_ready(engine: Engine) -> bool:
    """True only after a setup run persisted a readiness timestamp."""

    state = GoogleSheetsSettingsStore(engine).load_state()
    return state is not None and state.workspace_ready_at is not None


def readiness_for_state(
    state: SavedSpreadsheetState | None,
    *,
    environment_id: str | None,
) -> bool:
    """Readiness for a loaded row, also respecting a changed ``.env`` sheet.

    Readiness is validated against one specific sheet. When the row came from a
    ``.env`` setup and ``.env`` now names a different spreadsheet, that sheet was
    never initialized, so the workspace is not ready and setup must run again.
    Removing ``.env`` afterwards keeps the previously validated sheet usable.
    """

    if state is None or state.workspace_ready_at is None:
        return False
    if state.configured_via == "environment" and environment_id is not None:
        return environment_id == state.spreadsheet_id
    return True


def workspace_ready_for(engine: Engine, settings: Settings) -> bool:
    """``readiness_for_state`` for the persisted row and the current settings."""

    return readiness_for_state(
        GoogleSheetsSettingsStore(engine).load_state(),
        environment_id=environment_spreadsheet_id(settings),
    )


def effective_spreadsheet_id_or_none(engine: Engine, settings: Settings) -> str | None:
    """Fallback accessor used by API routes; never raises for absent configuration."""

    try:
        return effective_spreadsheet(engine, settings).spreadsheet_id
    except GoogleSheetsError:
        return None


class GoogleSheetsSettingsStore:
    def __init__(self, engine: Engine, *, clock=utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def load_saved_spreadsheet_id(self) -> str | None:
        state = self.load_state()
        return None if state is None else state.spreadsheet_id

    def load_state(self) -> SavedSpreadsheetState | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(
                    google_sheets_settings.c.spreadsheet_id,
                    google_sheets_settings.c.workspace_ready_at_utc,
                    google_sheets_settings.c.configured_via,
                ).where(google_sheets_settings.c.settings_id == 1)
            ).first()
        if row is None:
            return None
        ready_raw = row[1]
        return SavedSpreadsheetState(
            spreadsheet_id=str(row[0]),
            workspace_ready_at=None if ready_raw is None else parse_utc(str(ready_raw)),
            configured_via=None if row[2] is None else cast(SettingsSource, str(row[2])),
        )

    def save_spreadsheet_id(
        self,
        spreadsheet_id: str,
        *,
        workspace_ready_at: datetime | None = None,
        configured_via: SettingsSource | None = None,
    ) -> datetime:
        if configured_via not in (None, "ui", "cli", "environment"):
            raise ValueError("configured_via must be ui, cli, environment, or None")
        now = self._clock()
        with immediate_transaction(self._engine) as connection:
            exists = connection.execute(
                sa.select(google_sheets_settings.c.settings_id).where(
                    google_sheets_settings.c.settings_id == 1
                )
            ).first()
            values = {
                "spreadsheet_id": spreadsheet_id,
                "workspace_ready_at_utc": (
                    None if workspace_ready_at is None else format_utc(workspace_ready_at)
                ),
                "configured_via": configured_via,
                "updated_at_utc": format_utc(now),
            }
            if exists is None:
                connection.execute(google_sheets_settings.insert().values(settings_id=1, **values))
            else:
                connection.execute(
                    google_sheets_settings.update()
                    .where(google_sheets_settings.c.settings_id == 1)
                    .values(**values)
                )
        return now

    def clear(self) -> None:
        with immediate_transaction(self._engine) as connection:
            connection.execute(
                google_sheets_settings.delete().where(google_sheets_settings.c.settings_id == 1)
            )
