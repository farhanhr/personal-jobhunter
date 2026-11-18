"""User-owned application tracker updates for one opportunity.

A tracker patch never talks to Google. It records the edit in the existing
per-opportunity outbox projection, which the manual sync dispatcher later writes
to the canonical ``Opportunities`` row. Ownership follows ADR-021: only the seven
tracker columns are user-editable, and the outbox payload records which of them
carry a pending local edit so a discovery re-ingestion cannot discard them.

Three local states have to be distinguished before work is queued:

- An unsynced outbox row already carries this record's discovery payload, so the
  patch is merged into it and nothing is lost.
- A ``synced`` outbox row means the canonical row exists; a tracker-only payload
  is safe because the dispatcher copies every column the payload does not supply
  from the existing sheet row.
- A record known only from the read model is canonical too, so the same
  tracker-only payload is safe.

A staged record with no outbox row and no cached canonical row has no known
canonical target. Enqueuing a tracker-only payload there could create a blank
discovery row, so it is reported instead of written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_TRACKER_HEADERS,
    PENDING_USER_FIELDS_KEY,
)
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_ENTITY_TYPE,
    OUTBOX_OPERATION,
    OpportunityStagingRepository,
)
from personal_job_finder_api.repositories.read_model import find_read_model_opportunity
from personal_job_finder_api.sync.projections import is_tracker_only_opportunity_payload

TRACKER_FIELDS = OPPORTUNITY_TRACKER_HEADERS

# Statuses that prove a canonical row may already exist. ``in_progress``,
# ``retry_wait`` and ``blocked`` can follow a write whose response was lost, so
# the row may be in the sheet even though local state does not say ``synced``.
_DELIVERED_STATUSES = frozenset({"synced", "in_progress", "retry_wait", "blocked"})

# Sync states reported to the caller. ``not_queued`` means nothing is pending and
# no canonical row is cached either, so the effective values are all blank.
SYNC_NOT_QUEUED = "not_queued"
SYNC_SYNCED = "synced"


class TrackerError(Exception):
    """A tracker update the caller must handle with a stable code."""

    code = "tracker_error"


class TrackerNotFound(TrackerError):
    """No local data source knows this opportunity."""

    code = "opportunity_not_found"


class TrackerStateInconsistent(TrackerError):
    """The record has no canonical target that a tracker patch could safely use."""

    code = "tracker_state_inconsistent"


@dataclass(frozen=True, slots=True)
class TrackerResult:
    """Effective tracker values after one patch."""

    opportunity_id: str
    values: Mapping[str, str | None]
    sync_status: str


class OpportunityTrackerService:
    """Read, validate, and queue one tracker patch in a single transaction."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Any = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = OpportunityStagingRepository()

    def update(
        self,
        opportunity_id: str,
        patch: Mapping[str, str | None],
    ) -> TrackerResult:
        """Apply a validated tracker patch without any Google request.

        Read, compare, and queue happen inside one short ``BEGIN IMMEDIATE``
        transaction, so a concurrent patch or dispatcher claim cannot interleave.
        The read model is deliberately not touched: the dispatcher and its fenced
        settlement remain the only path that publishes canonical values locally.
        """

        now = format_utc(self._clock())

        with immediate_transaction(self._engine) as connection:
            staged = self._repository.find_opportunity(connection, opportunity_id)
            outbox = self._repository.find_outbox(
                connection,
                entity_id=opportunity_id,
                entity_type=OUTBOX_ENTITY_TYPE,
            )
            read_model = find_read_model_opportunity(
                connection,
                opportunity_id=opportunity_id,
            )

            if staged is None and outbox is None and read_model is None:
                raise TrackerNotFound()

            _require_safe_target(outbox=outbox, read_model=read_model)

            known = _known_values(read_model=read_model, outbox=outbox)
            if not _changes_values(known, patch):
                # An identical patch must not create duplicate work or reset an
                # existing retry schedule, so the outbox row is left untouched.
                return _result(opportunity_id, known, outbox=outbox, read_model=read_model)

            payload_json = _patch_payload(opportunity_id, patch)
            self._repository.upsert_outbox(
                connection,
                outbox_id=_outbox_id(outbox, opportunity_id),
                run_id=None,
                entity_id=opportunity_id,
                payload_json=payload_json,
                payload_hash=_payload_hash(payload_json),
                now_utc=now,
                entity_type=OUTBOX_ENTITY_TYPE,
                operation=OUTBOX_OPERATION,
            )

            stored = self._repository.find_outbox(
                connection,
                entity_id=opportunity_id,
                entity_type=OUTBOX_ENTITY_TYPE,
            )
            effective = _known_values(read_model=read_model, outbox=stored)
            return _result(opportunity_id, effective, outbox=stored, read_model=read_model)


