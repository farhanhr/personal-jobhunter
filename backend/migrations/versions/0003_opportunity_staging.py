"""Add durable opportunity staging, provenance, and sync outbox.

Revision ID: 0003_opportunity_staging
Revises: 0002_company_policy_status
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_opportunity_staging"
down_revision: str | Sequence[str] | None = "0002_company_policy_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("opportunity_id", sa.String(40), primary_key=True),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("job_title", sa.Text, nullable=False),
        sa.Column("target_role_id", sa.String(40), nullable=True),
        sa.Column("role_match_type", sa.String(32), nullable=True),
        sa.Column("role_match_reason", sa.Text, nullable=True),
        sa.Column("location_raw", sa.Text, nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("province", sa.String(100), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("location_status", sa.String(20), nullable=False),
        sa.Column("work_mode", sa.String(20), nullable=False),
        sa.Column("remote_scope", sa.String(24), nullable=False),
        sa.Column("location_reason", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("posted_at_utc", sa.String(32), nullable=True),
        sa.Column("source_updated_at_utc", sa.String(32), nullable=True),
        sa.Column("first_seen_at_utc", sa.String(32), nullable=False),
        sa.Column("last_seen_at_utc", sa.String(32), nullable=False),
        sa.Column("availability_status", sa.String(20), nullable=False),
        sa.Column("feed_bucket", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at_utc", sa.String(32), nullable=False),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "location_status IN ('eligible', 'review', 'excluded')",
            name="ck_opportunities_location_status",
        ),
        sa.CheckConstraint(
            "feed_bucket IN ('main', 'review', 'excluded')",
            name="ck_opportunities_feed_bucket",
        ),
    )
    op.create_index(
        "ix_opportunities_feed_bucket",
        "opportunities",
        ["feed_bucket", "last_seen_at_utc"],
    )
    op.create_index("ix_opportunities_company_id", "opportunities", ["company_id"])

    op.create_table(
        "job_sources",
        sa.Column("source_id", sa.String(40), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.String(40),
            sa.ForeignKey("opportunities.opportunity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("external_source_id", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("safe_source_url", sa.Text, nullable=True),
        sa.Column("captured_at_utc", sa.String(32), nullable=False),
        sa.Column("last_checked_at_utc", sa.String(32), nullable=False),
        sa.Column("provenance_note", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "source_type",
            "company_id",
            "external_source_id",
            name="uq_job_sources_identity",
        ),
    )
    op.create_index("ix_job_sources_opportunity_id", "job_sources", ["opportunity_id"])

    op.create_table(
        "opportunity_run_refs",
        sa.Column(
            "opportunity_id",
            sa.String(40),
            sa.ForeignKey("opportunities.opportunity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.String(40), primary_key=True),
        sa.Column("source_id", sa.String(40), primary_key=True),
        sa.Column("first_seen_at_utc", sa.String(32), nullable=False),
        sa.Column("last_seen_at_utc", sa.String(32), nullable=False),
    )
    op.create_index("ix_opportunity_run_refs_run_id", "opportunity_run_refs", ["run_id"])

    op.create_table(
        "sync_outbox",
        sa.Column("outbox_id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), nullable=True),
        sa.Column("destination", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(40), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("next_attempt_at_utc", sa.String(32), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_at_utc", sa.String(32), nullable=True),
        sa.Column("created_at_utc", sa.String(32), nullable=False),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.Column("synced_at_utc", sa.String(32), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'retry_wait', 'blocked', 'synced')",
            name="ck_sync_outbox_status",
        ),
        sa.UniqueConstraint(
            "destination",
            "entity_type",
            "entity_id",
            name="uq_sync_outbox_projection",
        ),
    )
    op.create_index(
        "ix_sync_outbox_status",
        "sync_outbox",
        ["status", "next_attempt_at_utc"],
    )


def downgrade() -> None:
    op.drop_index("ix_sync_outbox_status", table_name="sync_outbox")
    op.drop_table("sync_outbox")
    op.drop_index("ix_opportunity_run_refs_run_id", table_name="opportunity_run_refs")
    op.drop_table("opportunity_run_refs")
    op.drop_index("ix_job_sources_opportunity_id", table_name="job_sources")
    op.drop_table("job_sources")
    op.drop_index("ix_opportunities_company_id", table_name="opportunities")
    op.drop_index("ix_opportunities_feed_bucket", table_name="opportunities")
    op.drop_table("opportunities")
