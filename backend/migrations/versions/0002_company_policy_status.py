"""Add explicit source-policy approval to companies.

Revision ID: 0002_company_policy_status
Revises: 0001_initial_schema
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_company_policy_status"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("companies", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "policy_status",
                sa.String(20),
                nullable=False,
                server_default=sa.text("'unreviewed'"),
            )
        )
        batch.create_check_constraint(
            "ck_companies_policy_status",
            "policy_status IN ('unreviewed', 'permitted', 'blocked')",
        )


def downgrade() -> None:
    with op.batch_alter_table("companies", recreate="always") as batch:
        batch.drop_constraint("ck_companies_policy_status", type_="check")
        batch.drop_column("policy_status")
