"""Candidate-fit vocabulary shared by the AI service, the feed, and the API.

``candidate_fit_score`` is an AI judgement about one specific job, so it is kept
strictly separate from the rule-based ``role_relevance_score`` used for discovery
(ADR-021). A cached score of ``None`` means "not evaluated", never "poor fit".
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

BEST_FIT_THRESHOLD = 90


class CandidateFitSummary(BaseModel):
    """The stored, user-visible result of one fit evaluation."""

    candidate_fit_score: int = Field(ge=0, le=100)
    fit_verdict: str
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    rationale: str


def is_best_fit(score: int | None) -> bool:
    """Best Fit is a presentation threshold, not a stored flag."""

    return score is not None and score >= BEST_FIT_THRESHOLD


def parse_candidate_fit_summary(raw: str | None) -> CandidateFitSummary | None:
    """Read the cached JSON, treating unreadable data as "no evaluation"."""

    if not raw:
        return None
    try:
        return CandidateFitSummary.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return None
