"""SQLAlchemy Core repository for search-policy state and run persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection

from personal_job_finder_api.core.time import format_utc, parse_utc
from personal_job_finder_api.db.tables import companies, search_runs, target_roles
from personal_job_finder_api.domain.search_policy import ACTIVE_RUN_STATUSES, RunStatus

CONFIG_SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PolicyState:
    target_role_count: int
    usable_source_count: int
    accepted_runs_for_day: int
    last_accepted_at: datetime | None
    active_run_exists: bool


@dataclass(frozen=True, slots=True)
class PersistedSearchRun:
    run_id: str
    idempotency_key: str
    request_fingerprint: str
    operational_day: date
    status: RunStatus
    accepted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    config_snapshot_json: str
    error_summary: str | None
    progress_json: str | None
    source_summary_json: str | None

    @classmethod
    def from_mapping(cls, row: Any) -> PersistedSearchRun:
        return cls(
            run_id=row["run_id"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            operational_day=date.fromisoformat(row["operational_day"]),
            status=RunStatus(row["status"]),
            accepted_at=parse_utc(row["accepted_at_utc"]),
            started_at=parse_utc(row["started_at_utc"]) if row["started_at_utc"] else None,
            finished_at=parse_utc(row["finished_at_utc"]) if row["finished_at_utc"] else None,
            config_snapshot_json=row["config_snapshot_json"],
            error_summary=row["error_summary"],
            progress_json=row["progress_json"],
            source_summary_json=row["source_summary_json"],
        )

    @property
    def config_snapshot(self) -> dict[str, Any]:
        value = json.loads(self.config_snapshot_json)
        if not isinstance(value, dict):
            raise ValueError("stored configuration snapshot is not a JSON object")
        return value


class SearchPolicyRepository:
    """Queries that read and write the operational policy state."""

    def load_policy_state(
        self,
        connection: Connection,
        operational_day: date,
    ) -> PolicyState:
        role_count = connection.execute(
            select(func.count()).select_from(target_roles).where(target_roles.c.enabled.is_(True))
        ).scalar_one()

        source_count = connection.execute(
            select(func.count()).select_from(companies).where(_usable_source_condition())
        ).scalar_one()

        day_count = connection.execute(
            select(func.count())
            .select_from(search_runs)
            .where(search_runs.c.operational_day == operational_day.isoformat())
        ).scalar_one()

        latest_accepted_raw = connection.execute(
            select(func.max(search_runs.c.accepted_at_utc))
        ).scalar_one()

        active_count = connection.execute(
            select(func.count())
            .select_from(search_runs)
            .where(search_runs.c.status.in_(tuple(status.value for status in ACTIVE_RUN_STATUSES)))
        ).scalar_one()

        return PolicyState(
            target_role_count=int(role_count),
            usable_source_count=int(source_count),
            accepted_runs_for_day=int(day_count),
            last_accepted_at=parse_utc(latest_accepted_raw) if latest_accepted_raw else None,
            active_run_exists=int(active_count) > 0,
        )

    def find_by_idempotency_key(
        self,
        connection: Connection,
        idempotency_key: str,
    ) -> PersistedSearchRun | None:
        row = (
            connection.execute(
                select(search_runs).where(search_runs.c.idempotency_key == idempotency_key)
            )
            .mappings()
            .one_or_none()
        )
        return PersistedSearchRun.from_mapping(row) if row is not None else None

    def get_run(self, connection: Connection, run_id: str) -> PersistedSearchRun | None:
        row = (
            connection.execute(select(search_runs).where(search_runs.c.run_id == run_id))
            .mappings()
            .one_or_none()
        )
        return PersistedSearchRun.from_mapping(row) if row is not None else None

    def get_latest_run(self, connection: Connection) -> PersistedSearchRun | None:
        """Return the most recently accepted run, or None if no runs exist.

        Selection criteria:
        - Primary: accepted_at_utc descending (most recent first)
        - Tie-breaker: run_id ascending (deterministic)
        """
        row = (
            connection.execute(
                select(search_runs)
                .order_by(
                    search_runs.c.accepted_at_utc.desc(),
                    search_runs.c.run_id.asc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return PersistedSearchRun.from_mapping(row) if row is not None else None

    def insert_run(self, connection: Connection, run: PersistedSearchRun) -> None:
        connection.execute(
            search_runs.insert().values(
                run_id=run.run_id,
                idempotency_key=run.idempotency_key,
                request_fingerprint=run.request_fingerprint,
                operational_day=run.operational_day.isoformat(),
                status=run.status.value,
                accepted_at_utc=format_utc(run.accepted_at),
                started_at_utc=format_utc(run.started_at) if run.started_at else None,
                finished_at_utc=format_utc(run.finished_at) if run.finished_at else None,
                config_snapshot_json=run.config_snapshot_json,
                error_summary=run.error_summary,
                progress_json=run.progress_json,
                source_summary_json=run.source_summary_json,
            )
        )

    def mark_running(
        self,
        connection: Connection,
        run_id: str,
        *,
        started_at: datetime,
    ) -> bool:
        """Claim an accepted run for this process; return False if already claimed."""

        result = connection.execute(
            update(search_runs)
            .where(
                search_runs.c.run_id == run_id,
                search_runs.c.status == RunStatus.ACCEPTED.value,
            )
            .values(
                status=RunStatus.RUNNING.value,
                started_at_utc=format_utc(started_at),
            )
        )
        return result.rowcount == 1

    def finish_run(
        self,
        connection: Connection,
        run_id: str,
        *,
        status: RunStatus,
        finished_at: datetime,
        error_summary: str | None,
        progress_json: str,
        source_summary_json: str,
    ) -> bool:
        """Persist a terminal state for a running run."""

        if status.is_active:
            raise ValueError("finish_run requires a terminal status")

        result = connection.execute(
            update(search_runs)
            .where(
                search_runs.c.run_id == run_id,
                search_runs.c.status == RunStatus.RUNNING.value,
            )
            .values(
                status=status.value,
                finished_at_utc=format_utc(finished_at),
                error_summary=error_summary,
                progress_json=progress_json,
                source_summary_json=source_summary_json,
            )
        )
        return result.rowcount == 1

    def load_configuration_snapshot(self, connection: Connection) -> dict[str, Any]:
        role_rows = (
            connection.execute(
                select(target_roles)
                .where(target_roles.c.enabled.is_(True))
                .order_by(target_roles.c.canonical_name)
            )
            .mappings()
            .all()
        )
        company_rows = (
            connection.execute(
                select(companies)
                .where(companies.c.enabled.is_(True))
                .order_by(companies.c.company_name)
            )
            .mappings()
            .all()
        )

        return {
            "schema_version": CONFIG_SNAPSHOT_SCHEMA_VERSION,
            "target_roles": [
                {
                    "role_id": row["role_id"],
                    "canonical_name": row["canonical_name"],
                    "aliases": _load_string_list(row["aliases_json"]),
                    "excluded_terms": _load_string_list(row["excluded_terms_json"]),
                    "seniority": (
                        json.loads(row["seniority_json"])
                        if row["seniority_json"] is not None
                        else None
                    ),
                }
                for row in role_rows
            ],
            "companies": [
                {
                    "company_id": row["company_id"],
                    "company_name": row["company_name"],
                    "company_website": row["company_website"],
                    "career_url": row["career_url"],
                    "ats_type": row["ats_type"],
                    "ats_board_token": row["ats_board_token"],
                    "policy_status": row["policy_status"],
                    "access_notes": row["access_notes"],
                }
                for row in company_rows
            ],
        }


def _usable_source_condition():
    board_token = func.trim(func.coalesce(companies.c.ats_board_token, ""))
    career_url = func.trim(func.coalesce(companies.c.career_url, ""))

    return and_(
        companies.c.enabled.is_(True),
        companies.c.policy_status == "permitted",
        or_(
            and_(
                companies.c.ats_type.in_(("greenhouse", "lever", "ashby")),
                board_token != "",
            ),
            and_(companies.c.ats_type == "custom", career_url != ""),
        ),
    )


def _load_string_list(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("stored role list must be a JSON array of strings")
    return parsed
