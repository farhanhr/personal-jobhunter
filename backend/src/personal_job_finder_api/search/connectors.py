"""Stable connector boundary between the orchestrator and job sources.

The protocol and result types are defined here so source-specific modules can be
added without changing orchestration code. Connectors must be side-effect free
beyond reading source data and must never raise raw provider payloads across this
boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from personal_job_finder_api.domain.companies import is_valid_board_token
from personal_job_finder_api.domain.opportunities import OpportunityRecord


class SourceRunStatus(StrEnum):
    """Terminal result of one source within a search run."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceRunResult:
    """Normalized, persistence-safe summary of one connector execution.

    ``records`` keeps the normalized opportunities available for the current run.
    The orchestrator stages them durably before the run reports success; only the
    counters and the error code are persisted in the run summary.
    """

    source_id: str
    status: SourceRunStatus
    records_found: int = 0
    error_code: str | None = None
    records_relevant: int | None = None
    records_filtered: int | None = None
    records_invalid: int | None = None
    records: tuple[OpportunityRecord, ...] = field(default=(), repr=False)

    def to_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "source_id": self.source_id,
            "status": self.status.value,
            "records_found": self.records_found,
            "error_code": self.error_code,
        }
        if self.records_relevant is not None:
            summary["records_relevant"] = self.records_relevant
        if self.records_filtered is not None:
            summary["records_filtered"] = self.records_filtered
        if self.records_invalid is not None:
            summary["records_invalid"] = self.records_invalid
        return summary


class SourceConnector(Protocol):
    """One permitted, locally configured source."""

    source_id: str

    def run(self) -> SourceRunResult: ...


ConnectorFactory = Callable[[Mapping[str, Any]], Sequence[SourceConnector]]
"""Build connectors for a persisted run snapshot.

Factories are called during read-only eligibility checks, so they must be cheap,
side-effect free, and perform no network access.
"""


def default_connector_factory(snapshot: Mapping[str, Any]) -> Sequence[SourceConnector]:
    """Build the production connectors supported by the current milestone."""

    # Imported lazily so the source module can import this boundary module.
    from personal_job_finder_api.search.greenhouse import build_connectors_from_snapshot

    return build_connectors_from_snapshot(snapshot)


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
    """True when this company may legally be requested and is locally configured.

    This is a policy question ("may we request it?"), not a capability question
    ("do we have an implementation?"). Whether a connector exists for the source
    type is decided by the connector factory, so an unimplemented ATS type still
    counts here and simply yields no connector.
    """

    if company.get("enabled") is False:
        return False
    if company.get("policy_status") != "permitted":
        return False

    ats_type = company.get("ats_type")
    if ats_type in {"greenhouse", "lever", "ashby"}:
        return is_valid_board_token(company.get("ats_board_token"))
    if ats_type == "custom":
        return _has_text(company.get("career_url"))
    return False


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
