"""Tests for Indonesia location and remote-scope classification."""

from __future__ import annotations

import pytest

from personal_job_finder_api.domain.location import (
    LocationStatus,
    RemoteScope,
    WorkMode,
    classify_location,
)


@pytest.mark.parametrize(
    ("raw_location", "expected_city", "expected_province"),
    [
        ("Jakarta, Indonesia", "Jakarta", "DKI Jakarta"),
        ("Bandung", "Bandung", "Jawa Barat"),
        ("Surabaya, East Java", "Surabaya", "Jawa Timur"),
        ("Denpasar, Bali", "Denpasar", "Bali"),
        ("Yogyakarta", "Yogyakarta", "DI Yogyakarta"),
        ("Tangerang Selatan, Banten", "Tangerang Selatan", "Banten"),
        ("Medan, Sumatera Utara", "Medan", "Sumatera Utara"),
    ],
)
def test_indonesian_locations_are_eligible_with_normalized_city(
    raw_location: str,
    expected_city: str,
    expected_province: str,
) -> None:
    result = classify_location(raw_location)

    assert result.status is LocationStatus.ELIGIBLE
    assert result.country == "Indonesia"
    assert result.city == expected_city
    assert result.province == expected_province
    assert result.work_mode is WorkMode.ONSITE
    assert result.remote_scope is RemoteScope.NOT_APPLICABLE
    assert result.location_raw == raw_location


def test_indonesia_country_mention_alone_is_eligible_without_city_guess() -> None:
    result = classify_location("Indonesia")

    assert result.status is LocationStatus.ELIGIBLE
    assert result.country == "Indonesia"
    assert result.city is None
    assert result.province is None


def test_hybrid_indonesian_role_is_eligible_with_hybrid_work_mode() -> None:
    result = classify_location("Hybrid - Jakarta")

    assert result.status is LocationStatus.ELIGIBLE
    assert result.work_mode is WorkMode.HYBRID
    assert result.city == "Jakarta"


def test_remote_role_open_to_indonesia_is_eligible() -> None:
    result = classify_location("Remote - Indonesia")

    assert result.status is LocationStatus.ELIGIBLE
    assert result.work_mode is WorkMode.REMOTE
    assert result.remote_scope is RemoteScope.INDONESIA_ONLY


def test_remote_role_with_indonesian_city_is_eligible() -> None:
    result = classify_location("Jakarta (Remote)")

    assert result.status is LocationStatus.ELIGIBLE
    assert result.work_mode is WorkMode.REMOTE
    assert result.remote_scope is RemoteScope.INDONESIA_ONLY
    assert result.city == "Jakarta"


def test_worldwide_remote_role_is_not_silently_treated_as_indonesia() -> None:
    result = classify_location("Remote - Worldwide")

    assert result.status is LocationStatus.REVIEW
    assert result.work_mode is WorkMode.REMOTE
    assert result.remote_scope is RemoteScope.WORLDWIDE
    assert result.country is None
    assert "remote" in result.reason.lower()


def test_bare_remote_location_stays_in_review_with_unknown_scope() -> None:
    result = classify_location("Remote")

    assert result.status is LocationStatus.REVIEW
    assert result.work_mode is WorkMode.REMOTE
    assert result.remote_scope is RemoteScope.UNKNOWN
    assert result.country is None


def test_remote_role_restricted_to_another_country_is_excluded() -> None:
    result = classify_location("Remote - Singapore")

    assert result.status is LocationStatus.EXCLUDED
    assert result.work_mode is WorkMode.REMOTE
    assert result.remote_scope is RemoteScope.COUNTRY_RESTRICTED
    assert result.country == "Singapore"


@pytest.mark.parametrize(
    ("raw_location", "expected_country"),
    [
        ("Singapore", "Singapore"),
        ("Kuala Lumpur, Malaysia", "Malaysia"),
        ("Bangkok, Thailand", "Thailand"),
        ("Ho Chi Minh City, Vietnam", "Vietnam"),
        ("Bengaluru, India", "India"),
        ("Sydney, Australia", "Australia"),
        ("London, United Kingdom", "United Kingdom"),
        ("New York, NY, United States", "United States"),
    ],
)
def test_foreign_locations_are_excluded(
    raw_location: str,
    expected_country: str,
) -> None:
    result = classify_location(raw_location)

    assert result.status is LocationStatus.EXCLUDED
    assert result.country == expected_country
    assert result.work_mode is WorkMode.ONSITE


@pytest.mark.parametrize(
    "raw_location",
    ["APAC", "Southeast Asia", "EMEA", "Multiple locations", "Global"],
)
def test_ambiguous_regions_go_to_review(raw_location: str) -> None:
    result = classify_location(raw_location)

    assert result.status is LocationStatus.REVIEW
    assert result.country is None
    assert result.city is None


@pytest.mark.parametrize(
    "raw_location",
    [None, "", "   ", "N/A", "Unknown", "Not specified", "-"],
)
def test_missing_location_is_unknown_review(raw_location: str | None) -> None:
    result = classify_location(raw_location)

    assert result.status is LocationStatus.REVIEW
    assert result.work_mode is WorkMode.UNKNOWN
    assert result.remote_scope is RemoteScope.UNKNOWN
    assert result.country is None
    assert result.city is None


def test_location_matching_ignores_case_and_punctuation() -> None:
    result = classify_location("JAKARTA, DKI JAKARTA")

    assert result.status is LocationStatus.ELIGIBLE
    assert result.city == "Jakarta"
    assert result.province == "DKI Jakarta"


def test_partial_word_matches_do_not_create_false_indonesia_detection() -> None:
    # "Balinese" contains "bali" but is not a location on its own; and
    # "Indianapolis" must not be read as India.
    result = classify_location("Indianapolis, Indiana")

    assert result.status is LocationStatus.REVIEW
    assert result.country is None


def test_province_only_location_is_eligible_without_city() -> None:
    result = classify_location("Jawa Barat")

    assert result.status is LocationStatus.ELIGIBLE
    assert result.province == "Jawa Barat"
    assert result.city is None
