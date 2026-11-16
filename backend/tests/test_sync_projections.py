"""Tests for deterministic payload-to-header projection.

Every accepted header must map to a defined value or an intentional blank. The
projection must never invent employment type, experience level, skills, deadline,
author, or source content that the source did not provide.
"""

from __future__ import annotations

from typing import Any

from personal_job_finder_api.integrations.google_sheets.client import column_letter
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.sync.projections import (
    JOB_SOURCE_TAB,
    OPPORTUNITY_TAB,
    ProjectionError,
    project_job_source,
    project_opportunity,
    row_values,
)

OPPORTUNITY_PAYLOAD: dict[str, Any] = {
    "opportunity_id": "opp_1",
    "company_id": "company_acme",
    "company_name": "Acme Example",
    "job_title": "Backend Engineer (Remote)",
    "target_role_id": "role_backend",
    "role_match_type": "canonical_title",
    "role_match_reason": "Exact canonical title match.",
    "location_raw": "Jakarta, Indonesia",
    "country": "Indonesia",
    "province": "DKI Jakarta",
    "city": "Jakarta",
    "location_status": "eligible",
    "work_mode": "onsite",
    "remote_scope": "not_applicable",
    "description": "<p>Build services.</p>",
    "posted_at_utc": "2026-11-01T00:00:00.000000Z",
    "first_seen_at_utc": "2026-11-15T03:00:00.000000Z",
    "last_seen_at_utc": "2026-11-15T03:00:00.000000Z",
    "availability_status": "unknown",
    "feed_bucket": "main",
    "created_at_utc": "2026-11-15T03:00:00.000000Z",
    "updated_at_utc": "2026-11-15T03:00:00.000000Z",
}

JOB_SOURCE_PAYLOAD: dict[str, Any] = {
    "source_id": "src_1",
    "opportunity_id": "opp_1",
    "source_type": "greenhouse",
    "source_url": "https://boards.greenhouse.io/acme/jobs/1001",
    "safe_source_url": "https://boards.greenhouse.io/acme/jobs/1001",
    "external_source_id": "1001",
    "captured_at_utc": "2026-11-15T03:00:00.000000Z",
    "last_seen_at_utc": "2026-11-15T03:00:00.000000Z",
    "provenance_method": "ats_api",
    "observation_status": "seen",
}


def test_column_letter_covers_the_single_and_double_letter_ranges() -> None:
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    assert column_letter(len(OPPORTUNITY_HEADERS)) == "Z"


def test_every_opportunity_header_is_projected_exactly_once() -> None:
    row = project_opportunity(OPPORTUNITY_PAYLOAD)

    assert row.tab == OPPORTUNITY_TAB
    assert row.identity_column == "opportunity_id"
    assert row.identity == "opp_1"
    assert len(row.values) == len(OPPORTUNITY_HEADERS)


def test_opportunity_values_follow_the_accepted_column_order() -> None:
    row = project_opportunity(OPPORTUNITY_PAYLOAD)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["opportunity_id"] == "opp_1"
    assert mapped["company_name"] == "Acme Example"
    assert mapped["job_title_original"] == "Backend Engineer (Remote)"
    assert mapped["job_title_normalized"] == "backend engineer remote"
    assert mapped["target_role_id"] == "role_backend"
    assert mapped["role_match_type"] == "canonical_title"
    assert mapped["location_raw"] == "Jakarta, Indonesia"
    assert mapped["country"] == "Indonesia"
    assert mapped["province"] == "DKI Jakarta"
    assert mapped["city"] == "Jakarta"
    assert mapped["location_status"] == "eligible"
    assert mapped["work_mode"] == "onsite"
    assert mapped["remote_scope"] == "not_applicable"
    assert mapped["description"] == "<p>Build services.</p>"
    assert mapped["posted_at"] == "2026-11-01T00:00:00.000000Z"
    assert mapped["availability_status"] == "unknown"
    assert mapped["first_seen_at_utc"] == "2026-11-15T03:00:00.000000Z"
    assert mapped["last_seen_at_utc"] == "2026-11-15T03:00:00.000000Z"
    assert mapped["created_at_utc"] == "2026-11-15T03:00:00.000000Z"
    assert mapped["updated_at_utc"] == "2026-11-15T03:00:00.000000Z"


def test_unavailable_opportunity_fields_are_left_blank_not_invented() -> None:
    row = project_opportunity(OPPORTUNITY_PAYLOAD)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["employment_type"] == ""
    assert mapped["experience_level"] == ""
    assert mapped["required_skills_json"] == ""
    assert mapped["deadline"] == ""


