"""Shared test fixtures for M1 persistence and policy tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.db.tables import companies, target_roles


@pytest.fixture
def database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'personal-job-finder-test.sqlite3'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    run_migrations(database_url)
    database_engine = create_engine_for_url(database_url)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def seed_config(engine: Engine) -> Callable[..., None]:
    def _seed(
        *,
        roles: int = 1,
        companies_count: int = 1,
        company_enabled: bool = True,
        ats_type: str = "greenhouse",
        ats_board_token: str | None = "acme",
        career_url: str | None = None,
        policy_status: str = "permitted",
    ) -> None:
        now = format_utc(datetime(2026, 11, 1, tzinfo=UTC))
        with engine.begin() as connection:
            for index in range(roles):
                connection.execute(
                    target_roles.insert().values(
                        role_id=f"role_{index + 1}",
                        canonical_name=f"Backend Engineer {index + 1}",
                        aliases_json=json.dumps(["Backend Engineer"]),
                        excluded_terms_json=json.dumps([]),
                        seniority_json=None,
                        enabled=True,
                        created_at_utc=now,
                        updated_at_utc=now,
                    )
                )
            for index in range(companies_count):
                connection.execute(
                    companies.insert().values(
                        company_id=f"company_{index + 1}",
                        company_name=f"Example Company {index + 1}",
                        company_website="https://example.test",
                        career_url=career_url,
                        ats_type=ats_type,
                        ats_board_token=(
                            ats_board_token
                            if ats_type in {"greenhouse", "lever", "ashby"}
                            else None
                        ),
                        enabled=company_enabled,
                        policy_status=policy_status,
                        access_notes=None,
                        created_at_utc=now,
                        updated_at_utc=now,
                    )
                )

    return _seed


@pytest.fixture
def clock_factory() -> Callable[[datetime], Any]:
    def _make(initial: datetime):
        class MutableClock:
            def __init__(self, now: datetime) -> None:
                self.now = now

            def __call__(self) -> datetime:
                return self.now

        return MutableClock(initial)

    return _make
