"""Independent reference calculation for every optional Dashboard metric.

This module exists to disagree with the generated formulas. It reads canonical
row data (header-keyed dictionaries, exactly as a spreadsheet row would be read
back) and computes each metric from the *rules* in P0/12 and ADR-021. It must
never import :mod:`personal_job_finder_api.integrations.google_sheets.dashboard_layout`,
because sharing the formula builder would make the comparison circular: a bug in
the builder would be reproduced in the oracle and silently "pass".

The oracle is deliberately strict about what it cannot know:

- a value the canonical row does not state is ``None``/blank, never a guess;
- a date the tracker parser rejects is counted as unreadable, not coerced;
- an opportunity whose source row is missing is *not* invented.

Known, documented divergences from the spreadsheet formulas are listed in
:data:`KNOWN_DIVERGENCES` so a live mismatch can be classified instead of
silently "fixed" by changing a metric definition.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from personal_job_finder_api.domain.tracker_dates import parse_tracker_date

JAKARTA = ZoneInfo("Asia/Jakarta")

#: Mirrors the canonical source-type vocabulary. Kept local so the oracle does
#: not depend on the formula module; a test pins parity with both.
SOURCE_TYPES: tuple[str, ...] = (
    "greenhouse",
    "lever",
    "ashby",
    "permitted_career_page",
    "linkedin_post_manual",
)

APPLICATION_STATUS_VALUES: tuple[str, ...] = (
    "Need to Apply",
    "Applied",
    "Assessment",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "No Feedback",
)

#: Canonical machine timestamp written by the app for ``first_seen_at_utc``.
CANONICAL_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

APPLICATION_WINDOW_DAYS = 7
INTERVIEW_WINDOW_DAYS = 7
NEW_OPPORTUNITY_WINDOW_DAYS = 7

KNOWN_DIVERGENCES: tuple[str, ...] = (
    "Spreadsheet text comparison is case-insensitive; the backend enums are "
    "exact-case. Canonical refresh keeps stored values lowercase, so a "
    "mixed-case location_status or source_type is not reachable in normal use.",
    "Python's regex engine accepts some Unicode digits that the spreadsheet RE2 "
    "engine does not, so a non-ASCII digit in a date is read by the oracle and "
    "rejected by the formula.",
    "The spreadsheet cannot represent years before 1900 reliably; the oracle accepts them.",
    "The new-opportunity formula checks the timestamp *shape* and the window, "
    "but does not re-validate that the timestamp is a possible instant.",
)


@dataclass(frozen=True, slots=True)
class ExpectedMetrics:
    """Every Dashboard value, keyed by the spreadsheet cell that shows it."""

    values: Mapping[str, int | str]
    unreadable_date_applied: int = 0
    unreadable_interview_date: int = 0
    notes: tuple[str, ...] = field(default=())

    def __getitem__(self, address: str) -> int | str:
        return self.values[address]


@dataclass(frozen=True, slots=True)
class OpportunityRow:
    """The canonical opportunity columns the Dashboard reads."""

    opportunity_id: str
    location_status: str | None = None
    first_seen_at_utc: str | None = None
    date_applied: str | None = None
    application_status: str | None = None
    interview_date: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRow:
    """The canonical source columns the Dashboard reads."""

    opportunity_id: str | None
    source_type: str | None


def _text(value: Any) -> str:
    """Render a cell value the way ``TO_TEXT`` would for these columns."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def sheets_trim(value: str) -> str:
    """Mirror Google Sheets ``TRIM``: strip ends and collapse inner spaces.

    Sheets ``TRIM`` removes leading and trailing spaces and collapses runs of
    regular spaces between words. It does **not** remove non-breaking spaces, so
    ``"\\u00a0"`` stays non-blank. Python's ``str.strip`` does not collapse, so
    this helper is intentionally explicit.
    """

    return " ".join(part for part in value.split(" ") if part)


