"""SQLAlchemy Core repository for locally managed company sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from personal_job_finder_api.db.tables import companies


@dataclass(frozen=True, slots=True)
class StoredCompany:
    company_id: str
    company_name: str
    company_website: str | None
    career_url: str | None
    ats_type: str
    ats_board_token: str | None
    enabled: bool
    policy_status: str
    access_notes: str | None
    created_at_utc: str
    updated_at_utc: str

    @classmethod
    def from_mapping(cls, row: Any) -> StoredCompany:
        return cls(
            company_id=row["company_id"],
            company_name=row["company_name"],
            company_website=row["company_website"],
            career_url=row["career_url"],
            ats_type=row["ats_type"],
            ats_board_token=row["ats_board_token"],
            enabled=bool(row["enabled"]),
            policy_status=row["policy_status"],
            access_notes=row["access_notes"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    company_id: str
    company_name: str
    ats_type: str
    ats_board_token: str
    enabled: bool
    policy_status: str
    created_at_utc: str
    updated_at_utc: str


class CompanyRepository:
    """Persist and query the local company-source registry."""

    def list_all(self, connection: Connection) -> list[StoredCompany]:
        rows = (
            connection.execute(
                select(companies).order_by(
                    companies.c.company_name,
                    companies.c.company_id,
                )
            )
            .mappings()
            .all()
        )
        return [StoredCompany.from_mapping(row) for row in rows]

    def get(self, connection: Connection, company_id: str) -> StoredCompany | None:
        row = (
            connection.execute(select(companies).where(companies.c.company_id == company_id))
            .mappings()
            .one_or_none()
        )
        return StoredCompany.from_mapping(row) if row is not None else None

    def find_by_board_token(
        self,
        connection: Connection,
        *,
        ats_type: str,
        board_token: str,
    ) -> StoredCompany | None:
        row = (
            connection.execute(
                select(companies).where(
                    companies.c.ats_type == ats_type,
                    func.lower(companies.c.ats_board_token) == board_token.casefold(),
                )
            )
            .mappings()
            .one_or_none()
        )
        return StoredCompany.from_mapping(row) if row is not None else None

    def find_by_name(self, connection: Connection, company_name: str) -> StoredCompany | None:
        row = (
            connection.execute(
                select(companies).where(
                    func.lower(companies.c.company_name) == company_name.casefold()
                )
            )
            .mappings()
            .one_or_none()
        )
        return StoredCompany.from_mapping(row) if row is not None else None

    def insert(self, connection: Connection, record: CompanyRecord) -> None:
        connection.execute(
            companies.insert().values(
                company_id=record.company_id,
                company_name=record.company_name,
                company_website=None,
                career_url=None,
                ats_type=record.ats_type,
                ats_board_token=record.ats_board_token,
                enabled=record.enabled,
                policy_status=record.policy_status,
                access_notes=None,
                created_at_utc=record.created_at_utc,
                updated_at_utc=record.updated_at_utc,
            )
        )

    def update_state(
        self,
        connection: Connection,
        company_id: str,
        *,
        policy_status: str,
        enabled: bool,
        updated_at_utc: str,
    ) -> bool:
        result = connection.execute(
            update(companies)
            .where(companies.c.company_id == company_id)
            .values(
                policy_status=policy_status,
                enabled=enabled,
                updated_at_utc=updated_at_utc,
            )
        )
        return result.rowcount == 1
