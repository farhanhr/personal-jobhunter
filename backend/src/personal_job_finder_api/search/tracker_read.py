"""Effective tracker values for reading, without writing anything.

ADR-021 makes the seven application-tracking columns user-owned and makes the
Google Sheets ``Opportunities`` tab canonical. Locally there are two sources:

- the Read Model, which mirrors the canonical row as of the last refresh;
- the outbox, which may hold user edits that have not been written yet.

A pending edit is the user's latest intent, so it wins for exactly the fields
the payload declares as pending. Every other field falls back to the Read
Model. Nothing here writes, and nothing here talks to Google.

Two distinctions matter to the caller and are preserved:

- **known blank** versus **unknown**. A blank canonical cell is a known value of
  ``None``. A field that no local source knows is absent from ``known_fields``
  and must not be rendered as "blank".
- **tracker values** versus discovery payload. Only fields listed in the
  payload's ownership metadata (``_pending_user_fields``) are tracker edits. A
  discovery payload also carries tracker columns, but those are a copy made at
  ingestion time, not a user edit, and are never overlaid.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from personal_job_finder_api.db.tables import opportunities_read_model, sync_outbox
from personal_job_finder_api.integrations.google_sheets.config import (
    OPPORTUNITY_TRACKER_HEADERS,
    PENDING_USER_FIELDS_KEY,
)
from personal_job_finder_api.repositories.opportunities import (
    OUTBOX_DESTINATION,
    OUTBOX_ENTITY_TYPE,
)

TRACKER_FIELDS = OPPORTUNITY_TRACKER_HEADERS

# The subset shown on a feed card. ``notes`` is deliberately excluded from list
# responses: it is long free text and belongs on the detail view only.
TRACKER_SUMMARY_FIELDS = (
    "application_status",
    "date_applied",
    "follow_up_date",
    "interview_date",
)

SYNC_NOT_QUEUED = "not_queued"
SYNC_SYNCED = "synced"


@dataclass(frozen=True, slots=True)
class TrackerView:
    """Effective tracker state of one opportunity.

    ``values`` always contains every tracker field, using ``None`` for a value
    that is either known-blank or unknown. ``known_fields`` is what separates
    those two cases, so an empty tracker must never be read as "all blank".
    """

    values: Mapping[str, str | None]
    known_fields: tuple[str, ...]
    sync_status: str

    def summary_values(self) -> dict[str, str | None]:
        return {field: self.values.get(field) for field in TRACKER_SUMMARY_FIELDS}


def resolve_tracker_view(
    *,
    read_model: Mapping[str, Any] | None,
    outbox: Mapping[str, Any] | None,
) -> TrackerView:
    """Resolve effective tracker values from a Read Model row and an outbox row."""

    known = known_tracker_values(read_model=read_model, outbox=outbox)
    values = {field: known.get(field) for field in TRACKER_FIELDS}
    return TrackerView(
        values=values,
        known_fields=tuple(sorted(known)),
        sync_status=sync_status_for(outbox=outbox, read_model=read_model),
    )


def known_tracker_values(
    *,
    read_model: Mapping[str, Any] | None,
    outbox: Mapping[str, Any] | None,
) -> dict[str, str | None]:
    """Return only the tracker values whose canonical state is known locally.

    The Read Model mirrors the sheet, so it makes every tracker field known. An
    unsynced outbox row makes only the fields it declares as pending known:
    those are local edits that have not been published. A field missing from the
    result cannot be compared safely, because the sheet may already hold a value
    this machine has not read.
    """

    known: dict[str, str | None] = {}

    if read_model is not None:
        for field in TRACKER_FIELDS:
            known[field] = _as_text(read_model.get(field))

    if outbox is not None and str(outbox.get("status")) != SYNC_SYNCED:
        payload = outbox_payload(outbox)
        pending = payload.get(PENDING_USER_FIELDS_KEY)
        if isinstance(pending, list):
            for field in pending:
                # Only a declared, recognised tracker field may be overlaid. A
                # malformed ownership list cannot smuggle another column in.
                if isinstance(field, str) and field in TRACKER_FIELDS:
                    known[field] = _as_text(payload.get(field))

    return known


def sync_status_for(
    *,
    outbox: Mapping[str, Any] | None,
    read_model: Mapping[str, Any] | None,
) -> str:
    """Report the sync state to show next to tracker values.

    ``not_queued`` means nothing is pending and no canonical row is cached, so
    every value is unknown rather than blank.
    """

    if outbox is not None:
        return str(outbox.get("status"))
    if read_model is not None:
        # The canonical mirror is the only source, so nothing is pending.
        return SYNC_SYNCED
    return SYNC_NOT_QUEUED


def outbox_payload(outbox: Mapping[str, Any]) -> dict[str, Any]:
    """Decode an outbox payload, treating anything malformed as empty.

    A corrupt or non-object payload yields ``{}``: no field is then treated as
    pending, so a damaged row can never blank or overwrite a canonical value.
    """

    try:
        payload = json.loads(str(outbox.get("payload_json")))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_tracker_views(
    connection: Any,
    opportunity_ids: Iterable[str],
) -> dict[str, TrackerView]:
    """Resolve tracker views for a bounded set of opportunities in two queries.

    Both lookups are ``IN`` queries over the same id list, so the cost is fixed
    per page regardless of how many opportunities it contains. Opportunities
    with no row in either source get an all-unknown view.
    """

    ids = list(dict.fromkeys(opportunity_ids))
    if not ids:
        return {}

    read_rows = (
        connection.execute(
            select(opportunities_read_model).where(
                opportunities_read_model.c.opportunity_id.in_(ids)
            )
        )
        .mappings()
        .all()
    )
    outbox_rows = (
        connection.execute(
            select(sync_outbox).where(
                sync_outbox.c.destination == OUTBOX_DESTINATION,
                sync_outbox.c.entity_type == OUTBOX_ENTITY_TYPE,
                sync_outbox.c.entity_id.in_(ids),
            )
        )
        .mappings()
        .all()
    )

    read_by_id = {str(row["opportunity_id"]): row for row in read_rows}
    outbox_by_id = {str(row["entity_id"]): row for row in outbox_rows}

    return {
        opportunity_id: resolve_tracker_view(
            read_model=read_by_id.get(opportunity_id),
            outbox=outbox_by_id.get(opportunity_id),
        )
        for opportunity_id in ids
    }


def _as_text(value: Any) -> str | None:
    """Treat a blank cell and an explicit clear as the same absent value."""

    if isinstance(value, str):
        return value if value != "" else None
    return None


__all__ = [
    "SYNC_NOT_QUEUED",
    "SYNC_SYNCED",
    "TRACKER_FIELDS",
    "TRACKER_SUMMARY_FIELDS",
    "TrackerView",
    "known_tracker_values",
    "load_tracker_views",
    "outbox_payload",
    "resolve_tracker_view",
    "sync_status_for",
]
