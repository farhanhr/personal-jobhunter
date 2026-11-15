"""Company source registry service.

Registration never implies approval: every new source starts ``unreviewed`` and can
only be fetched after an explicit ``permitted`` decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine

from personal_job_finder_api.core.time import ensure_aware_utc, format_utc, utc_now
from personal_job_finder_api.db.engine import immediate_transaction
from personal_job_finder_api.domain.companies import (
    CompanyValidationError,
    is_valid_board_token,
    validate_board_token,
    validate_company_name,
)
from personal_job_finder_api.repositories.companies import (
    CompanyRecord,
    CompanyRepository,
    StoredCompany,
)

GREENHOUSE_ATS_TYPE = "greenhouse"
UNREVIEWED = "unreviewed"
PERMITTED = "permitted"
BLOCKED = "blocked"
POLICY_STATUSES = (UNREVIEWED, PERMITTED, BLOCKED)


@dataclass(frozen=True, slots=True)
class CompanySource:
    company_id: str
    company_name: str
    ats_type: str
    board_token: str
    enabled: bool
    policy_status: str

    @property
    def can_fetch(self) -> bool:
        return (
            self.enabled
            and self.policy_status == PERMITTED
            and is_valid_board_token(self.board_token)
        )


class CompanySourceNotFound(LookupError):
    """Raised when a company id does not exist."""


class DuplicateCompanySource(Exception):
    """Raised when a company name or board token is already registered."""

    def __init__(self, code: str, message: str, details: dict[str, object]) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class CompanySourceService:
    """Manage the local registry of permitted Greenhouse company boards."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._repository = CompanyRepository()

    def list_companies(self) -> list[CompanySource]:
        with self._engine.connect() as connection:
            stored = self._repository.list_all(connection)
        return [_to_source(company) for company in stored]

    def create_company(self, *, company_name: str, board_token: str) -> CompanySource:
        name = validate_company_name(company_name)
        token = validate_board_token(board_token)
        now = format_utc(ensure_aware_utc(self._clock(), "clock"))

        with immediate_transaction(self._engine) as connection:
            if self._repository.find_by_board_token(
                connection,
                ats_type=GREENHOUSE_ATS_TYPE,
                board_token=token,
            ):
                raise DuplicateCompanySource(
                    "duplicate_company_source",
                    "This Greenhouse board token is already registered.",
                    {"board_token": token},
                )
            if self._repository.find_by_name(connection, name):
                raise DuplicateCompanySource(
                    "duplicate_company_source",
                    "A company with this name is already registered.",
                    {"company_name": name},
                )

            record = CompanyRecord(
                company_id=f"company_{uuid.uuid4().hex}",
                company_name=name,
                ats_type=GREENHOUSE_ATS_TYPE,
                ats_board_token=token,
                enabled=True,
                policy_status=UNREVIEWED,
                created_at_utc=now,
                updated_at_utc=now,
            )
            self._repository.insert(connection, record)

        return _to_source(
            StoredCompany(
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

    def update_company(
        self,
        company_id: str,
        *,
        policy_status: str | None = None,
        enabled: bool | None = None,
    ) -> CompanySource:
        if policy_status is None and enabled is None:
            raise CompanyValidationError(
                "empty_company_update",
                "Provide policy_status, enabled, or both.",
            )
        if policy_status is not None and policy_status not in POLICY_STATUSES:
            raise CompanyValidationError(
                "invalid_policy_status",
                "policy_status must be unreviewed, permitted, or blocked.",
                {"policy_status": policy_status},
            )

        now = format_utc(ensure_aware_utc(self._clock(), "clock"))

        with immediate_transaction(self._engine) as connection:
            existing = self._repository.get(connection, company_id)
            if existing is None:
                raise CompanySourceNotFound(company_id)

            updated = self._repository.update_state(
                connection,
                company_id,
                policy_status=policy_status or existing.policy_status,
                enabled=existing.enabled if enabled is None else enabled,
                updated_at_utc=now,
            )
            if not updated:
                raise CompanySourceNotFound(company_id)

            stored = self._repository.get(connection, company_id)
            if stored is None:  # pragma: no cover - guarded by the update above
                raise CompanySourceNotFound(company_id)

        return _to_source(stored)


def _to_source(company: StoredCompany) -> CompanySource:
    return CompanySource(
        company_id=company.company_id,
        company_name=company.company_name,
        ats_type=company.ats_type,
        board_token=company.ats_board_token or "",
        enabled=company.enabled,
        policy_status=company.policy_status,
    )
