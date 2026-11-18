"""Tests for deterministic payload-to-header projection.

Every accepted header must map to a defined value or an intentional blank. The
projection must never invent employment type, experience level, skills, deadline,
author, or source content that the source did not provide.
"""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets.client import column_letter
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.sync.projections import (
    JOB_SOURCE_TAB,
    OPPORTUNITY_TAB,
    ProjectionError,
    is_tracker_only_opportunity_payload,
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

TRACKER_HEADERS = (
    "job_type",
    "date_applied",
    "application_status",
    "follow_up_date",
    "interview_date",
    "vacancy_url",
    "notes",
)

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
    assert column_letter(33) == "AG"
    assert column_letter(len(OPPORTUNITY_HEADERS)) == "AG"


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


def test_tracker_fields_are_appended_after_the_discovery_contract() -> None:
    assert OPPORTUNITY_HEADERS[-len(TRACKER_HEADERS) :] == TRACKER_HEADERS


def test_new_opportunity_initializes_every_tracker_field_blank() -> None:
    row = project_opportunity(OPPORTUNITY_PAYLOAD)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert {header: mapped.get(header) for header in TRACKER_HEADERS} == {
        header: "" for header in TRACKER_HEADERS
    }


def test_existing_tracker_fields_are_preserved_verbatim_on_update() -> None:
    tracker_values = {
        "job_type": "Development Program",
        "date_applied": "2026-11-10",
        "application_status": "Applied",
        "follow_up_date": "2026-11-18",
        "interview_date": "2026-11-20",
        "vacancy_url": "https://apply.example.test/acme/1001",
        "notes": "Referred by a former colleague.",
    }
    existing = [""] * len(OPPORTUNITY_HEADERS)
    for header, value in tracker_values.items():
        if header in OPPORTUNITY_HEADERS:
            existing[OPPORTUNITY_HEADERS.index(header)] = value

    row = project_opportunity(OPPORTUNITY_PAYLOAD, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert {header: mapped.get(header) for header in TRACKER_HEADERS} == tracker_values


def test_pending_tracker_fields_override_the_existing_row_but_others_survive() -> None:
    existing = [""] * len(OPPORTUNITY_HEADERS)
    tracked = {
        "job_type": "Internship",
        "date_applied": "2026-11-01",
        "application_status": "Applied",
        "follow_up_date": "",
        "interview_date": "",
        "vacancy_url": "",
        "notes": "Old note",
    }
    for header, value in tracked.items():
        if header in OPPORTUNITY_HEADERS:
            existing[OPPORTUNITY_HEADERS.index(header)] = value

    payload = dict(OPPORTUNITY_PAYLOAD)
    payload["date_applied"] = "2026-11-15"
    payload["notes"] = "New note"
    payload["_pending_user_fields"] = ["date_applied", "notes"]

    row = project_opportunity(payload, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped.get("job_type") == "Internship"
    assert mapped.get("application_status") == "Applied"
    assert mapped.get("date_applied") == "2026-11-15"
    assert mapped.get("notes") == "New note"
    assert mapped.get("company_name") == "Acme Example"


def test_tracker_patch_without_discovery_values_preserves_the_existing_row() -> None:
    existing = [""] * len(OPPORTUNITY_HEADERS)
    for header, value in {
        "company_name": "Acme Example",
        "job_title_original": "Backend Engineer",
        "location_status": "eligible",
        "work_mode": "onsite",
        "remote_scope": "not_applicable",
        "availability_status": "unknown",
        "user_status": "saved",
        "job_type": "Full-time",
    }.items():
        if header in OPPORTUNITY_HEADERS:
            existing[OPPORTUNITY_HEADERS.index(header)] = value

    payload = {
        "opportunity_id": "opp_1",
        "application_status": "Interview",
        "interview_date": "2026-11-20",
        "_pending_user_fields": ["application_status", "interview_date"],
    }

    row = project_opportunity(payload, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped.get("company_name") == "Acme Example"
    assert mapped.get("job_title_original") == "Backend Engineer"
    assert mapped.get("user_status") == "saved"
    assert mapped.get("job_type") == "Full-time"
    assert mapped.get("application_status") == "Interview"
    assert mapped.get("interview_date") == "2026-11-20"


def test_a_pending_tracker_edit_never_rewrites_a_stale_discovery_title() -> None:
    """Discovery values must follow the payload, not the previous sheet row.

    ``job_title`` and ``posted_at_utc`` are payload keys, while the sheet columns
    are ``job_title_original``, ``job_title_normalized`` and ``posted_at``. A
    pending tracker patch must still write the fresh discovery values; otherwise a
    rename during re-ingestion would be silently reverted in the canonical sheet.
    """

    existing = [""] * len(OPPORTUNITY_HEADERS)
    for header, value in {
        "opportunity_id": "opp_1",
        "company_name": "Acme Example",
        "job_title_original": "Backend Engineer",
        "job_title_normalized": "backend engineer",
        "posted_at": "2026-01-01",
        "notes": "Old note",
    }.items():
        existing[OPPORTUNITY_HEADERS.index(header)] = value

    payload = dict(OPPORTUNITY_PAYLOAD)
    payload["job_title"] = "Senior Backend Engineer"
    payload["posted_at_utc"] = "2026-11-01"
    payload["notes"] = "New note"
    payload["_pending_user_fields"] = ["notes"]

    row = project_opportunity(payload, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["job_title_original"] == "Senior Backend Engineer"
    assert mapped["job_title_normalized"] == "senior backend engineer"
    assert mapped["posted_at"] == "2026-11-01"
    assert mapped["notes"] == "New note"


def test_an_explicit_blank_pending_value_clears_the_canonical_cell() -> None:
    existing = [""] * len(OPPORTUNITY_HEADERS)
    for header, value in {
        "opportunity_id": "opp_1",
        "notes": "Clear me",
        "application_status": "Applied",
    }.items():
        existing[OPPORTUNITY_HEADERS.index(header)] = value

    payload = {
        "opportunity_id": "opp_1",
        "notes": "",
        "application_status": "Applied",
        "_pending_user_fields": ["notes"],
    }

    row = project_opportunity(payload, existing_row=existing)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["notes"] == ""
    assert mapped["application_status"] == "Applied"


def test_vacancy_url_is_never_derived_from_source_url() -> None:
    payload = dict(OPPORTUNITY_PAYLOAD)
    payload["source_url"] = "https://boards.greenhouse.io/acme/jobs/1001"

    row = project_opportunity(payload)
    mapped = dict(zip(OPPORTUNITY_HEADERS, row.values, strict=True))

    assert mapped["vacancy_url"] == ""


def test_a_pending_field_without_a_value_is_rejected() -> None:
    """Declaring ownership without supplying a value would erase the cell."""

    payload = {
        "opportunity_id": "opp_1",
        "_pending_user_fields": ["notes"],
    }

    with pytest.raises(ProjectionError):
        project_opportunity(payload, existing_row=[""] * len(OPPORTUNITY_HEADERS))


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


def test_job_source_requires_its_parent_opportunity() -> None:
    payload = dict(JOB_SOURCE_PAYLOAD)
    payload["opportunity_id"] = ""

    with pytest.raises(ProjectionError) as rejected:
        project_job_source(payload)

    assert rejected.value.code == "missing_parent_opportunity"


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


# ---------------------------------------------------------------------------
# Tracker-only payload classification (M4C3A hardening)
# ---------------------------------------------------------------------------


def test_a_complete_discovery_payload_is_not_classified_as_tracker_only() -> None:
    assert not is_tracker_only_opportunity_payload(OPPORTUNITY_PAYLOAD)


def test_a_tracker_only_payload_is_classified_as_tracker_only() -> None:
    payload = {
        "opportunity_id": "opp_1",
        "application_status": "Applied",
        "notes": "Waiting for reply",
        "_pending_user_fields": ["application_status", "notes"],
    }

    assert is_tracker_only_opportunity_payload(payload)


def test_a_payload_with_any_discovery_key_is_not_tracker_only() -> None:
    """A single discovery column is enough to prove the payload builds a row."""

    for key in ("company_id", "job_title", "location_status", "description"):
        payload = {
            "opportunity_id": "opp_1",
            "notes": "Waiting",
            "_pending_user_fields": ["notes"],
            key: "value",
        }
        assert not is_tracker_only_opportunity_payload(payload), key


def test_a_legacy_tracker_payload_without_a_discriminator_still_classifies() -> None:
    """Payloads queued before this change carry no marker and must still work."""

    legacy = {
        "opportunity_id": "opp_1",
        "job_type": "Internship",
        "_pending_user_fields": ["job_type"],
    }

    assert is_tracker_only_opportunity_payload(legacy)


def test_internal_metadata_never_becomes_a_projection_column() -> None:
    payload = {
        "opportunity_id": "opp_1",
        "notes": "Waiting",
        "_pending_user_fields": ["notes"],
    }

    row = project_opportunity(payload)

    assert row.headers == OPPORTUNITY_HEADERS
    assert "_pending_user_fields" not in row.headers
    assert "_pending_user_fields" not in OPPORTUNITY_HEADERS
