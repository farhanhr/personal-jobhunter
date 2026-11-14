"""Initial M1 search-policy schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = (
    "accepted",
    "running",
    "completed",
    "completed_with_warnings",
    "completed_pending_sync",
    "failed",
    "interrupted",
)
_status_sql = ", ".join(f"'{status}'" for status in RUN_STATUSES)


def upgrade() -> None:
    op.create_table(
        "target_roles",
        sa.Column("role_id", sa.String(40), primary_key=True),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "excluded_terms_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("seniority_json", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at_utc", sa.String(32), nullable=False),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_target_roles_enabled"),
        sa.UniqueConstraint("canonical_name", name="uq_target_roles_canonical_name"),
    )
    op.create_index("ix_target_roles_enabled", "target_roles", ["enabled"])

    op.create_table(
        "companies",
        sa.Column("company_id", sa.String(40), primary_key=True),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("company_website", sa.String(500), nullable=True),
        sa.Column("career_url", sa.String(1000), nullable=True),
        sa.Column("ats_type", sa.String(20), nullable=False),
        sa.Column("ats_board_token", sa.String(200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("access_notes", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.String(32), nullable=False),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "ats_type IN ('greenhouse', 'lever', 'ashby', 'custom', 'none')",
            name="ck_companies_ats_type",
        ),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_companies_enabled"),
    )
    op.create_index("ix_companies_enabled", "companies", ["enabled"])
    op.create_index("ix_companies_ats_type", "companies", ["ats_type"])

    op.create_table(
        "search_runs",
        sa.Column("run_id", sa.String(40), primary_key=True),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("operational_day", sa.String(10), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("accepted_at_utc", sa.String(32), nullable=False),
        sa.Column("started_at_utc", sa.String(32), nullable=True),
        sa.Column("finished_at_utc", sa.String(32), nullable=True),
        sa.Column("config_snapshot_json", sa.Text(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("progress_json", sa.Text(), nullable=True),
        sa.Column("source_summary_json", sa.Text(), nullable=True),
        sa.CheckConstraint(f"status IN ({_status_sql})", name="ck_search_runs_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_search_runs_idempotency_key"),
    )
    op.create_index("ix_search_runs_operational_day", "search_runs", ["operational_day"])
    op.create_index("ix_search_runs_status", "search_runs", ["status"])
    op.create_index("ix_search_runs_accepted_at_utc", "search_runs", ["accepted_at_utc"])
    op.execute(
        "CREATE UNIQUE INDEX ux_search_runs_single_active "
        "ON search_runs ((CASE WHEN status IN ('accepted', 'running') THEN 1 END))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_search_runs_single_active")
    op.drop_table("search_runs")
    op.drop_table("companies")
    op.drop_table("target_roles")
