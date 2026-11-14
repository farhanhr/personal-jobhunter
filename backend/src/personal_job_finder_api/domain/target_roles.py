"""Pure validation for user-managed primary target roles."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from personal_job_finder_api.domain.search_policy import MAX_TARGET_ROLES

MAX_TARGET_ROLE_NAME_LENGTH = 200
MAX_ROLE_ALIAS_LENGTH = 200
MAX_ROLE_ID_LENGTH = 40

_ROLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class TargetRoleDraft:
    """A role submitted by the user; ``role_id`` is absent for new roles."""

    canonical_name: str
    aliases: tuple[str, ...] = ()
    role_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedTargetRole:
    """Normalized role data that is safe to persist."""

    role_id: str | None
    canonical_name: str
    aliases: tuple[str, ...]


class TargetRoleValidationError(ValueError):
    """A stable, machine-readable target-role validation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def validate_target_roles(
    drafts: Sequence[TargetRoleDraft],
    *,
    existing_role_ids: Collection[str] = (),
) -> tuple[ValidatedTargetRole, ...]:
    """Validate and normalize a complete replacement role list.

    An empty list is a valid initial configuration. The search policy remains
    responsible for refusing to start a run without an enabled role.
    """

    if len(drafts) > MAX_TARGET_ROLES:
        raise TargetRoleValidationError(
            "too_many_target_roles",
            "At most three primary target roles can be configured.",
            {
                "max_target_roles": MAX_TARGET_ROLES,
                "actual_target_roles": len(drafts),
            },
        )

    known_ids = set(existing_role_ids)
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    normalized_roles: list[tuple[str | None, str, tuple[str, ...]]] = []

    for index, draft in enumerate(drafts):
        role_id = _validate_role_id(
            draft.role_id,
            index=index,
            known_ids=known_ids,
            seen_ids=seen_ids,
        )
        canonical_name = _normalize_text(
            draft.canonical_name,
            code="invalid_target_role_name",
            message=(
                "Primary target role names must not be empty, contain control "
                "characters, or exceed 200 characters."
            ),
            max_length=MAX_TARGET_ROLE_NAME_LENGTH,
            details={"role_index": index},
        )
        folded_name = canonical_name.casefold()
        if folded_name in seen_names:
            raise TargetRoleValidationError(
                "duplicate_target_role",
                "Primary target role names must be unique.",
                {"canonical_name": folded_name, "role_index": index},
            )
        seen_names.add(folded_name)
        normalized_roles.append(
            (role_id, canonical_name, tuple(draft.aliases)),
        )

    seen_aliases: set[str] = set()
    validated: list[ValidatedTargetRole] = []
    for index, (role_id, canonical_name, aliases) in enumerate(normalized_roles):
        normalized_aliases: list[str] = []
        for alias_index, raw_alias in enumerate(aliases):
            alias = _normalize_text(
                raw_alias,
                code="invalid_role_alias",
                message=(
                    "Aliases must not be empty, contain control characters, or "
                    "exceed 200 characters."
                ),
                max_length=MAX_ROLE_ALIAS_LENGTH,
                details={"role_index": index, "alias_index": alias_index},
            )
            folded_alias = alias.casefold()
            if folded_alias in seen_names:
                raise TargetRoleValidationError(
                    "alias_matches_role_name",
                    "An alias cannot equal a primary target role name.",
                    {
                        "alias": folded_alias,
                        "role_index": index,
                        "alias_index": alias_index,
                    },
                )
            if folded_alias in seen_aliases:
                raise TargetRoleValidationError(
                    "duplicate_role_alias",
                    "Aliases must be unique across all primary target roles.",
                    {
                        "alias": folded_alias,
                        "role_index": index,
                        "alias_index": alias_index,
                    },
                )
            seen_aliases.add(folded_alias)
            normalized_aliases.append(alias)

        validated.append(
            ValidatedTargetRole(
                role_id=role_id,
                canonical_name=canonical_name,
                aliases=tuple(normalized_aliases),
            )
        )

    return tuple(validated)


def _validate_role_id(
    raw_role_id: str | None,
    *,
    index: int,
    known_ids: set[str],
    seen_ids: set[str],
) -> str | None:
    if raw_role_id is None:
        return None

    role_id = raw_role_id.strip()
    if (
        not role_id
        or len(role_id) > MAX_ROLE_ID_LENGTH
        or _ROLE_ID_PATTERN.fullmatch(role_id) is None
    ):
        raise TargetRoleValidationError(
            "invalid_role_id",
            "Role ids must be 1-40 letters, digits, underscores, or hyphens.",
            {"role_id": raw_role_id, "role_index": index},
        )
    if role_id not in known_ids:
        raise TargetRoleValidationError(
            "unknown_target_role",
            "Role id does not belong to the saved configuration.",
            {"role_id": role_id},
        )
    if role_id in seen_ids:
        raise TargetRoleValidationError(
            "duplicate_role_id",
            "Role ids must be unique.",
            {"role_id": role_id},
        )
    seen_ids.add(role_id)
    return role_id


def _normalize_text(
    raw_value: str,
    *,
    code: str,
    message: str,
    max_length: int,
    details: dict[str, Any],
) -> str:
    if not isinstance(raw_value, str):
        raise TargetRoleValidationError(code, message, details)
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
        raise TargetRoleValidationError(code, message, details)

    value = " ".join(raw_value.split())
    if not value or len(value) > max_length:
        raise TargetRoleValidationError(code, message, details)
    return value
