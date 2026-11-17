"""Deterministic mapping from outbox payloads to accepted spreadsheet columns.

The mapping is explicit rather than reflective: every accepted header appears in
exactly one place below, so adding a column to the contract forces a deliberate
decision here. Values the source did not provide stay blank; the projection never
invents an employment type, experience level, skill list, deadline, author, or
source excerpt.

Column ownership is part of the contract. ``user_status`` belongs to the user, so
an update must carry the value already in the sheet instead of overwriting it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from personal_job_finder_api.domain.text import normalize_words
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    OPPORTUNITY_TRACKER_HEADERS,
    OPPORTUNITY_USER_OWNED_HEADERS,
    PENDING_USER_FIELDS_KEY,
)

OPPORTUNITY_TAB = "Opportunities"
JOB_SOURCE_TAB = "Job_Sources"

# Only these columns are user-managed. Everything else is application-owned and
# may be refreshed from the source of truth.
USER_OWNED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    OPPORTUNITY_TAB: OPPORTUNITY_USER_OWNED_HEADERS,
    JOB_SOURCE_TAB: (),
}

# Documented defaults used only when a row is created for the first time.
USER_OWNED_DEFAULTS: Mapping[tuple[str, str], str] = {
    (OPPORTUNITY_TAB, "user_status"): "new",
}

# Contract enum for ``provenance_method``. An unmapped source type stays blank
# rather than claiming a provenance the contract does not define.
PROVENANCE_METHODS: Mapping[str, str] = {
    "greenhouse": "ats_api",
    "lever": "ats_api",
    "ashby": "ats_api",
    "permitted_career_page": "permitted_page",
    "linkedin_post_manual": "manual_user_input",
}

OPPORTUNITY_OBSERVATION_STATUS = "seen"

EXCLUDED_LOCATION_STATUS = "excluded"

# Payload keys whose sheet header has a different name, or that feed more than one
# header. Without this map a pending tracker patch would treat these columns as
# "not supplied" and copy the previous sheet value, silently reverting a discovery
# update such as a renamed job title.
OPPORTUNITY_SUPPLIED_BY_PAYLOAD_KEY: Mapping[str, tuple[str, ...]] = {
    "job_title": ("job_title_original", "job_title_normalized"),
    "posted_at_utc": ("posted_at",),
}


class ProjectionError(Exception):
    """Raised when a payload cannot be projected safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProjectedRow:
    """One fully populated spreadsheet row, aligned with the tab headers."""

    tab: str
    identity_column: str
    identity: str
    headers: tuple[str, ...]
    values: tuple[str, ...]

    def index_of(self, header: str) -> int:
        return self.headers.index(header)

    @property
    def is_withdrawn(self) -> bool:
        """A withdrawn projection marks the record as excluded from the feed."""

        if self.tab != OPPORTUNITY_TAB:
            return False
        return self.values[self.index_of("location_status")] == EXCLUDED_LOCATION_STATUS


def row_values(values: Sequence[Any]) -> tuple[str, ...]:
    """Render cell values as text without changing their meaning."""

    rendered: list[str] = []
    for value in values:
        if value is None:
            rendered.append("")
        elif isinstance(value, str):
            rendered.append(value)
        else:
            rendered.append(str(value))
    return tuple(rendered)


