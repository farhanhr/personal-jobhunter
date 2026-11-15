"""Greenhouse public job board connector.

Uses the documented, unauthenticated GET endpoint
``https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true``.

Source-specific policy
----------------------
* **Origin:** fixed at ``https://boards-api.greenhouse.io``. Only the validated
  board token is interpolated as a single path segment; callers cannot supply a
  base URL.
* **Authentication:** none. Only public published job posts are read.
* **Pagination:** none. This endpoint returns the whole board in one response and
  documents no cursor, so exactly one request is made per run.
* **Request limits:** one request per run, plus at most one retry after HTTP 429
  when ``Retry-After`` is short. Timeout 10 s, response ceiling 25 MB.
* **Cache:** none in this milestone. Every run re-reads the board so the user sees
  current postings; no conditional requests are sent yet.
* **Not implemented:** the application submission endpoint, browser automation,
  and any authenticated Greenhouse API.
* **Errors:** stable codes ``invalid_board_token``, ``unsupported_content_type``,
  ``malformed_json``, ``unexpected_payload``, ``http_403``, ``http_404``,
  ``http_429``, ``http_4xx``, ``http_5xx``, ``http_unexpected_status``,
  ``timeout``, ``network_error``, ``response_too_large``, ``blocked_redirect``.
  Provider bodies and exception text never cross this boundary.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from personal_job_finder_api.core.time import ensure_aware_utc, utc_now
from personal_job_finder_api.domain.companies import is_valid_board_token
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    is_main_feed,
    match_target_role,
)
from personal_job_finder_api.search.connectors import (
    SourceConnector,
    SourceRunResult,
    SourceRunStatus,
)
from personal_job_finder_api.search.http import (
    HttpResponse,
    HttpTransport,
    UrllibTransport,
    header_value,
)

GREENHOUSE_API_ORIGIN = "https://boards-api.greenhouse.io"
SOURCE_TYPE = "greenhouse"
PROVENANCE_NOTE = "Captured from the public Greenhouse job board API for this company."

DEFAULT_TIMEOUT_SECONDS = 10.0
# Boards are fetched with ``content=true`` so descriptions arrive in one response.
# Large boards can be several megabytes of HTML; the ceiling stays bounded so a
# hostile or broken response cannot exhaust memory.
DEFAULT_MAX_BYTES = 25_000_000
DEFAULT_MAX_ATTEMPTS = 2
MAX_IN_PROCESS_RETRY_AFTER_SECONDS = 5.0

_JSON_CONTENT_TYPE = "application/json"
_INVALID_JSON = object()


class InvalidBoardToken(ValueError):
    """Raised when a board token cannot be used as a URL path segment."""


def build_board_url(board_token: str) -> str:
    """Build the fixed-origin job list URL for one validated board token."""

    if not is_valid_board_token(board_token):
        raise InvalidBoardToken("board token must be a single path identifier")
    return f"{GREENHOUSE_API_ORIGIN}/v1/boards/{board_token}/jobs?content=true"


class GreenhouseConnector:
    """Fetch and normalize one company's published Greenhouse jobs."""

    def __init__(
        self,
        *,
        company_id: str,
        company_name: str,
        board_token: str,
        target_roles: tuple[TargetRoleSpec, ...] = (),
        transport: HttpTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        self.company_id = company_id
        self.company_name = company_name
        self.board_token = board_token
        self.target_roles = target_roles
        self._transport = transport or UrllibTransport()
        self._clock = clock
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._max_attempts = max_attempts

    @property
    def source_id(self) -> str:
        return self.company_id

    def run(self) -> SourceRunResult:
        try:
            url = build_board_url(self.board_token)
        except InvalidBoardToken:
            return self._failure("invalid_board_token")

        response, transport_error = self._fetch(url)
        if transport_error is not None:
            return self._failure(transport_error)
        if response is None:  # pragma: no cover - defensive contract check
            return self._failure("network_error")

        if not _is_json(response.content_type):
            return self._failure("unsupported_content_type")

        document = _decode_json(response.body)
        if document is _INVALID_JSON:
            return self._failure("malformed_json")
        if not isinstance(document, Mapping):
            return self._failure("unexpected_payload")

        entries = document.get("jobs")
        if not isinstance(entries, list):
            return self._failure("unexpected_payload")
        if entries and not any(isinstance(entry, Mapping) for entry in entries):
            # A populated list of scalars is a wrong response shape, not merely
            # a set of malformed individual records.
            return self._failure("unexpected_payload")

        records, invalid = self._normalize_entries(entries)
        relevant = sum(1 for record in records if is_main_feed(record))
        return SourceRunResult(
            source_id=self.company_id,
            status=SourceRunStatus.COMPLETED,
            records_found=len(records),
            error_code=None,
            records_relevant=relevant,
            records_filtered=len(records) - relevant,
            records_invalid=invalid,
            records=tuple(records),
        )

    def _fetch(self, url: str) -> tuple[HttpResponse | None, str | None]:
        attempt = 0
        while attempt < self._max_attempts:
            attempt += 1
            try:
                response = self._transport.get(
                    url,
                    timeout_seconds=self._timeout_seconds,
                    max_bytes=self._max_bytes,
                )
            except Exception as exc:  # transport contract exposes a stable code
                return None, _transport_error_code(exc)

            if response.status_code == 429:
                if attempt < self._max_attempts:
                    delay = _retry_after_seconds(response.headers)
                    if delay is not None and delay <= MAX_IN_PROCESS_RETRY_AFTER_SECONDS:
                        self._sleep(delay)
                        continue
                return None, "http_429"

            error_code = _status_error_code(response.status_code)
            if error_code is not None:
                return None, error_code
            return response, None

        return None, "http_429"

    def _normalize_entries(
        self,
        entries: list[Any],
    ) -> tuple[list[OpportunityRecord], int]:
        records: list[OpportunityRecord] = []
        invalid = 0
        for entry in entries:
            record = self._normalize_entry(entry)
            if record is None:
                invalid += 1
            else:
                records.append(record)
        return records, invalid

    def _normalize_entry(self, entry: Any) -> OpportunityRecord | None:
        if not isinstance(entry, Mapping):
            return None

        external_id = _external_id(entry.get("id"))
        if external_id is None:
            return None

        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            return None

        source_url = entry.get("absolute_url")
        if not isinstance(source_url, str) or not _is_https_url(source_url):
            return None

        description = entry.get("content")
        if not isinstance(description, str):
            description = None

        location_raw = _location_name(entry.get("location"))
        location = classify_location(location_raw)
        role_match = match_target_role(
            title,
            unescape(description) if description is not None else None,
            self.target_roles,
        )

        return OpportunityRecord(
            company_id=self.company_id,
            company_name=self.company_name,
            job_title=title,
            location=location,
            description=description,
            posted_at=_parse_iso(entry.get("first_published")),
            source_updated_at=_parse_iso(entry.get("updated_at")),
            source=JobSourceRef(
                source_type=SOURCE_TYPE,
                source_url=source_url,
                external_source_id=external_id,
                captured_at=ensure_aware_utc(self._clock(), "clock"),
                provenance_note=PROVENANCE_NOTE,
            ),
            role_match=role_match,
        )

    def _failure(self, error_code: str) -> SourceRunResult:
        return SourceRunResult(
            source_id=self.company_id,
            status=SourceRunStatus.FAILED,
            records_found=0,
            error_code=error_code,
        )


def build_connectors_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    transport: HttpTransport | None = None,
) -> tuple[SourceConnector, ...]:
    """Build Greenhouse connectors for permitted companies in a run snapshot.

    The snapshot is the immutable configuration captured at acceptance time, so a
    source blocked after acceptance still runs with the configuration the user
    approved; the change applies to the next run. ``transport`` exists so tests can
    inject a fake HTTP layer without touching this production path.
    """

    target_roles = _target_roles(snapshot.get("target_roles"))
    companies = snapshot.get("companies")
    if not isinstance(companies, list):
        return ()

    connectors: list[SourceConnector] = []
    for company in companies:
        if not isinstance(company, Mapping):
            continue
        if company.get("ats_type") != SOURCE_TYPE:
            continue
        if company.get("enabled") is False:
            continue
        if company.get("policy_status") != "permitted":
            continue

        board_token = company.get("ats_board_token")
        if not is_valid_board_token(board_token):
            continue
        company_id = company.get("company_id")
        company_name = company.get("company_name")
        if not isinstance(company_id, str) or not isinstance(company_name, str):
            continue

        connectors.append(
            GreenhouseConnector(
                company_id=company_id,
                company_name=company_name,
                board_token=board_token,
                target_roles=target_roles,
                transport=transport,
            )
        )
    return tuple(connectors)


