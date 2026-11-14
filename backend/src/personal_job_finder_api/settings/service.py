"""Target-role configuration service."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, format_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.domain.target_roles import (
    TargetRoleDraft,
    ValidatedTargetRole,
    validate_target_roles,
)
from personal_job_finder_api.repositories.target_roles import (
    StoredTargetRole,
    TargetRoleRecord,
    TargetRoleRepository,
)


@dataclass(frozen=True, slots=True)
class TargetRole:
    role_id: str
    canonical_name: str
    aliases: tuple[str, ...]


class TargetRoleService:
    """Manage the enabled target-role configuration as one atomic replacement."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = TargetRoleRepository()

    def list_roles(self) -> list[TargetRole]:
        with self._engine.connect() as connection:
            stored = self._repository.list_all(connection)
        return [
            TargetRole(
                role_id=role.role_id,
                canonical_name=role.canonical_name,
                aliases=role.aliases,
            )
            for role in stored
        ]

    def replace_roles(self, drafts: Sequence[TargetRoleDraft]) -> list[TargetRole]:
        now = ensure_aware_utc(self._clock(), "clock")

        with immediate_transaction(self._engine) as connection:
            existing = self._repository.list_all(connection)
            existing_by_id = {role.role_id: role for role in existing}
            validated = validate_target_roles(
                drafts,
                existing_role_ids=existing_by_id,
            )
            records = self._build_records(validated, existing_by_id, now)
            self._repository.replace_all(connection, records)

        return self._roles_from_records(records)

    def _build_records(
        self,
        validated: tuple[ValidatedTargetRole, ...],
        existing_by_id: dict[str, StoredTargetRole],
        now: datetime,
    ) -> tuple[TargetRoleRecord, ...]:
        used_ids = set(existing_by_id)
        claimed_ids = {role.role_id for role in validated if role.role_id is not None}
        assigned_ids: set[str] = set()
        existing_by_name = {
            role.canonical_name.casefold(): role for role in existing_by_id.values()
        }
        updated_at = format_utc(now)
        records: list[TargetRoleRecord] = []

        for role in validated:
            if role.role_id is None:
                matched = existing_by_name.get(role.canonical_name.casefold())
                if (
                    matched is not None
                    and matched.role_id not in claimed_ids
                    and matched.role_id not in assigned_ids
                ):
                    # A retried create keeps its identity when the canonical name
                    # already exists and no other draft claims that stored row.
                    role_id = matched.role_id
                    created_at = matched.created_at_utc
                    excluded_terms_json = matched.excluded_terms_json
                    seniority_json = matched.seniority_json
                else:
                    role_id = self._new_role_id(used_ids)
                    used_ids.add(role_id)
                    created_at = updated_at
                    excluded_terms_json = "[]"
                    seniority_json = None
            else:
                stored = existing_by_id[role.role_id]
                role_id = stored.role_id
                created_at = stored.created_at_utc
                excluded_terms_json = stored.excluded_terms_json
                seniority_json = stored.seniority_json

            assigned_ids.add(role_id)
            records.append(
                TargetRoleRecord(
                    role_id=role_id,
                    canonical_name=role.canonical_name,
                    aliases_json=json.dumps(
                        list(role.aliases),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    excluded_terms_json=excluded_terms_json,
                    seniority_json=seniority_json,
                    created_at_utc=created_at,
                    updated_at_utc=updated_at,
                )
            )

        return tuple(records)

    @staticmethod
    def _roles_from_records(
        records: tuple[TargetRoleRecord, ...],
    ) -> list[TargetRole]:
        ordered = sorted(
            records,
            key=lambda record: (record.canonical_name, record.role_id),
        )
        return [
            TargetRole(
                role_id=record.role_id,
                canonical_name=record.canonical_name,
                aliases=tuple(json.loads(record.aliases_json)),
            )
            for record in ordered
        ]

    @staticmethod
    def _new_role_id(used_ids: set[str]) -> str:
        while True:
            role_id = f"role_{uuid.uuid4().hex}"
            if role_id not in used_ids:
                return role_id
