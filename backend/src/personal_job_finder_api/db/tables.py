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

LOCATION_STATUS_VALUES = ("eligible", "review", "excluded")
FEED_BUCKET_VALUES = ("main", "review", "excluded")
OUTBOX_STATUS_VALUES = (
    "pending",
    "in_progress",
    "retry_wait",
    "blocked",
    "synced",
)

_location_sql = ", ".join(f"'{value}'" for value in LOCATION_STATUS_VALUES)
_bucket_sql = ", ".join(f"'{value}'" for value in FEED_BUCKET_VALUES)
_outbox_sql = ", ".join(f"'{value}'" for value in OUTBOX_STATUS_VALUES)

# Staging table. Google Sheets stays the user-facing record store; these rows are
# the local staging projection that waits for a sync and feeds the dashboard.
opportunities = sa.Table(
    "opportunities",
    metadata,
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
        f"location_status IN ({_location_sql})",
        name="ck_opportunities_location_status",
    ),
    sa.CheckConstraint(
        f"feed_bucket IN ({_bucket_sql})",
        name="ck_opportunities_feed_bucket",
    ),
)

job_sources = sa.Table(
    "job_sources",
    metadata,
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

opportunity_run_refs = sa.Table(
    "opportunity_run_refs",
    metadata,
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

sync_outbox = sa.Table(
    "sync_outbox",
    metadata,
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
        f"status IN ({_outbox_sql})",
        name="ck_sync_outbox_status",
    ),
    sa.UniqueConstraint(
        "destination",
        "entity_type",
        "entity_id",
        name="uq_sync_outbox_projection",
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
sa.Index(
    "ix_opportunities_feed_bucket",
    opportunities.c.feed_bucket,
    opportunities.c.last_seen_at_utc,
)
sa.Index("ix_opportunities_company_id", opportunities.c.company_id)
sa.Index("ix_job_sources_opportunity_id", job_sources.c.opportunity_id)
sa.Index("ix_opportunity_run_refs_run_id", opportunity_run_refs.c.run_id)
sa.Index(
    "ix_sync_outbox_status",
    sync_outbox.c.status,
    sync_outbox.c.next_attempt_at_utc,
)
