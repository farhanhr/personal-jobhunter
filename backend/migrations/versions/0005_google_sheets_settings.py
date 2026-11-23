"""Add the local Google Sheets settings row.

Stores the spreadsheet identifier chosen in the Settings UI. The row is additive:
it never drops or rewrites existing tables, and ``.env`` remains the other
configuration source (UI value wins; see ADR-028).

Revision ID: 0005_google_sheets_settings
Revises: 0004_read_model
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_google_sheets_settings"
down_revision: str | Sequence[str] | None = "0004_read_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_sheets_settings",
        sa.Column("settings_id", sa.Integer, primary_key=True),
        sa.Column("spreadsheet_id", sa.String(200), nullable=False),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint("settings_id = 1", name="ck_google_sheets_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("google_sheets_settings")
