"""Track whether a spreadsheet has completed workspace setup.

The readiness timestamp is set only after the shared setup orchestration has
validated credentials, spreadsheet access, and the workspace schema. It is
deliberately not a live connectivity flag: a revoked key must leave already
synchronized local data readable.

Revision ID: 0006_google_sheets_workspace_state
Revises: 0005_google_sheets_settings
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_google_sheets_workspace_state"
down_revision: str | Sequence[str] | None = "0005_google_sheets_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("google_sheets_settings", recreate="always") as batch:
        batch.add_column(sa.Column("workspace_ready_at_utc", sa.String(32), nullable=True))
        batch.add_column(sa.Column("configured_via", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("google_sheets_settings", recreate="always") as batch:
        batch.drop_column("configured_via")
        batch.drop_column("workspace_ready_at_utc")
