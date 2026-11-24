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
    # Candidate fit is cached on the staging row, which a Sheets refresh never
    # rewrites (ADR-030). A NULL score means "not evaluated yet", not "poor fit".
    sa.Column("candidate_fit_score", sa.Integer, nullable=True),
    sa.Column("candidate_fit_summary", sa.Text, nullable=True),
    sa.Column("candidate_fit_evaluated_at", sa.String(32), nullable=True),
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

# ---------------------------------------------------------------------------
# Read model
#
# These tables mirror the canonical Google Sheets tabs and are a rebuildable
# cache, never a second editable master (ADR-015). Columns therefore follow the
# accepted sheet headers exactly, and values are stored as text because Sheets
# returns text. A full refresh replaces the whole snapshot inside one
# transaction, so a failed or partial refresh leaves the last known good
# snapshot untouched.
# ---------------------------------------------------------------------------

WORK_MODE_VALUES = ("onsite", "hybrid", "remote", "unknown")
ROLE_MATCH_TYPE_VALUES = (
    "canonical_title",
    "approved_alias",
    "description_keyword",
    "needs_review",
)
REMOTE_SCOPE_VALUES = (
    "indonesia_only",
    "worldwide",
    "country_restricted",
    "unknown",
    "not_applicable",
)
AVAILABILITY_STATUS_VALUES = ("open", "closed_confirmed", "unknown")
USER_STATUS_VALUES = ("new", "saved", "ignored")
SOURCE_TYPE_VALUES = (
    "greenhouse",
    "lever",
    "ashby",
    "permitted_career_page",
    "linkedin_post_manual",
)
PROVENANCE_METHOD_VALUES = ("ats_api", "permitted_page", "manual_user_input")
OBSERVATION_STATUS_VALUES = ("seen", "source_unavailable", "unknown")
REFRESH_OUTCOME_VALUES = ("succeeded", "failed")
READ_MODEL_STATE_ID = "google_sheets"


google_sheets_settings = sa.Table(
    "google_sheets_settings",
    metadata,
    sa.Column("settings_id", sa.Integer, primary_key=True),
    sa.Column("spreadsheet_id", sa.String(200), nullable=False),
    sa.Column("workspace_ready_at_utc", sa.String(32), nullable=True),
    sa.Column("configured_via", sa.String(16), nullable=True),
    sa.Column("updated_at_utc", sa.String(32), nullable=False),
    sa.CheckConstraint("settings_id = 1", name="ck_google_sheets_settings_singleton"),
)


AI_PROVIDER_VALUES = ("custom", "groq", "openrouter", "deepseek", "gemini", "ollama")
DEFAULT_AI_PROVIDER = "custom"
DEFAULT_AI_MODEL = "llama-3.3-70b-versatile"

_ai_provider_sql = ", ".join(f"'{value}'" for value in AI_PROVIDER_VALUES)

# Bring-your-own-key AI provider configuration (ADR-030). A single row holds the
# active endpoint; ``api_key`` is the user's own secret and never leaves the
# local machine except to the provider the user chose.
ai_settings = sa.Table(
    "ai_settings",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "provider",
        sa.String(32),
        nullable=False,
        server_default=sa.text(f"'{DEFAULT_AI_PROVIDER}'"),
    ),
    sa.Column("base_url", sa.Text, nullable=False),
    sa.Column("api_key", sa.Text, nullable=True),
    sa.Column(
        "model",
        sa.String(200),
        nullable=False,
        server_default=sa.text(f"'{DEFAULT_AI_MODEL}'"),
    ),
    sa.Column("is_configured", sa.Boolean, nullable=False, server_default=sa.text("0")),
    sa.Column("updated_at_utc", sa.String(32), nullable=False),
    sa.CheckConstraint("id = 1", name="ck_ai_settings_singleton"),
    sa.CheckConstraint(
        f"provider IN ({_ai_provider_sql})",
        name="ck_ai_settings_provider",
    ),
)


