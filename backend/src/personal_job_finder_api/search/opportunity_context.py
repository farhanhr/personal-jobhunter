"""Minimal opportunity context shared by the AI features.

Fit analysis and tailored-CV generation both need the same three fields. Loading
them in one place keeps the "not found" and "no description" rules identical.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import opportunities


@dataclass(frozen=True, slots=True)
class OpportunityContext:
    opportunity_id: str
    job_title: str
    company_name: str
    description: str | None


def load_opportunity_context(
    engine: Engine,
    opportunity_id: str,
) -> OpportunityContext | None:
    """Return the staged job's title, company, and description, or ``None``."""

    with engine.connect() as connection:
        row = connection.execute(
            sa.select(
                opportunities.c.job_title,
                opportunities.c.company_name,
                opportunities.c.description,
            ).where(opportunities.c.opportunity_id == opportunity_id)
        ).first()
    if row is None:
        return None
    return OpportunityContext(
        opportunity_id=opportunity_id,
        job_title=str(row[0]),
        company_name=str(row[1]),
        description=None if row[2] is None else str(row[2]),
    )
