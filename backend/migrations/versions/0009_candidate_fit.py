"""Cache the AI candidate-fit evaluation on each staged opportunity (ADR-030).

The columns live on the staging projection rather than the Sheets read model so
a spreadsheet refresh cannot erase an evaluation the user paid tokens for. A
NULL score means "not evaluated yet"; a low score is a real result.

Revision ID: 0009_candidate_fit
Revises: 0008_candidate_profiles
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_candidate_fit"
down_revision: str | Sequence[str] | None = "0008_candidate_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("opportunities", recreate="always") as batch:
        batch.add_column(sa.Column("candidate_fit_score", sa.Integer, nullable=True))
        batch.add_column(sa.Column("candidate_fit_summary", sa.Text, nullable=True))
        batch.add_column(sa.Column("candidate_fit_evaluated_at", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("opportunities", recreate="always") as batch:
        batch.drop_column("candidate_fit_evaluated_at")
        batch.drop_column("candidate_fit_summary")
        batch.drop_column("candidate_fit_score")
