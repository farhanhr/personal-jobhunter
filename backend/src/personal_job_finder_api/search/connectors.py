"""Stable connector boundary between the orchestrator and job sources.

M2B ships no production connector. The protocol and result types are defined now so
M3 connectors can be added without changing orchestration code. Connectors must be
side-effect free beyond reading source data and must never raise raw provider
payloads across this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class SourceRunStatus(StrEnum):
    """Terminal result of one source within a search run."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceRunResult:
    """Normalized, persistence-safe summary of one connector execution."""

    source_id: str
    status: SourceRunStatus
    records_found: int = 0
    error_code: str | None = None

    def to_summary(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status.value,
            "records_found": self.records_found,
            "error_code": self.error_code,
        }


class SourceConnector(Protocol):
    """One permitted, locally configured source."""

    source_id: str

    def run(self) -> SourceRunResult: ...


ConnectorFactory = Callable[[Mapping[str, Any]], Sequence[SourceConnector]]
"""Build connectors for a persisted run snapshot.

Factories are called during read-only eligibility checks, so they must be cheap,
side-effect free, and perform no network access. M3 wires real factories here.
"""


def default_connector_factory(_snapshot: Mapping[str, Any]) -> Sequence[SourceConnector]:
    """M2B has no production connectors; M3 replaces this factory explicitly."""

    return ()


def connector_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return a snapshot whose companies are permitted and locally usable.

    The persisted configuration snapshot intentionally retains every enabled
    company for auditability. Connector factories must only ever see sources that
    may legally be requested, so filter at this boundary.
    """

    companies = snapshot.get("companies")
    usable = []
    if isinstance(companies, list):
        usable = [
            company
            for company in companies
            if isinstance(company, Mapping) and _is_usable_company(company)
        ]
    return {**snapshot, "companies": usable}


def _is_usable_company(company: Mapping[str, Any]) -> bool:
    if company.get("enabled") is False:
        return False
    if company.get("policy_status") != "permitted":
        return False

    ats_type = company.get("ats_type")
    if ats_type in {"greenhouse", "lever", "ashby"}:
        return _has_text(company.get("ats_board_token"))
    if ats_type == "custom":
        return _has_text(company.get("career_url"))
    return False


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
