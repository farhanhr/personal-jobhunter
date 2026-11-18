"""Tracker date parsing tests.

The parser is strict and deterministic: it reads the user's own text without
rewriting it, and it never guesses an unrecognized value.
"""

from __future__ import annotations

from datetime import date

from personal_job_finder_api.domain.tracker_dates import (
    REASON_INVALID_CALENDAR,
    REASON_UNRECOGNIZED,
    parse_tracker_date,
)


def test_an_iso_date_is_accepted() -> None:
    parsed = parse_tracker_date("2026-11-01")

    assert parsed.value == date(2026, 11, 1)
    assert parsed.reason is None
    assert not parsed.is_invalid


def test_a_day_first_numeric_date_is_accepted() -> None:
    parsed = parse_tracker_date("01/11/2026")

    assert parsed.value == date(2026, 11, 1)
    assert parsed.reason is None


def test_a_single_digit_day_and_month_are_accepted() -> None:
    assert parse_tracker_date("1/11/2026").value == date(2026, 11, 1)


def test_a_trailing_annotation_is_ignored_for_the_date() -> None:
    parsed = parse_tracker_date("01/11/2026 (pagi)")

    assert parsed.value == date(2026, 11, 1)
    assert parsed.raw == "01/11/2026 (pagi)"


def test_a_blank_value_is_not_an_error() -> None:
    for raw in ("", "   ", None):
        parsed = parse_tracker_date(raw)
        assert parsed.value is None
        assert parsed.reason is None
        assert parsed.is_blank
        assert not parsed.is_invalid


def test_an_impossible_calendar_date_is_rejected() -> None:
    parsed = parse_tracker_date("31/02/2026")

    assert parsed.value is None
    assert parsed.reason == REASON_INVALID_CALENDAR
    assert parsed.is_invalid


def test_an_unrecognized_value_is_rejected() -> None:
    for raw in ("besok", "01-11-2026", "2026/11/01", "01/11/26", "1 Nov 2026"):
        parsed = parse_tracker_date(raw)
        assert parsed.value is None
        assert parsed.reason == REASON_UNRECOGNIZED
        assert parsed.is_invalid


def test_an_annotation_without_a_date_is_rejected() -> None:
    parsed = parse_tracker_date("(pagi)")

    assert parsed.value is None
    assert parsed.reason == REASON_UNRECOGNIZED


def test_the_raw_value_is_never_changed() -> None:
    raw = "  01/11/2026 (pagi)  "

    assert parse_tracker_date(raw).raw == raw