# The active master CV. The raw extracted text is kept so a re-parse never needs
# the original upload, and so the user can see exactly what the AI was given.
candidate_profiles = sa.Table(
    "candidate_profiles",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("raw_text", sa.Text, nullable=False),
    sa.Column("profile_json", sa.Text, nullable=False),
    sa.Column("file_name", sa.Text, nullable=True),
    sa.Column("updated_at_utc", sa.String(32), nullable=False),
    sa.CheckConstraint("id = 1", name="ck_candidate_profiles_singleton"),
)


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    """Build a NULL-tolerant CHECK so an absent value stays absent."""

    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IS NULL OR {column} IN ({allowed})"


opportunities_read_model = sa.Table(
    "opportunities_read_model",
    metadata,
    sa.Column("opportunity_id", sa.String(40), primary_key=True),
    sa.Column("company_name", sa.Text, nullable=True),
    sa.Column("job_title_original", sa.Text, nullable=True),
    sa.Column("job_title_normalized", sa.Text, nullable=True),
    sa.Column("target_role_id", sa.String(40), nullable=True),
    sa.Column("role_match_type", sa.String(32), nullable=True),
    sa.Column("role_match_reason", sa.Text, nullable=True),
    sa.Column("location_raw", sa.Text, nullable=True),
    sa.Column("country", sa.String(100), nullable=True),
    sa.Column("province", sa.String(100), nullable=True),
    sa.Column("city", sa.String(100), nullable=True),
    sa.Column("location_status", sa.String(20), nullable=True),
    sa.Column("work_mode", sa.String(20), nullable=True),
    sa.Column("remote_scope", sa.String(24), nullable=True),
    sa.Column("employment_type", sa.String(50), nullable=True),
    sa.Column("experience_level", sa.String(50), nullable=True),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("required_skills_json", sa.Text, nullable=True),
    sa.Column("posted_at", sa.String(32), nullable=True),
    sa.Column("deadline", sa.String(32), nullable=True),
    sa.Column("availability_status", sa.String(20), nullable=True),
    # User-owned in the canonical sheet: a refresh must copy, never overwrite.
    sa.Column("user_status", sa.String(20), nullable=True),
    sa.Column("first_seen_at_utc", sa.String(32), nullable=True),
    sa.Column("last_seen_at_utc", sa.String(32), nullable=True),
    sa.Column("created_at_utc", sa.String(32), nullable=True),
    sa.Column("updated_at_utc", sa.String(32), nullable=True),
    # Application tracking is user-owned data. Values are free text (or text
    # dates) so the local cache never coerces or rejects a canonical value.
    sa.Column("job_type", sa.Text, nullable=True),
    sa.Column("date_applied", sa.Text, nullable=True),
    sa.Column("application_status", sa.Text, nullable=True),
    sa.Column("follow_up_date", sa.Text, nullable=True),
    sa.Column("interview_date", sa.Text, nullable=True),
    sa.Column("vacancy_url", sa.Text, nullable=True),
    sa.Column("notes", sa.Text, nullable=True),
    sa.CheckConstraint(
        _enum_check("role_match_type", ROLE_MATCH_TYPE_VALUES),
        name="ck_opportunities_read_model_role_match_type",
    ),
    sa.CheckConstraint(
        _enum_check("location_status", LOCATION_STATUS_VALUES),
        name="ck_opportunities_read_model_location_status",
    ),
    sa.CheckConstraint(
        _enum_check("work_mode", WORK_MODE_VALUES),
        name="ck_opportunities_read_model_work_mode",
    ),
    sa.CheckConstraint(
        _enum_check("remote_scope", REMOTE_SCOPE_VALUES),
        name="ck_opportunities_read_model_remote_scope",
    ),
    sa.CheckConstraint(
        _enum_check("availability_status", AVAILABILITY_STATUS_VALUES),
        name="ck_opportunities_read_model_availability_status",
    ),
    sa.CheckConstraint(
        _enum_check("user_status", USER_STATUS_VALUES),
        name="ck_opportunities_read_model_user_status",
    ),
)

