"""SQLite table definitions using SQLAlchemy Core.

The schema is intentionally explicit: quota and locking behaviour must remain
visible rather than hidden behind ORM models.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

RUN_STATUS_VALUES = (
    "accepted",
    "running",
    "completed",
    "completed_with_warnings",
    "completed_pending_sync",
    "failed",
    "interrupted",
)
ACTIVE_STATUS_VALUES = ("accepted", "running")
COMPANY_POLICY_STATUSES = ("unreviewed", "permitted", "blocked")

_status_sql = ", ".join(f"'{value}'" for value in RUN_STATUS_VALUES)
_policy_status_sql = ", ".join(f"'{value}'" for value in COMPANY_POLICY_STATUSES)

target_roles = sa.Table(
    "target_roles",
    metadata,
    sa.Column("role_id", sa.String(40), primary_key=True),
    sa.Column("canonical_name", sa.String(200), nullable=False),
    sa.Column("aliases_json", sa.Text, nullable=False, server_default=sa.text("'[]'")),
    sa.Column(
        "excluded_terms_json",
        sa.Text,
        nullable=False,
        server_default=sa.text("'[]'"),
    ),
    sa.Column("seniority_json", sa.Text, nullable=True),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at_utc", sa.String(32), nullable=False),
    sa.Column("updated_at_utc", sa.String(32), nullable=False),
    sa.CheckConstraint("enabled IN (0, 1)", name="ck_target_roles_enabled"),
    sa.UniqueConstraint("canonical_name", name="uq_target_roles_canonical_name"),
)

companies = sa.Table(
    "companies",
    metadata,
    sa.Column("company_id", sa.String(40), primary_key=True),
    sa.Column("company_name", sa.String(200), nullable=False),
    sa.Column("company_website", sa.String(500), nullable=True),
    sa.Column("career_url", sa.String(1000), nullable=True),
    sa.Column("ats_type", sa.String(20), nullable=False),
    sa.Column("ats_board_token", sa.String(200), nullable=True),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
    sa.Column(
        "policy_status",
        sa.String(20),
        nullable=False,
        server_default=sa.text("'unreviewed'"),
    ),
    sa.Column("access_notes", sa.Text, nullable=True),
    sa.Column("created_at_utc", sa.String(32), nullable=False),
    sa.Column("updated_at_utc", sa.String(32), nullable=False),
    sa.CheckConstraint(
        "ats_type IN ('greenhouse', 'lever', 'ashby', 'custom', 'none')",
        name="ck_companies_ats_type",
    ),
    sa.CheckConstraint("enabled IN (0, 1)", name="ck_companies_enabled"),
    sa.CheckConstraint(
        f"policy_status IN ({_policy_status_sql})",
        name="ck_companies_policy_status",
    ),
)

search_runs = sa.Table(
    "search_runs",
    metadata,
    sa.Column("run_id", sa.String(40), primary_key=True),
    sa.Column("idempotency_key", sa.String(36), nullable=False),
    sa.Column("request_fingerprint", sa.String(64), nullable=False),
    sa.Column("operational_day", sa.String(10), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("accepted_at_utc", sa.String(32), nullable=False),
    sa.Column("started_at_utc", sa.String(32), nullable=True),
    sa.Column("finished_at_utc", sa.String(32), nullable=True),
    sa.Column("config_snapshot_json", sa.Text, nullable=False),
    sa.Column("error_summary", sa.Text, nullable=True),
    sa.Column("progress_json", sa.Text, nullable=True),
    sa.Column("source_summary_json", sa.Text, nullable=True),
    sa.CheckConstraint(
        f"status IN ({_status_sql})",
        name="ck_search_runs_status",
    ),
    sa.UniqueConstraint(
        "idempotency_key",
        name="uq_search_runs_idempotency_key",
    ),
)

sa.Index("ix_target_roles_enabled", target_roles.c.enabled)
sa.Index("ix_companies_enabled", companies.c.enabled)
sa.Index("ix_companies_ats_type", companies.c.ats_type)
sa.Index("ix_search_runs_operational_day", search_runs.c.operational_day)
sa.Index("ix_search_runs_status", search_runs.c.status)
sa.Index("ix_search_runs_accepted_at_utc", search_runs.c.accepted_at_utc)
sa.Index(
    "ux_search_runs_single_active",
    sa.case((search_runs.c.status.in_(ACTIVE_STATUS_VALUES), 1), else_=None),
    unique=True,
)