def test_missing_optional_payload_keys_project_to_blank_cells() -> None:
    minimal = {
        "opportunity_id": "opp_2",
        "company_name": "Acme Example",
        "job_title": "Backend Engineer",
        "location_status": "review",
        "work_mode": "unknown",
        "remote_scope": "unknown",
        "availability_status": "unknown",
        "first_seen_at_utc": "2026-11-15T03:00:00.000000Z",
        "last_seen_at_utc": "2026-11-15T03:00:00.000000Z",
    }

    row = project_opportunity(minimal)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["target_role_id"] == ""
    assert mapped["role_match_type"] == ""
    assert mapped["role_match_reason"] == ""
    assert mapped["country"] == ""
    assert mapped["description"] == ""
    assert mapped["posted_at"] == ""
    assert mapped["job_title_normalized"] == "backend engineer"


def test_new_opportunity_initializes_user_status_to_the_documented_default() -> None:
    row = project_opportunity(OPPORTUNITY_PAYLOAD)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["user_status"] == "new"


def test_existing_user_status_is_preserved_on_update() -> None:
    existing = [""] * len(OPPORTUNITY_HEADERS)
    existing[OPPORTUNITY_HEADERS.index("user_status")] = "saved"
    existing[OPPORTUNITY_HEADERS.index("company_name")] = "Stale Name"

    row = project_opportunity(OPPORTUNITY_PAYLOAD, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["user_status"] == "saved"
    assert mapped["company_name"] == "Acme Example"


def test_blank_user_status_in_an_existing_row_is_not_overwritten_with_a_default() -> None:
    existing = [""] * len(OPPORTUNITY_HEADERS)

    row = project_opportunity(OPPORTUNITY_PAYLOAD, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["user_status"] == ""


def test_projection_requires_a_stable_identity() -> None:
    payload = dict(OPPORTUNITY_PAYLOAD)
    payload["opportunity_id"] = ""

    try:
        project_opportunity(payload)
    except ProjectionError as error:
        assert error.code == "missing_identity"
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("an empty identity must not be projected")


def test_every_job_source_header_is_projected_exactly_once() -> None:
    row = project_job_source(JOB_SOURCE_PAYLOAD)
    mapped = dict(zip(JOB_SOURCE_HEADERS, row.values, strict=True))

    assert row.tab == JOB_SOURCE_TAB
    assert row.identity_column == "source_id"
    assert row.identity == "src_1"
    assert len(row.values) == len(JOB_SOURCE_HEADERS)
    assert mapped["opportunity_id"] == "opp_1"
    assert mapped["source_type"] == "greenhouse"
    assert mapped["source_url"] == "https://boards.greenhouse.io/acme/jobs/1001"
    assert mapped["external_source_id"] == "1001"
    assert mapped["captured_at_utc"] == "2026-11-15T03:00:00.000000Z"
    assert mapped["last_seen_at_utc"] == "2026-11-15T03:00:00.000000Z"
    assert mapped["provenance_method"] == "ats_api"
    assert mapped["observation_status"] == "seen"


def test_job_source_author_and_excerpt_are_blank_when_not_supplied() -> None:
    row = project_job_source(JOB_SOURCE_PAYLOAD)
    mapped = dict(zip(JOB_SOURCE_HEADERS, row.values, strict=True))

    assert mapped["author_name"] == ""
    assert mapped["author_role"] == ""
    assert mapped["source_text_excerpt"] == ""
    assert mapped["posted_at"] == ""


def test_job_source_never_writes_an_unsafe_source_url() -> None:
    payload = dict(JOB_SOURCE_PAYLOAD)
    payload["source_url"] = "javascript:alert(1)"
    payload["safe_source_url"] = None

    row = project_job_source(payload)
    mapped = dict(zip(JOB_SOURCE_HEADERS, row.values, strict=True))

    assert mapped["source_url"] == ""


def test_row_values_converts_blank_and_none_to_empty_cells() -> None:
    assert row_values(("a", None, "", 3, True)) == ("a", "", "", "3", "True")


def test_projection_never_evaluates_source_text_as_a_formula() -> None:
    payload = dict(OPPORTUNITY_PAYLOAD)
    payload["company_name"] = '=IMPORTXML("https://example.test")'
    payload["description"] = "@SUM(1+1)"

    row = project_opportunity(payload)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["company_name"] == '=IMPORTXML("https://example.test")'
    assert mapped["description"] == "@SUM(1+1)"
