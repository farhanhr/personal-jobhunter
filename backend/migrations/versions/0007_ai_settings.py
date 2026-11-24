"""Add the singleton AI provider settings table (ADR-030).

The table stores the user's bring-your-own-key endpoint. It is additive: no
existing table is rebuilt, so upgrading an existing local database keeps every
row it already has.

Revision ID: 0007_ai_settings
Revises: 0006_google_sheets_workspace_state
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_ai_settings"
down_revision: str | Sequence[str] | None = "0006_google_sheets_workspace_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "provider",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'custom'"),
        ),
        sa.Column("base_url", sa.Text, nullable=False),
        sa.Column("api_key", sa.Text, nullable=True),
        sa.Column(
            "model",
            sa.String(200),
            nullable=False,
            server_default=sa.text("'llama-3.3-70b-versatile'"),
        ),
        sa.Column(
            "is_configured",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("updated_at_utc", sa.String(32), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ai_settings_singleton"),
        sa.CheckConstraint(
            "provider IN ('custom', 'groq', 'openrouter', 'deepseek', 'gemini', 'ollama')",
            name="ck_ai_settings_provider",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_settings")
