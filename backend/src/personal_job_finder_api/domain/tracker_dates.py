"""Strict interpretation of the user-owned tracker date columns.

The canonical ``Opportunities`` tab stores ``date_applied`` and
``interview_date`` as free text (ADR-021, P0/12 §2). The dashboard has to read
those values, but the stored text is never rewritten: the canonical value stays
exactly as the user typed it, and only the interpretation lives here.

Accepted formats, applied in this order:

1. ``YYYY-MM-DD`` -- ISO-8601, for example ``2026-11-01``. The month and day
   may use one or two digits; the year must use four.
2. ``D/MM/YYYY`` or ``DD/MM/YYYY`` -- the Indonesian numeric convention, for
   example ``01/11/2026``. The **day comes first**. ``MM/DD/YYYY`` is not
   accepted and is never guessed: a value such as ``11/01/2026`` is read as
   11 January 2026.

One trailing parenthetical annotation is ignored while reading the date, so
``01/11/2026 (pagi)`` is the date 1 November 2026. The annotation may hold any
text without nested parentheses, for example ``(pagi)``, ``(10:00)``, or
``(reschedule)``. The raw text keeps the annotation.

Anything else is rejected deterministically:

- ``unrecognized_format`` -- the value is not one of the accepted formats.
- ``invalid_calendar_date`` -- the value has a recognized shape but does not
  exist, for example ``31/02/2026``.

A blank value is neither counted nor reported as invalid. Blank means "not
recorded", which is a valid state for a user-owned field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: The value does not match any accepted format.
REASON_UNRECOGNIZED = "unrecognized_format"

#: The value matches a format but is not a real calendar date.
REASON_INVALID_CALENDAR = "invalid_calendar_date"

_ISO_PATTERN = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DAY_FIRST_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ANNOTATION_PATTERN = re.compile(r"\s*\([^()]*\)\s*$")


@dataclass(frozen=True, slots=True)
class ParsedTrackerDate:
    """The interpretation of one raw tracker date value.

    ``value`` is the calendar date when the raw text could be read, and ``None``
    otherwise. ``reason`` explains a rejected value and stays ``None`` for a
    blank value, which is simply "not recorded".
    """

    raw: str
    value: date | None = None
    reason: str | None = None

    @property
    def is_blank(self) -> bool:
        """True when the user recorded nothing."""

        return self.value is None and self.reason is None

    @property
    def is_invalid(self) -> bool:
        """True when a non-empty value could not be read as a date."""

        return self.reason is not None


def parse_tracker_date(raw: str | None) -> ParsedTrackerDate:
    """Interpret one raw tracker date value without changing it."""

    text = (raw or "").strip()
    if not text:
        return ParsedTrackerDate(raw=raw or "")

    candidate = _ANNOTATION_PATTERN.sub("", text).strip()

    iso_match = _ISO_PATTERN.match(candidate)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
        return _build(raw, year, month, day)

    day_first_match = _DAY_FIRST_PATTERN.match(candidate)
    if day_first_match:
        day, month, year = (int(part) for part in day_first_match.groups())
        return _build(raw, year, month, day)

    return ParsedTrackerDate(raw=raw or "", reason=REASON_UNRECOGNIZED)


def _build(raw: str, year: int, month: int, day: int) -> ParsedTrackerDate:
    try:
        return ParsedTrackerDate(raw=raw, value=date(year, month, day))
    except ValueError:
        return ParsedTrackerDate(raw=raw, reason=REASON_INVALID_CALENDAR)
