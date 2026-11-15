"""Canonical opportunity and job-source records plus deterministic role matching.

The shape follows the P0 data model: one opportunity carries the original source
values, and one ``JobSourceRef`` records where it came from. Nothing here performs
I/O, guesses missing values, or invents a numeric relevance score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from personal_job_finder_api.domain.location import LocationClassification, LocationStatus
from personal_job_finder_api.domain.text import contains_phrase, normalize_words

AVAILABILITY_UNKNOWN = "unknown"


class RoleMatchType(StrEnum):
    """How a job title was tied to a configured target role."""

    CANONICAL_TITLE = "canonical_title"
    APPROVED_ALIAS = "approved_alias"
    DESCRIPTION_KEYWORD = "description_keyword"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class TargetRoleSpec:
    """The matching-relevant subset of a persisted target role."""

    role_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoleMatch:
    role_id: str
    match_type: RoleMatchType
    reason: str


@dataclass(frozen=True, slots=True)
class JobSourceRef:
    """Provenance for one source record."""

    source_type: str
    source_url: str
    external_source_id: str
    captured_at: datetime
    provenance_note: str


@dataclass(frozen=True, slots=True)
class OpportunityRecord:
    """One normalized opportunity with its original source values preserved."""

    company_id: str
    company_name: str
    job_title: str
    location: LocationClassification
    description: str | None
    posted_at: datetime | None
    source_updated_at: datetime | None
    source: JobSourceRef
    role_match: RoleMatch | None
    availability_status: str = AVAILABILITY_UNKNOWN


def match_target_role(
    title: str | None,
    description: str | None,
    roles: tuple[TargetRoleSpec, ...] | list[TargetRoleSpec],
) -> RoleMatch | None:
    """Match a title against configured roles in a deterministic order.

    Title matches on a canonical name or an approved alias are main-feed
    candidates. Description-only matches are recorded for review. A role whose
    excluded term appears in the title or description is never auto-accepted.
    """

    title_words = normalize_words(title or "")
    description_words = normalize_words(description or "")

    for role in roles:
        canonical_hit = contains_phrase(title_words, role.canonical_name)
        alias_hit = next(
            (alias for alias in role.aliases if contains_phrase(title_words, alias)),
            None,
        )
        if not canonical_hit and alias_hit is None:
            continue

        excluded_hit = next(
            (
                term
                for term in role.excluded_terms
                if contains_phrase(title_words, term) or contains_phrase(description_words, term)
            ),
            None,
        )
        if excluded_hit is not None:
            return RoleMatch(
                role_id=role.role_id,
                match_type=RoleMatchType.NEEDS_REVIEW,
                reason=(
                    f"The title matches '{role.canonical_name}', but the excluded "
                    f"term '{excluded_hit}' is present."
                ),
            )

        if canonical_hit:
            return RoleMatch(
                role_id=role.role_id,
                match_type=RoleMatchType.CANONICAL_TITLE,
                reason=f"The title contains the configured role '{role.canonical_name}'.",
            )
        return RoleMatch(
            role_id=role.role_id,
            match_type=RoleMatchType.APPROVED_ALIAS,
            reason=f"The title contains the approved alias '{alias_hit}'.",
        )

    for role in roles:
        canonical_hit = contains_phrase(description_words, role.canonical_name)
        alias_hit = next(
            (alias for alias in role.aliases if contains_phrase(description_words, alias)),
            None,
        )
        if not canonical_hit and alias_hit is None:
            continue
        term = role.canonical_name if canonical_hit else alias_hit
        return RoleMatch(
            role_id=role.role_id,
            match_type=RoleMatchType.DESCRIPTION_KEYWORD,
            reason=(
                f"The description mentions '{term}', but the title does not, so this "
                "record needs review."
            ),
        )

    return None


def is_main_feed(record: OpportunityRecord) -> bool:
    """True only for a confident role match in an eligible Indonesian location."""

    match = record.role_match
    return (
        match is not None
        and match.match_type in {RoleMatchType.CANONICAL_TITLE, RoleMatchType.APPROVED_ALIAS}
        and record.location.status is LocationStatus.ELIGIBLE
    )


def is_review_candidate(record: OpportunityRecord) -> bool:
    """True when the record is relevant but cannot be auto-accepted."""

    if record.role_match is None:
        return False
    if record.location.status is LocationStatus.EXCLUDED:
        return False
    return not is_main_feed(record)