def project_opportunity(
    payload: Mapping[str, Any],
    *,
    existing_row: Sequence[str] | None = None,
) -> ProjectedRow:
    """Map an opportunity payload onto ``OPPORTUNITY_HEADERS``.

    ``existing_row`` carries the values currently stored for this record. When
    present, user-owned cells are copied across so an update cannot erase them.
    """

    identity = _required_identity(payload, "opportunity_id")
    title = _text(payload.get("job_title"))
    pending_user_fields = _pending_user_fields(payload)

    cells: dict[str, Any] = {
        "opportunity_id": identity,
        "company_name": _text(payload.get("company_name")),
        "job_title_original": title,
        "job_title_normalized": normalize_words(title) if title else "",
        "target_role_id": _text(payload.get("target_role_id")),
        "role_match_type": _text(payload.get("role_match_type")),
        "role_match_reason": _text(payload.get("role_match_reason")),
        "location_raw": _text(payload.get("location_raw")),
        "country": _text(payload.get("country")),
        "province": _text(payload.get("province")),
        "city": _text(payload.get("city")),
        "location_status": _text(payload.get("location_status")),
        "work_mode": _text(payload.get("work_mode")),
        "remote_scope": _text(payload.get("remote_scope")),
        # Not captured by the current connectors. Blank is honest; a guess is not.
        "employment_type": "",
        "experience_level": "",
        "description": _text(payload.get("description")),
        "required_skills_json": "",
        "posted_at": _text(payload.get("posted_at_utc")),
        "deadline": "",
        "availability_status": _text(payload.get("availability_status")),
        # User-owned values are supplied only by a tracker patch. A normal
        # discovery payload leaves these blank and the merge below copies the
        # canonical sheet values.
        "user_status": _text(payload.get("user_status")),
        "first_seen_at_utc": _text(payload.get("first_seen_at_utc")),
        "last_seen_at_utc": _text(payload.get("last_seen_at_utc")),
        "created_at_utc": _text(payload.get("created_at_utc")),
        "updated_at_utc": _text(payload.get("updated_at_utc")),
        **{header: _text(payload.get(header)) for header in OPPORTUNITY_TRACKER_HEADERS},
    }

    return _build(
        tab=OPPORTUNITY_TAB,
        headers=OPPORTUNITY_HEADERS,
        identity_column="opportunity_id",
        identity=identity,
        cells=cells,
        existing_row=existing_row,
        supplied_fields=_supplied_opportunity_headers(payload),
        pending_user_fields=pending_user_fields,
    )


def _supplied_opportunity_headers(payload: Mapping[str, Any]) -> frozenset[str]:
    """Translate payload keys into the sheet headers they actually populate."""

    supplied = frozenset(payload) & set(OPPORTUNITY_HEADERS)
    for key, headers in OPPORTUNITY_SUPPLIED_BY_PAYLOAD_KEY.items():
        if key in payload:
            supplied = supplied | set(headers)
    return frozenset(supplied)


def project_job_source(
    payload: Mapping[str, Any],
    *,
    existing_row: Sequence[str] | None = None,
) -> ProjectedRow:
    """Map a source-provenance payload onto ``JOB_SOURCE_HEADERS``."""

    identity = _required_identity(payload, "source_id")
    parent_id = _text(payload.get("opportunity_id"))
    if not parent_id:
        raise ProjectionError(
            "missing_parent_opportunity",
            "A job source needs the stable identity of its parent opportunity.",
        )
    source_type = _text(payload.get("source_type"))
    # Only a validated URL may reach the sheet. A payload whose URL failed
    # validation stays blank rather than writing a clickable unsafe scheme.
    safe_url = _text(payload.get("safe_source_url"))

    cells: dict[str, Any] = {
        "source_id": identity,
        "opportunity_id": parent_id,
        "source_type": source_type,
        "source_url": safe_url,
        "external_source_id": _text(payload.get("external_source_id")),
        # Only supplied by a manual import; a connector cannot know these.
        "author_name": _text(payload.get("author_name")),
        "author_role": _text(payload.get("author_role")),
        "source_text_excerpt": _text(payload.get("source_text_excerpt")),
        "posted_at": _text(payload.get("posted_at")),
        "captured_at_utc": _text(payload.get("captured_at_utc")),
        "last_seen_at_utc": _text(payload.get("last_seen_at_utc")),
        "provenance_method": PROVENANCE_METHODS.get(source_type, ""),
        "observation_status": _text(payload.get("observation_status"))
        or OPPORTUNITY_OBSERVATION_STATUS,
    }

    return _build(
        tab=JOB_SOURCE_TAB,
        headers=JOB_SOURCE_HEADERS,
        identity_column="source_id",
        identity=identity,
        cells=cells,
        existing_row=existing_row,
    )