def normalize_status(value: Any) -> str:
    """The formula normalization: ``LOWER(TRIM(TO_TEXT(value)))``."""

    return sheets_trim(_text(value)).lower()


def _is_blank(value: Any) -> bool:
    return sheets_trim(_text(value)) == ""


def _utc_text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _jakarta_midnight(as_of: date, day_offset: int) -> datetime:
    """Jakarta midnight for ``as_of + day_offset``, expressed in UTC."""

    shifted = as_of + timedelta(days=day_offset)
    return datetime.combine(shifted, time(0, 0), tzinfo=JAKARTA).astimezone(ZoneInfo("UTC"))


def _canonical_timestamp(value: Any) -> datetime | None:
    """Parse a canonical ``first_seen_at_utc`` value, or return ``None``."""

    text = _text(value)
    if not CANONICAL_UTC.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        return None


def _tracker_date(value: Any) -> tuple[date | None, bool]:
    """Return ``(parsed_date, is_unreadable)`` for one tracker value.

    A blank value is "not recorded", not invalid, matching the backend's
    ``is_blank`` handling.
    """

    text = _text(value)
    if sheets_trim(text) == "":
        return None, False
    try:
        parsed = parse_tracker_date(text)
    except Exception:
        return None, True
    if parsed.value is None:
        return None, parsed.reason is not None
    return parsed.value, False


def opportunity_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[OpportunityRow, ...]:
    """Project header-keyed canonical rows onto the Dashboard's columns."""

    projected: list[OpportunityRow] = []
    for row in rows:
        projected.append(
            OpportunityRow(
                opportunity_id=_text(row.get("opportunity_id")),
                location_status=(
                    None
                    if row.get("location_status") is None
                    else _text(row.get("location_status"))
                ),
                first_seen_at_utc=(
                    None
                    if row.get("first_seen_at_utc") is None
                    else _text(row.get("first_seen_at_utc"))
                ),
                date_applied=(
                    None if row.get("date_applied") is None else _text(row.get("date_applied"))
                ),
                application_status=(
                    None
                    if row.get("application_status") is None
                    else _text(row.get("application_status"))
                ),
                interview_date=(
                    None if row.get("interview_date") is None else _text(row.get("interview_date"))
                ),
            )
        )
    return tuple(projected)


def source_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[SourceRow, ...]:
    """Project header-keyed canonical source rows onto the Dashboard's columns."""

    projected: list[SourceRow] = []
    for row in rows:
        opportunity = row.get("opportunity_id")
        source_type = row.get("source_type")
        projected.append(
            SourceRow(
                opportunity_id=None if opportunity is None else _text(opportunity),
                source_type=None if source_type is None else _text(source_type),
            )
        )
    return tuple(projected)


def _distinct_ids(pairs: Iterable[tuple[str, bool]]) -> int:
    """Count distinct non-blank IDs whose flag is true."""

    return len({identifier for identifier, keep in pairs if keep and identifier != ""})


