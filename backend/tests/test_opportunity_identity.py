"""Tests for the deterministic source-record identity rule."""

from __future__ import annotations

from personal_job_finder_api.domain.opportunity_identity import (
    opportunity_id_for,
    source_id_for,
)


def test_same_source_identity_yields_the_same_opportunity_id() -> None:
    first = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )
    second = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )

    assert first == second
    assert first.startswith("opp_")
    assert len(first) <= 40


def test_identity_depends_on_company_not_only_on_the_job_id() -> None:
    acme = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )
    other = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_other",
        external_source_id="1001",
    )

    assert acme != other


def test_identity_depends_on_source_type() -> None:
    greenhouse = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )
    lever = opportunity_id_for(
        source_type="lever",
        company_id="company_acme",
        external_source_id="1001",
    )

    assert greenhouse != lever


def test_identity_is_case_and_whitespace_normalized() -> None:
    assert opportunity_id_for(
        source_type="Greenhouse",
        company_id="company_acme",
        external_source_id=" 1001 ",
    ) == opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )


def test_identity_rejects_empty_parts() -> None:
    for source_type, company_id, external_id in (
        ("", "company_acme", "1001"),
        ("greenhouse", "", "1001"),
        ("greenhouse", "company_acme", ""),
        ("greenhouse", "company_acme", "   "),
    ):
        try:
            opportunity_id_for(
                source_type=source_type,
                company_id=company_id,
                external_source_id=external_id,
            )
        except ValueError:
            continue
        raise AssertionError("expected ValueError for an empty identity part")


def test_separator_characters_cannot_collide_two_identities() -> None:
    # A naive "a:b" + "c" join would collide with "a" + "b:c".
    left = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_a:b",
        external_source_id="c",
    )
    right = opportunity_id_for(
        source_type="greenhouse",
        company_id="company_a",
        external_source_id="b:c",
    )

    assert left != right


def test_source_id_is_stable_and_distinct_from_opportunity_id() -> None:
    source = source_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )

    assert source == source_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )
    assert source.startswith("src_")
    assert source != opportunity_id_for(
        source_type="greenhouse",
        company_id="company_acme",
        external_source_id="1001",
    )