job_sources_read_model = sa.Table(
    "job_sources_read_model",
    metadata,
    sa.Column("source_id", sa.String(40), primary_key=True),
    sa.Column(
        "opportunity_id",
        sa.String(40),
        sa.ForeignKey(
            "opportunities_read_model.opportunity_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    ),
    sa.Column("source_type", sa.String(32), nullable=True),
    sa.Column("source_url", sa.Text, nullable=True),
    sa.Column("external_source_id", sa.String(200), nullable=True),
    sa.Column("author_name", sa.Text, nullable=True),
    sa.Column("author_role", sa.Text, nullable=True),
    sa.Column("source_text_excerpt", sa.Text, nullable=True),
    sa.Column("posted_at", sa.String(32), nullable=True),
    sa.Column("captured_at_utc", sa.String(32), nullable=True),
    sa.Column("last_seen_at_utc", sa.String(32), nullable=True),
    sa.Column("provenance_method", sa.String(24), nullable=True),
    sa.Column("observation_status", sa.String(24), nullable=True),
    sa.CheckConstraint(
        _enum_check("source_type", SOURCE_TYPE_VALUES),
        name="ck_job_sources_read_model_source_type",
    ),
    sa.CheckConstraint(
        _enum_check("provenance_method", PROVENANCE_METHOD_VALUES),
        name="ck_job_sources_read_model_provenance_method",
    ),
    sa.CheckConstraint(
        _enum_check("observation_status", OBSERVATION_STATUS_VALUES),
        name="ck_job_sources_read_model_observation_status",
    ),
)

# Contacts is deferred from MVP. No read-model table, refresh dependency, API,
# or fixture may assume a Contacts tab exists (ADR-021).

# One row describing the last refresh. ``last_full_refresh_at_utc`` only moves
# forward on a successful publication, so a failed attempt cannot make stale data
# look fresh.
sheets_sync_state = sa.Table(
    "sheets_sync_state",
    metadata,
    sa.Column("state_id", sa.String(32), primary_key=True),
    sa.Column("last_full_refresh_at_utc", sa.String(32), nullable=True),
    sa.Column("last_attempt_at_utc", sa.String(32), nullable=True),
    sa.Column("last_refresh_outcome", sa.String(16), nullable=True),
    sa.Column("last_refresh_error_code", sa.String(64), nullable=True),
    sa.Column("spreadsheet_schema_version", sa.String(32), nullable=True),
    sa.Column("updated_at_utc", sa.String(32), nullable=False),
    sa.CheckConstraint(
        f"state_id = '{READ_MODEL_STATE_ID}'",
        name="ck_sheets_sync_state_singleton",
    ),
    sa.CheckConstraint(
        _enum_check("last_refresh_outcome", REFRESH_OUTCOME_VALUES),
        name="ck_sheets_sync_state_outcome",
    ),
)

sa.Index(
    "ix_opportunities_read_model_status_seen",
    opportunities_read_model.c.location_status,
    opportunities_read_model.c.last_seen_at_utc,
)
sa.Index(
    "ix_opportunities_read_model_user_status",
    opportunities_read_model.c.user_status,
)
sa.Index(
    "ix_opportunities_read_model_target_role",
    opportunities_read_model.c.target_role_id,
)
# Dashboard queries filter date windows on these two columns (P0/12).
sa.Index(
    "ix_opportunities_read_model_date_applied",
    opportunities_read_model.c.date_applied,
)
sa.Index(
    "ix_opportunities_read_model_interview_date",
    opportunities_read_model.c.interview_date,
)
sa.Index(
    "ix_job_sources_read_model_opportunity",
    job_sources_read_model.c.opportunity_id,
)
# Dedup contract (ADR-022): ``source_id`` is the only unique identity. The same
# (source_type, external_source_id) may occur on several canonical rows, so this
# is a non-unique lookup that callers must treat as potentially ambiguous.
sa.Index(
    "ix_job_sources_read_model_external_identity",
    job_sources_read_model.c.source_type,
    job_sources_read_model.c.external_source_id,
)
sa.Index(
    "ix_job_sources_read_model_source_url",
    job_sources_read_model.c.source_url,
)
