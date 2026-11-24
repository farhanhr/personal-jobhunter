"""Add the singleton master CV table (ADR-030).

Stores the extracted CV text and the parsed profile so the user can inspect and
edit it without uploading again. Additive: no existing table is rebuilt.

Revision ID: 0008_candidate_profiles
Revises: 0007_ai_settings
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_candidate_profiles"
down_revision: str | Sequence[str] | None = "0007_ai_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("profile_json", sa.Text, nullable=False),
        sa.Column("file_name", sa.Text, nullable=True),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_candidate_profiles_singleton"),
    )


def downgrade() -> None:
    op.drop_table("candidate_profiles")
