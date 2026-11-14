"""Pure validation tests for target-role configuration."""

from __future__ import annotations

import pytest

from personal_job_finder_api.domain.target_roles import (
    TargetRoleDraft,
    TargetRoleValidationError,
    validate_target_roles,
)


def _draft(
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    role_id: str | None = None,
) -> TargetRoleDraft:
    return TargetRoleDraft(canonical_name=name, aliases=aliases, role_id=role_id)


def test_empty_configuration_is_valid() -> None:
    assert validate_target_roles([]) == ()


def test_one_and_three_roles_are_valid() -> None:
    one = validate_target_roles([_draft("Backend Engineer")])
    three = validate_target_roles(
        [
            _draft("Backend Engineer"),
            _draft("Data Analyst"),
            _draft("Product Manager"),
        ]
    )

    assert [role.canonical_name for role in one] == ["Backend Engineer"]
    assert [role.canonical_name for role in three] == [
        "Backend Engineer",
        "Data Analyst",
        "Product Manager",
    ]


def test_four_roles_are_rejected() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [
                _draft("Role One"),
                _draft("Role Two"),
                _draft("Role Three"),
                _draft("Role Four"),
            ]
        )

    assert raised.value.code == "too_many_target_roles"
    assert raised.value.details == {"max_target_roles": 3, "actual_target_roles": 4}


def test_names_and_aliases_are_trimmed_and_internal_spaces_collapsed() -> None:
    validated = validate_target_roles(
        [_draft("  Backend   Engineer  ", aliases=("  Backend   Developer  ",))]
    )

    assert validated[0].canonical_name == "Backend Engineer"
    assert validated[0].aliases == ("Backend Developer",)


@pytest.mark.parametrize(
    "name",
    ["", "   ", "a" * 201, "Backend\nEngineer", "Backend\x00Engineer"],
)
def test_invalid_role_names_are_rejected(name: str) -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles([_draft(name)])

    assert raised.value.code == "invalid_target_role_name"


def test_role_name_at_the_length_limit_is_accepted() -> None:
    validated = validate_target_roles([_draft("a" * 200)])

    assert validated[0].canonical_name == "a" * 200


def test_duplicate_primary_roles_are_rejected_case_insensitively() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [
                _draft("Backend Engineer"),
                _draft("  backend   engineer  "),
            ]
        )

    assert raised.value.code == "duplicate_target_role"
    assert raised.value.details["canonical_name"] == "backend engineer"


def test_duplicate_aliases_within_one_role_are_rejected() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles([_draft("Backend Engineer", aliases=("Backend Dev", "backend dev"))])

    assert raised.value.code == "duplicate_role_alias"
    assert raised.value.details["alias"] == "backend dev"


def test_duplicate_aliases_across_roles_are_rejected() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [
                _draft("Backend Engineer", aliases=("Software Engineer",)),
                _draft("Data Analyst", aliases=("software engineer",)),
            ]
        )

    assert raised.value.code == "duplicate_role_alias"


def test_alias_cannot_equal_a_primary_role_name() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [
                _draft("Backend Engineer", aliases=("Data Analyst",)),
                _draft("Data Analyst"),
            ]
        )

    assert raised.value.code == "alias_matches_role_name"


@pytest.mark.parametrize(
    "alias",
    ["", "   ", "a" * 201, "Backend\nDeveloper", "Backend\x00Developer"],
)
def test_invalid_aliases_are_rejected(alias: str) -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles([_draft("Backend Engineer", aliases=(alias,))])

    assert raised.value.code == "invalid_role_alias"


def test_alias_at_the_length_limit_is_accepted() -> None:
    validated = validate_target_roles([_draft("Backend Engineer", aliases=("a" * 200,))])

    assert validated[0].aliases == ("a" * 200,)


def test_existing_role_id_is_preserved() -> None:
    validated = validate_target_roles(
        [_draft("Backend Engineer", role_id="role_existing")],
        existing_role_ids={"role_existing"},
    )

    assert validated[0].role_id == "role_existing"


def test_unknown_role_id_is_rejected() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [_draft("Backend Engineer", role_id="role_missing")],
            existing_role_ids={"role_existing"},
        )

    assert raised.value.code == "unknown_target_role"
    assert raised.value.details == {"role_id": "role_missing"}


def test_duplicate_role_ids_are_rejected() -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [
                _draft("Backend Engineer", role_id="role_existing"),
                _draft("Data Analyst", role_id="role_existing"),
            ],
            existing_role_ids={"role_existing"},
        )

    assert raised.value.code == "duplicate_role_id"


@pytest.mark.parametrize("role_id", ["", "   ", "role id", "x" * 41])
def test_invalid_role_ids_are_rejected(role_id: str) -> None:
    with pytest.raises(TargetRoleValidationError) as raised:
        validate_target_roles(
            [_draft("Backend Engineer", role_id=role_id)],
            existing_role_ids={role_id},
        )

    assert raised.value.code == "invalid_role_id"