def compute(
    opportunities: Sequence[OpportunityRow],
    sources: Sequence[SourceRow],
    *,
    as_of: date,
) -> ExpectedMetrics:
    """Compute every Dashboard metric for one snapshot of canonical rows."""

    def not_excluded(row: OpportunityRow) -> bool:
        return row.location_status != "excluded"

    total = _distinct_ids((row.opportunity_id, not_excluded(row)) for row in opportunities)
    excluded_count = _distinct_ids(
        (row.opportunity_id, row.location_status == "excluded") for row in opportunities
    )

    window_start = _jakarta_midnight(as_of, -(NEW_OPPORTUNITY_WINDOW_DAYS - 1))
    window_end = _jakarta_midnight(as_of, 1)

    def is_new(row: OpportunityRow) -> bool:
        moment = _canonical_timestamp(row.first_seen_at_utc)
        if moment is None:
            return False
        return window_start <= moment < window_end

    new_count = _distinct_ids(
        (row.opportunity_id, not_excluded(row) and is_new(row)) for row in opportunities
    )

    application_start = as_of - timedelta(days=APPLICATION_WINDOW_DAYS - 1)
    application_end = as_of
    interview_start = as_of
    interview_end = as_of + timedelta(days=INTERVIEW_WINDOW_DAYS)

    unreadable_applied = 0
    unreadable_interview = 0

    def applied_in_window(row: OpportunityRow) -> bool:
        nonlocal unreadable_applied
        parsed, unreadable = _tracker_date(row.date_applied)
        if unreadable:
            unreadable_applied += 1
            return False
        return parsed is not None and application_start <= parsed <= application_end

    def interview_in_window(row: OpportunityRow) -> bool:
        nonlocal unreadable_interview
        parsed, unreadable = _tracker_date(row.interview_date)
        if unreadable:
            unreadable_interview += 1
            return False
        return parsed is not None and interview_start <= parsed <= interview_end

    applications = _distinct_ids(
        (row.opportunity_id, applied_in_window(row)) for row in opportunities
    )
    interviews = _distinct_ids(
        (row.opportunity_id, interview_in_window(row)) for row in opportunities
    )

    values: dict[str, int | str] = {
        "B2": as_of.isoformat(),
        "B6": total,
        "B7": excluded_count,
        "B8": new_count,
        "B9": applications,
        "B10": interviews,
    }

    recorded = 0
    blank_status = 0
    status_counts: dict[str, int] = {}
    for index, label in enumerate(APPLICATION_STATUS_VALUES):
        wanted = label.lower()
        count = _distinct_ids(
            (
                row.opportunity_id,
                normalize_status(row.application_status) == wanted,
            )
            for row in opportunities
        )
        status_counts[label] = count
        values[f"B{13 + index}"] = count

    recorded = _distinct_ids(
        (row.opportunity_id, normalize_status(row.application_status) != "")
        for row in opportunities
    )
    blank_status = _distinct_ids(
        (row.opportunity_id, normalize_status(row.application_status) == "")
        for row in opportunities
    )
    values["J1"] = recorded
    values["B21"] = blank_status
    values["B22"] = max(0, recorded - sum(status_counts.values()))

    # Source groups join each source row to its opportunity. A source row whose
    # opportunity is missing or excluded is not countable; a source row whose
    # opportunity exists but is excluded is likewise not countable.
    opportunity_by_id = {row.opportunity_id: row for row in opportunities if row.opportunity_id}

    def source_is_countable(source: SourceRow) -> bool:
        if not source.opportunity_id:
            return False
        parent = opportunity_by_id.get(source.opportunity_id)
        if parent is None:
            return False
        return parent.location_status != "excluded"

    for index, source_type in enumerate(SOURCE_TYPES):
        values[f"B{25 + index}"] = _distinct_ids(
            (
                source.opportunity_id or "",
                source_is_countable(source) and source.source_type == source_type,
            )
            for source in sources
        )

    orphan_ids = {
        row.opportunity_id
        for row in opportunities
        if row.opportunity_id and row.location_status != "excluded"
    }
    linked_ids = {source.opportunity_id for source in sources if source.opportunity_id}
    orphan_count = len(orphan_ids - linked_ids)
    blank_type_count = _distinct_ids(
        (
            source.opportunity_id or "",
            source_is_countable(source) and _text(source.source_type) == "",
        )
        for source in sources
    )
    values["B30"] = orphan_count + blank_type_count
    values["B33"] = unreadable_applied
    values["B34"] = unreadable_interview

    return ExpectedMetrics(
        values=values,
        unreadable_date_applied=unreadable_applied,
        unreadable_interview_date=unreadable_interview,
        notes=(),
    )


def header_only(*, as_of: date) -> ExpectedMetrics:
    """Expected values when both canonical tabs hold only their header row."""

    return compute((), (), as_of=as_of)