def _require_safe_target(
    *,
    outbox: Mapping[str, Any] | None,
    read_model: Mapping[str, Any] | None,
) -> None:
    """Refuse a patch that could create a blank canonical row."""

    if read_model is not None:
        return
    if outbox is None:
        raise TrackerStateInconsistent()
    if str(outbox["status"]) in _DELIVERED_STATUSES:
        return
    if not is_tracker_only_opportunity_payload(_payload(outbox)):
        # An unsent full discovery payload will create the row completely, and
        # the merge keeps those columns.
        return
    raise TrackerStateInconsistent()


def _known_values(
    *,
    read_model: Mapping[str, Any] | None,
    outbox: Mapping[str, Any] | None,
) -> dict[str, str | None]:
    """Return only the tracker values whose canonical state is known locally.

    The read model mirrors the sheet, so it makes every tracker field known. An
    unsynced outbox row makes only the fields it declares as pending known: those
    are local edits that have not been published yet. Any field missing from the
    result cannot be compared safely, because the sheet may already hold a value
    this machine has not read.
    """

    known: dict[str, str | None] = {}
    if read_model is not None:
        for field in TRACKER_FIELDS:
            known[field] = _as_text(read_model.get(field))

    if outbox is not None and str(outbox["status"]) != SYNC_SYNCED:
        payload = _payload(outbox)
        pending = payload.get(PENDING_USER_FIELDS_KEY)
        if isinstance(pending, list):
            for field in pending:
                if field in TRACKER_FIELDS:
                    known[field] = _as_text(payload.get(field))

    return known


def _changes_values(
    known: Mapping[str, str | None],
    patch: Mapping[str, str | None],
) -> bool:
    """Report whether a patch changes anything, treating unknown state as changed.

    An unknown field must be queued rather than skipped: skipping would silently
    drop the user's edit when the sheet already holds a different value.
    """

    for field, value in patch.items():
        if field not in known or known[field] != _as_text(value):
            return True
    return False


def _patch_payload(opportunity_id: str, patch: Mapping[str, str | None]) -> str:
    """Build the minimal ownership-aware payload for one patch.

    Only the supplied tracker fields are included, plus their ownership list. The
    dispatcher fills every other column from the canonical row, so a patch cannot
    carry stale tracker values forward or blank discovery columns.
    """

    payload: dict[str, Any] = {"opportunity_id": opportunity_id}
    pending: list[str] = []
    for field, value in patch.items():
        payload[field] = "" if value is None else value
        pending.append(field)
    payload[PENDING_USER_FIELDS_KEY] = sorted(pending)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _outbox_id(outbox: Mapping[str, Any] | None, opportunity_id: str) -> str:
    if outbox is not None:
        return str(outbox["outbox_id"])
    # Mirrors the staging convention (``outbox_opp_<identity digest>``) so one
    # opportunity keeps a single projection row for its whole lifetime.
    return f"outbox_opp_{opportunity_id.removeprefix('opp_')}"


def _payload(outbox: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(outbox["payload_json"]))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_text(value: Any) -> str | None:
    """Treat a blank cell and an explicit clear as the same absent value."""

    if isinstance(value, str):
        return value if value != "" else None
    return None


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _result(
    opportunity_id: str,
    values: Mapping[str, str | None],
    *,
    outbox: Mapping[str, Any] | None,
    read_model: Mapping[str, Any] | None,
) -> TrackerResult:
    """Build the response, reporting unknown fields as absent rather than guessed."""

    effective: dict[str, str | None] = {field: values.get(field) for field in TRACKER_FIELDS}
    return TrackerResult(
        opportunity_id=opportunity_id,
        values=effective,
        sync_status=_sync_status(outbox=outbox, read_model=read_model),
    )


def _sync_status(
    *,
    outbox: Mapping[str, Any] | None,
    read_model: Mapping[str, Any] | None,
) -> str:
    if outbox is not None:
        return str(outbox["status"])
    if read_model is not None:
        # The canonical mirror is the only source, so nothing is pending.
        return SYNC_SYNCED
    return SYNC_NOT_QUEUED


__all__ = [
    "SYNC_NOT_QUEUED",
    "SYNC_SYNCED",
    "TRACKER_FIELDS",
    "OpportunityTrackerService",
    "TrackerError",
    "TrackerNotFound",
    "TrackerResult",
    "TrackerStateInconsistent",
]