def _build(
    *,
    tab: str,
    headers: tuple[str, ...],
    identity_column: str,
    identity: str,
    cells: Mapping[str, Any],
    existing_row: Sequence[str] | None,
    supplied_fields: Collection[str] = (),
    pending_user_fields: Sequence[str] = (),
) -> ProjectedRow:
    missing = [header for header in headers if header not in cells]
    if missing:
        raise ProjectionError(
            "unmapped_header",
            f"The projection for {tab} has no mapping for: {', '.join(missing)}.",
        )

    if pending_user_fields:
        ordered = _values_with_pending_fields(
            tab=tab,
            headers=headers,
            cells=cells,
            existing_row=existing_row,
            supplied_fields=supplied_fields,
            pending_user_fields=pending_user_fields,
        )
    else:
        for column in USER_OWNED_COLUMNS[tab]:
            cells[column] = _user_owned_value(
                tab=tab,
                column=column,
                headers=headers,
                existing_row=existing_row,
            )
        ordered = row_values([cells[header] for header in headers])

    return ProjectedRow(
        tab=tab,
        identity_column=identity_column,
        identity=identity,
        headers=headers,
        values=ordered,
    )


def _values_with_pending_fields(
    *,
    tab: str,
    headers: tuple[str, ...],
    cells: Mapping[str, Any],
    existing_row: Sequence[str] | None,
    supplied_fields: Collection[str],
    pending_user_fields: Sequence[str],
) -> tuple[str, ...]:
    """Overlay pending user edits without blanking discovery fields.

    A tracker patch may contain only user-owned columns. When a row already
    exists, every column the payload did not supply is copied from that row.
    Pending user columns take the payload value, including an explicit blank.

    Columns the connectors never capture (``employment_type``,
    ``experience_level``, ``required_skills_json``, ``deadline``) are therefore
    preserved from the existing row here, whereas a full discovery projection
    blanks them. Preserving is the safer direction: a tracker edit must never
    erase a value the canonical sheet already holds.
    """

    base = dict(zip(headers, existing_row, strict=False)) if existing_row is not None else {}
    pending = set(pending_user_fields)
    values: dict[str, str] = {}
    for header in headers:
        if header in USER_OWNED_COLUMNS[tab]:
            if header in pending:
                values[header] = row_values([cells[header]])[0]
            elif existing_row is not None:
                values[header] = row_values([base.get(header, "")])[0]
            else:
                values[header] = USER_OWNED_DEFAULTS.get((tab, header), "")
        elif header in supplied_fields:
            values[header] = row_values([cells[header]])[0]
        else:
            values[header] = row_values([base.get(header, "")])[0]

    return row_values([values[header] for header in headers])


def _pending_user_fields(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the reserved pending-user-field list from an outbox payload."""

    raw = payload.get(PENDING_USER_FIELDS_KEY)
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ProjectionError(
            "invalid_pending_user_fields",
            "The pending user field list must be a sequence of header names.",
        )
    fields = tuple(raw)
    if not all(isinstance(field, str) for field in fields):
        raise ProjectionError(
            "invalid_pending_user_fields",
            "Every pending user field must be a header name.",
        )
    unknown = sorted(set(fields) - set(OPPORTUNITY_USER_OWNED_HEADERS))
    if unknown:
        raise ProjectionError(
            "invalid_pending_user_fields",
            f"Unknown pending user fields: {', '.join(unknown)}.",
        )
    # A declared pending field with no value is indistinguishable from an
    # explicit blank, so it would silently erase the canonical cell. Require the
    # value to be present, even when that value is an empty string.
    missing = sorted(field for field in fields if field not in payload)
    if missing:
        raise ProjectionError(
            "invalid_pending_user_fields",
            f"Pending user fields without a supplied value: {', '.join(missing)}.",
        )
    return fields


def _user_owned_value(
    *,
    tab: str,
    column: str,
    headers: tuple[str, ...],
    existing_row: Sequence[str] | None,
) -> str:
    """Return the user's value when a row exists, otherwise the documented default."""

    if existing_row is None:
        return USER_OWNED_DEFAULTS.get((tab, column), "")

    index = headers.index(column)
    if index >= len(existing_row):
        return ""
    return row_values([existing_row[index]])[0]


def _required_identity(payload: Mapping[str, Any], key: str) -> str:
    identity = _text(payload.get(key))
    if not identity:
        raise ProjectionError(
            "missing_identity",
            f"The payload has no {key}; a projection needs a stable identity.",
        )
    return identity


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
