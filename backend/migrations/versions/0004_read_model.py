"""Add the SQLite read-model foundation.

Creates the projection of the canonical Google Sheets tabs plus the refresh state
row. The read model is a rebuildable cache (ADR-015), so these tables carry no
operational authority: quota, locks, outbox, and staging stay in the M4A/M4B
tables untouched by this revision.

Contacts is deferred from MVP (ADR-021), so no contacts table is created and a
missing Contacts tab cannot invalidate a refresh snapshot.

This revision was amended in place before publication. Verification performed
2026-11-16: no persistent application database existed (no default data directory,
no ``PJF_DATA_DIR`` override, no ``.db``/``.sqlite``/``.sqlite3`` file in the
repository), so no database could already carry the earlier revision shape. If a
local database is ever found at ``0004_read_model`` with the pre-correction shape,
treat this as published history and add a corrective revision instead of editing
this file.

Revision ID: 0004_read_model
Revises: 0003_opportunity_staging
Create Date: 2026-11-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_read_model"
down_revision: str | Sequence[str] | None = "0003_opportunity_staging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCATION_STATUS_SQL = "'eligible', 'review', 'excluded'"
ROLE_MATCH_TYPE_SQL = "'canonical_title', 'approved_alias', 'description_keyword', 'needs_review'"
WORK_MODE_SQL = "'onsite', 'hybrid', 'remote', 'unknown'"
REMOTE_SCOPE_SQL = (
    "'indonesia_only', 'worldwide', 'country_restricted', 'unknown', 'not_applicable'"
)
AVAILABILITY_SQL = "'open', 'closed_confirmed', 'unknown'"
USER_STATUS_SQL = "'new', 'saved', 'ignored'"
SOURCE_TYPE_SQL = "'greenhouse', 'lever', 'ashby', 'permitted_career_page', 'linkedin_post_manual'"
PROVENANCE_SQL = "'ats_api', 'permitted_page', 'manual_user_input'"
OBSERVATION_SQL = "'seen', 'source_unavailable', 'unknown'"
OUTCOME_SQL = "'succeeded', 'failed'"


def _nullable_enum(column: str, allowed_sql: str) -> str:
    """A CHECK that also permits NULL, so absent values stay absent."""

    return f"{column} IS NULL OR {column} IN ({allowed_sql})"


def upgrade() -> None:
    op.create_table(
        "opportunities_read_model",
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
        sa.Column("user_status", sa.String(20), nullable=True),
        sa.Column("first_seen_at_utc", sa.String(32), nullable=True),
        sa.Column("last_seen_at_utc", sa.String(32), nullable=True),
        sa.Column("created_at_utc", sa.String(32), nullable=True),
        sa.Column("updated_at_utc", sa.String(32), nullable=True),
        # User-owned application tracking. Free text on purpose: the canonical
        # sheet is authoritative and may contain wording outside a suggestion
        # list. Dates stay text so the raw canonical value is preserved.
        sa.Column("job_type", sa.Text, nullable=True),
        sa.Column("date_applied", sa.Text, nullable=True),
        sa.Column("application_status", sa.Text, nullable=True),
        sa.Column("follow_up_date", sa.Text, nullable=True),
        sa.Column("interview_date", sa.Text, nullable=True),
        sa.Column("vacancy_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.CheckConstraint(
            _nullable_enum("role_match_type", ROLE_MATCH_TYPE_SQL),
            name="ck_opportunities_read_model_role_match_type",
        ),
        sa.CheckConstraint(
            _nullable_enum("location_status", LOCATION_STATUS_SQL),
            name="ck_opportunities_read_model_location_status",
        ),
        sa.CheckConstraint(
            _nullable_enum("work_mode", WORK_MODE_SQL),
            name="ck_opportunities_read_model_work_mode",
        ),
        sa.CheckConstraint(
            _nullable_enum("remote_scope", REMOTE_SCOPE_SQL),
            name="ck_opportunities_read_model_remote_scope",
        ),
        sa.CheckConstraint(
            _nullable_enum("availability_status", AVAILABILITY_SQL),
            name="ck_opportunities_read_model_availability_status",
        ),
        sa.CheckConstraint(
            _nullable_enum("user_status", USER_STATUS_SQL),
            name="ck_opportunities_read_model_user_status",
        ),
    )
    op.create_index(
        "ix_opportunities_read_model_status_seen",
        "opportunities_read_model",
        ["location_status", "last_seen_at_utc"],
    )
    op.create_index(
        "ix_opportunities_read_model_user_status",
        "opportunities_read_model",
        ["user_status"],
    )
    op.create_index(
        "ix_opportunities_read_model_target_role",
        "opportunities_read_model",
        ["target_role_id"],
    )
    # Dashboard windows filter on these columns (P0/12).
    op.create_index(
        "ix_opportunities_read_model_date_applied",
        "opportunities_read_model",
        ["date_applied"],
    )
    op.create_index(
        "ix_opportunities_read_model_interview_date",
        "opportunities_read_model",
        ["interview_date"],
    )

    op.create_table(
        "job_sources_read_model",
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
            _nullable_enum("source_type", SOURCE_TYPE_SQL),
            name="ck_job_sources_read_model_source_type",
        ),
        sa.CheckConstraint(
            _nullable_enum("provenance_method", PROVENANCE_SQL),
            name="ck_job_sources_read_model_provenance_method",
        ),
        sa.CheckConstraint(
            _nullable_enum("observation_status", OBSERVATION_SQL),
            name="ck_job_sources_read_model_observation_status",
        ),
    )
    op.create_index(
        "ix_job_sources_read_model_opportunity",
        "job_sources_read_model",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_job_sources_read_model_external_identity",
        "job_sources_read_model",
        ["source_type", "external_source_id"],
    )
    op.create_index(
        "ix_job_sources_read_model_source_url",
        "job_sources_read_model",
        ["source_url"],
    )

    op.create_table(
        "sheets_sync_state",
        sa.Column("state_id", sa.String(32), primary_key=True),
        sa.Column("last_full_refresh_at_utc", sa.String(32), nullable=True),
        sa.Column("last_attempt_at_utc", sa.String(32), nullable=True),
        sa.Column("last_refresh_outcome", sa.String(16), nullable=True),
        sa.Column("last_refresh_error_code", sa.String(64), nullable=True),
        sa.Column("spreadsheet_schema_version", sa.String(32), nullable=True),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "state_id = 'google_sheets'",
            name="ck_sheets_sync_state_singleton",
        ),
        sa.CheckConstraint(
            _nullable_enum("last_refresh_outcome", OUTCOME_SQL),
            name="ck_sheets_sync_state_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("sheets_sync_state")
    op.drop_index(
        "ix_job_sources_read_model_source_url",
        table_name="job_sources_read_model",
    )
    op.drop_index(
        "ix_job_sources_read_model_external_identity",
        table_name="job_sources_read_model",
    )
    op.drop_index(
        "ix_job_sources_read_model_opportunity",
        table_name="job_sources_read_model",
    )
    op.drop_table("job_sources_read_model")
    op.drop_index(
        "ix_opportunities_read_model_interview_date",
        table_name="opportunities_read_model",
    )
    op.drop_index(
        "ix_opportunities_read_model_date_applied",
        table_name="opportunities_read_model",
    )
    op.drop_index(
        "ix_opportunities_read_model_target_role",
        table_name="opportunities_read_model",
    )
    op.drop_index(
        "ix_opportunities_read_model_user_status",
        table_name="opportunities_read_model",
    )
    op.drop_index(
        "ix_opportunities_read_model_status_seen",
        table_name="opportunities_read_model",
    )
    op.drop_table("opportunities_read_model")