def _target_roles(raw_roles: Any) -> tuple[TargetRoleSpec, ...]:
    if not isinstance(raw_roles, list):
        return ()

    roles: list[TargetRoleSpec] = []
    for raw_role in raw_roles:
        if not isinstance(raw_role, Mapping):
            continue
        role_id = raw_role.get("role_id")
        canonical_name = raw_role.get("canonical_name")
        if not isinstance(role_id, str) or not isinstance(canonical_name, str):
            continue
        roles.append(
            TargetRoleSpec(
                role_id=role_id,
                canonical_name=canonical_name,
                aliases=_string_tuple(raw_role.get("aliases")),
                excluded_terms=_string_tuple(raw_role.get("excluded_terms")),
            )
        )
    return tuple(roles)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _transport_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    known_codes = {
        "timeout",
        "network_error",
        "response_too_large",
        "blocked_redirect",
    }
    if isinstance(code, str) and code in known_codes:
        return code
    # Unexpected transport bugs must not leak exception text or crash the run.
    return "network_error"


def _status_error_code(status_code: int) -> str | None:
    if 200 <= status_code < 300:
        return None
    if status_code == 403:
        return "http_403"
    if status_code == 404:
        return "http_404"
    if 400 <= status_code < 500:
        return "http_4xx"
    if 500 <= status_code < 600:
        return "http_5xx"
    return "http_unexpected_status"


def _is_json(content_type: str | None) -> bool:
    if content_type is None:
        return False
    media_type = content_type.split(";", 1)[0].strip().casefold()
    return media_type == _JSON_CONTENT_TYPE


def _decode_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _INVALID_JSON


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw_value = header_value(headers, "Retry-After")
    if raw_value is None:
        return None
    try:
        seconds = float(raw_value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return seconds


def _external_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and candidate.isdigit():
            return candidate
    return None


def _location_name(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None


def _is_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return ensure_aware_utc(parsed, "updated_at")
