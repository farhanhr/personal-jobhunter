"""SQLAlchemy Core repository for user-managed target roles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from personal_job_finder_api.db.tables import target_roles


@dataclass(frozen=True, slots=True)
class StoredTargetRole:
    role_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    excluded_terms_json: str
    seniority_json: str | None
    created_at_utc: str
    updated_at_utc: str


@dataclass(frozen=True, slots=True)
class TargetRoleRecord:
    role_id: str
    canonical_name: str
    aliases_json: str
    excluded_terms_json: str
    seniority_json: str | None
    created_at_utc: str
    updated_at_utc: str


class TargetRoleRepository:
    """Read and atomically replace the enabled target-role rows."""

    def list_all(self, connection: Connection) -> tuple[StoredTargetRole, ...]:
        rows = (
            connection.execute(
                select(target_roles)
                .where(target_roles.c.enabled.is_(True))
                .order_by(target_roles.c.canonical_name, target_roles.c.role_id)
            )
            .mappings()
            .all()
        )
        return tuple(
            StoredTargetRole(
                role_id=row["role_id"],
                canonical_name=row["canonical_name"],
                aliases=tuple(_load_string_list(row["aliases_json"])),
                excluded_terms_json=row["excluded_terms_json"],
                seniority_json=row["seniority_json"],
                created_at_utc=row["created_at_utc"],
                updated_at_utc=row["updated_at_utc"],
            )
            for row in rows
        )

    def replace_all(
        self,
        connection: Connection,
        records: tuple[TargetRoleRecord, ...],
    ) -> None:
        connection.execute(target_roles.delete())
        if not records:
            return

        connection.execute(
            target_roles.insert(),
            [
                {
                    "role_id": record.role_id,
                    "canonical_name": record.canonical_name,
                    "aliases_json": record.aliases_json,
                    "excluded_terms_json": record.excluded_terms_json,
                    "seniority_json": record.seniority_json,
                    "enabled": True,
                    "created_at_utc": record.created_at_utc,
                    "updated_at_utc": record.updated_at_utc,
                }
                for record in records
            ],
        )


def _load_string_list(value: str) -> list[str]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("stored role list must be a JSON array of strings")
    return parsed
