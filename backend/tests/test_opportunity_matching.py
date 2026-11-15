"""Tests for deterministic target-role matching and opportunity records."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from personal_job_finder_api.domain.location import LocationStatus, classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    RoleMatchType,
    TargetRoleSpec,
    is_main_feed,
    is_review_candidate,
    match_target_role,
)

BACKEND_ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer", "API Engineer"),
)
DATA_ROLE = TargetRoleSpec(
    role_id="role_data",
    canonical_name="Data Analyst",
    aliases=("Business Intelligence Analyst",),
)
ROLES = (BACKEND_ROLE, DATA_ROLE)


def test_canonical_title_match_is_a_main_feed_match() -> None:
    match = match_target_role("Senior Backend Engineer", None, ROLES)

    assert match is not None
    assert match.role_id == "role_backend"
    assert match.match_type is RoleMatchType.CANONICAL_TITLE
    assert "Backend Engineer" in match.reason


def test_approved_alias_match_is_recorded_as_alias() -> None:
    match = match_target_role("Backend Developer", None, ROLES)

    assert match is not None
    assert match.role_id == "role_backend"
    assert match.match_type is RoleMatchType.APPROVED_ALIAS


def test_matching_is_case_and_punctuation_insensitive() -> None:
    match = match_target_role("backend   engineer (remote)", None, ROLES)

    assert match is not None
    assert match.match_type is RoleMatchType.CANONICAL_TITLE


def test_unlisted_title_variation_does_not_match() -> None:
    # "Engineering Manager" is not the configured role or an approved alias.
    assert match_target_role("Engineering Manager", None, ROLES) is None


def test_unrelated_role_is_not_matched_by_shared_words() -> None:
    assert (
        match_target_role(
            "Data Engineer",
            "Work with data pipelines and reporting.",
            ROLES,
        )
        is None
    )


def test_description_only_mention_requires_review() -> None:
    match = match_target_role(
        "Software Development Engineer",
        "You will work closely with our Backend Engineer group.",
        ROLES,
    )

    assert match is not None
    assert match.role_id == "role_backend"
    assert match.match_type is RoleMatchType.DESCRIPTION_KEYWORD


def test_excluded_term_turns_a_title_match_into_review() -> None:
    role = TargetRoleSpec(
        role_id="role_backend",
        canonical_name="Backend Engineer",
        aliases=(),
        excluded_terms=("internship",),
    )

    match = match_target_role(
        "Backend Engineer Internship",
        "Six month program.",
        (role,),
    )

    assert match is not None
    assert match.match_type is RoleMatchType.NEEDS_REVIEW
    assert "internship" in match.reason.casefold()


def test_first_configured_role_wins_when_two_roles_match() -> None:
    match = match_target_role("Backend Engineer / Data Analyst", None, ROLES)

    assert match is not None
    assert match.role_id == "role_backend"


def test_match_with_no_roles_configured_returns_none() -> None:
    assert match_target_role("Backend Engineer", None, ()) is None


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Backend Engineer", None),
        (None, "Backend Engineer"),
    ],
)
def test_matching_requires_a_title_or_description_string(
    title: str | None,
    description: str | None,
) -> None:
    match = match_target_role(title, description, ROLES)

    assert match is not None
    assert match.role_id == "role_backend"


def _record(
    *,
    title: str = "Backend Engineer",
    location: str = "Jakarta, Indonesia",
    role_match=None,
    description: str | None = "Build services.",
) -> OpportunityRecord:
    source = JobSourceRef(
        source_type="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/1",
        external_source_id="1",
        captured_at=datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
        provenance_note="Public Greenhouse job board",
    )
    return OpportunityRecord(
        company_id="company_1",
        company_name="Example Company",
        job_title=title,
        location=classify_location(location),
        description=description,
        posted_at=None,
        source_updated_at=None,
        source=source,
        role_match=role_match,
    )


def test_eligible_location_and_title_match_enter_the_main_feed() -> None:
    record = _record(role_match=match_target_role("Backend Engineer", None, ROLES))

    assert is_main_feed(record) is True
    assert is_review_candidate(record) is False


def test_unknown_location_keeps_a_matching_role_in_review() -> None:
    record = _record(
        location="Remote",
        role_match=match_target_role("Backend Engineer", None, ROLES),
    )

    assert is_main_feed(record) is False
    assert is_review_candidate(record) is True


def test_foreign_location_excludes_even_a_matching_role() -> None:
    record = _record(
        location="Singapore",
        role_match=match_target_role("Backend Engineer", None, ROLES),
    )

    assert record.location.status is LocationStatus.EXCLUDED
    assert is_main_feed(record) is False
    assert is_review_candidate(record) is False


def test_unrelated_role_is_excluded_not_reviewed() -> None:
    record = _record(title="Accountant", role_match=None)

    assert is_main_feed(record) is False
    assert is_review_candidate(record) is False


def test_original_title_is_never_replaced_by_the_matched_role() -> None:
    record = _record(
        title="Senior Backend Engineer (Payments)",
        role_match=match_target_role("Senior Backend Engineer (Payments)", None, ROLES),
    )

    assert record.job_title == "Senior Backend Engineer (Payments)"
    assert record.role_match is not None
    assert record.role_match.role_id == "role_backend"
