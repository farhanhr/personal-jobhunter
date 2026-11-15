"""Unit tests for the Greenhouse job board connector.

Every test injects a fake transport, so no test can reach the network.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from typing import Any

import pytest

from personal_job_finder_api.domain.location import LocationStatus, WorkMode
from personal_job_finder_api.domain.opportunities import RoleMatchType, TargetRoleSpec
from personal_job_finder_api.search.connectors import SourceRunStatus
from personal_job_finder_api.search.greenhouse import (
    DEFAULT_MAX_BYTES,
    GREENHOUSE_API_ORIGIN,
    GreenhouseConnector,
    build_board_url,
)
from personal_job_finder_api.search.http import HttpResponse, HttpTransportError

FIXED_NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
BACKEND_ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Unit tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HttpResponse:
        self.calls.append({"url": url, "timeout_seconds": timeout_seconds, "max_bytes": max_bytes})
        if not self._responses:
            raise AssertionError("Unexpected extra HTTP request")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def json_response(payload: object, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(payload).encode("utf-8"),
        headers={},
    )


def job(
    job_id: object = 127817,
    *,
    title: object = "Backend Engineer",
    location: object = "Jakarta, Indonesia",
    content: object = "<p>Build services.</p>",
    updated_at: object = "2016-01-14T10:55:28-05:00",
    absolute_url: object = "https://boards.greenhouse.io/example/jobs/127817",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": job_id,
        "title": title,
        "location": {"name": location},
        "content": content,
        "updated_at": updated_at,
        "absolute_url": absolute_url,
        "internal_job_id": 144381,
        "language": "en",
        "metadata": None,
    }
    return payload


def payload(*jobs: dict[str, Any]) -> dict[str, Any]:
    return {"jobs": list(jobs), "meta": {"total": len(jobs)}}


def connector(
    transport: FakeTransport,
    *,
    board_token: str = "example-board",
    target_roles: tuple[TargetRoleSpec, ...] = (BACKEND_ROLE,),
    max_attempts: int = 2,
    sleeps: list[float] | None = None,
    max_bytes: int = 2_000_000,
    timeout_seconds: float = 10.0,
) -> GreenhouseConnector:
    return GreenhouseConnector(
        company_id="company_1",
        company_name="Example Company",
        board_token=board_token,
        target_roles=target_roles,
        transport=transport,
        clock=lambda: FIXED_NOW,
        sleep=(sleeps if sleeps is not None else []).append,
        max_attempts=max_attempts,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )


def test_board_url_uses_the_fixed_https_greenhouse_origin() -> None:
    url = build_board_url("example-board")

    assert url == (f"{GREENHOUSE_API_ORIGIN}/v1/boards/example-board/jobs?content=true")
    assert url.startswith("https://boards-api.greenhouse.io/")


@pytest.mark.parametrize(
    "board_token",
    ["../etc/passwd", "example/board", "example board", "", " ", ".", "..", "a%2Fb"],
)
def test_malformed_board_tokens_are_rejected_before_any_request(
    board_token: str,
) -> None:
    transport = FakeTransport([])

    with pytest.raises(ValueError):
        build_board_url(board_token)

    result = connector(transport, board_token=board_token).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "invalid_board_token"
    assert transport.calls == []


def test_valid_response_is_normalized_and_counted() -> None:
    transport = FakeTransport([json_response(payload(job()))])

    result = connector(transport).run()

    assert result.status is SourceRunStatus.COMPLETED
    assert result.error_code is None
    assert result.records_found == 1
    assert result.records_relevant == 1
    assert result.records_filtered == 0
    assert result.records_invalid == 0
    assert transport.calls == [
        {
            "url": f"{GREENHOUSE_API_ORIGIN}/v1/boards/example-board/jobs?content=true",
            "timeout_seconds": 10.0,
            "max_bytes": 2_000_000,
        }
    ]

    record = result.records[0]
    assert record.company_id == "company_1"
    assert record.company_name == "Example Company"
    assert record.job_title == "Backend Engineer"
    assert record.description == "<p>Build services.</p>"
    assert record.location.location_raw == "Jakarta, Indonesia"
    assert record.location.status is LocationStatus.ELIGIBLE
    assert record.location.city == "Jakarta"
    assert record.location.work_mode is WorkMode.ONSITE
    assert record.source.source_type == "greenhouse"
    assert record.source.external_source_id == "127817"
    assert record.source.source_url == "https://boards.greenhouse.io/example/jobs/127817"
    assert record.source.captured_at == FIXED_NOW
    assert "Greenhouse" in record.source.provenance_note
    assert record.posted_at is None
    assert record.source_updated_at == datetime(2016, 1, 14, 15, 55, 28, tzinfo=UTC)
    assert record.role_match is not None
    assert record.role_match.match_type is RoleMatchType.CANONICAL_TITLE


def test_empty_board_is_a_valid_zero_result_execution() -> None:
    transport = FakeTransport([json_response(payload())])

    result = connector(transport).run()

    assert result.status is SourceRunStatus.COMPLETED
    assert result.records_found == 0
    assert result.records_relevant == 0
    assert result.records_invalid == 0
    assert result.records == ()
    assert result.error_code is None


def test_malformed_json_is_reported_without_leaking_body() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=200,
                content_type="application/json",
                body=b"{not valid json",
                headers={},
            )
        ]
    )

    result = connector(transport).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "malformed_json"
    assert result.records_found == 0


@pytest.mark.parametrize(
    "body",
    [
        {"jobs": "not-a-list"},
        {"meta": {"total": 1}},
        {"jobs": [1, 2, 3]},
        [],
    ],
)
def test_unexpected_response_structure_is_reported(body: object) -> None:
    transport = FakeTransport([json_response(body)])

    result = connector(transport).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "unexpected_payload"


def test_unsupported_content_type_is_reported() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=200,
                content_type="text/html",
                body=b"<html></html>",
                headers={},
            )
        ]
    )

    result = connector(transport).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "unsupported_content_type"


def test_missing_optional_fields_are_preserved_as_unknown() -> None:
    entry = job()
    del entry["content"]
    del entry["location"]
    del entry["updated_at"]
    transport = FakeTransport([json_response(payload(entry))])

    result = connector(transport).run()

    assert result.status is SourceRunStatus.COMPLETED
    record = result.records[0]
    assert record.description is None
    assert record.location.location_raw is None
    assert record.location.status is LocationStatus.REVIEW
    assert record.source_updated_at is None
    assert record.posted_at is None


def test_first_published_is_mapped_to_posted_at_when_supplied() -> None:
    entry = job()
    entry["first_published"] = "2026-10-01T09:30:00Z"
    transport = FakeTransport([json_response(payload(entry))])

    result = connector(transport).run()

    assert result.records[0].posted_at == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    # The source update timestamp stays a separate value.
    assert result.records[0].source_updated_at == datetime(2016, 1, 14, 15, 55, 28, tzinfo=UTC)


def test_invalid_first_published_is_not_invented() -> None:
    entry = job()
    entry["first_published"] = "not-a-date"
    transport = FakeTransport([json_response(payload(entry))])

    result = connector(transport).run()

    assert result.records[0].posted_at is None


def test_default_response_ceiling_tolerates_large_boards() -> None:
    # A board fetched with content=true can be several megabytes of HTML.
    assert DEFAULT_MAX_BYTES >= 20_000_000


def test_missing_location_is_review_not_indonesia() -> None:
    transport = FakeTransport([json_response(payload(job(location=None)))])

    result = connector(transport).run()

    record = result.records[0]
    assert record.location.status is LocationStatus.REVIEW
    assert record.location.country is None
    assert result.records_relevant == 0
    assert result.records_filtered == 1


def test_indonesian_location_is_relevant() -> None:
    transport = FakeTransport([json_response(payload(job(location="Bandung")))])
    result = connector(transport).run()

    assert result.records[0].location.status is LocationStatus.ELIGIBLE
    assert result.records_relevant == 1


def test_foreign_location_is_filtered() -> None:
    transport = FakeTransport([json_response(payload(job(location="Kuala Lumpur, Malaysia")))])

    result = connector(transport).run()

    record = result.records[0]
    assert record.location.status is LocationStatus.EXCLUDED
    assert record.location.country == "Malaysia"
    assert result.records_relevant == 0
    assert result.records_filtered == 1


def test_ambiguous_location_is_filtered_for_review() -> None:
    transport = FakeTransport([json_response(payload(job(location="APAC")))])

    result = connector(transport).run()

    assert result.records[0].location.status is LocationStatus.REVIEW
    assert result.records_relevant == 0
    assert result.records_filtered == 1


def test_unrelated_role_is_filtered_but_still_counted() -> None:
    transport = FakeTransport(
        [json_response(payload(job(title="Accountant"), job(title="Backend Engineer")))]
    )

    result = connector(transport).run()

    assert result.records_found == 2
    assert result.records_relevant == 1
    assert result.records_filtered == 1


def test_entries_missing_required_fields_are_counted_as_invalid() -> None:
    missing_title = job()
    del missing_title["title"]
    missing_url = job()
    del missing_url["absolute_url"]
    transport = FakeTransport([json_response(payload(missing_title, missing_url, job()))])

    result = connector(transport).run()

    assert result.status is SourceRunStatus.COMPLETED
    assert result.records_found == 1
    assert result.records_invalid == 2


@pytest.mark.parametrize(
    "absolute_url",
    [
        "javascript:alert(1)",
        "http://boards.greenhouse.io/example/jobs/1",
        "https://user:pass@boards.greenhouse.io/jobs/1",
        "https://boards.greenhouse.io@evil.test/jobs/1",
        "not-a-url",
    ],
)
def test_unsafe_job_urls_are_invalid(absolute_url: str) -> None:
    transport = FakeTransport([json_response(payload(job(absolute_url=absolute_url)))])

    result = connector(transport).run()

    assert result.records_found == 0
    assert result.records_invalid == 1


@pytest.mark.parametrize(
    "absolute_url",
    [
        "https://boards.greenhouse.io/example/jobs/1",
        "https://job-boards.greenhouse.io/example/jobs/1",
        # Greenhouse documents that absolute_url may be the company's own career
        # page, so a non-greenhouse host is legitimate source data.
        "https://careers.example.test/jobs/1?gh_jid=1",
    ],
)
def test_https_job_urls_are_preserved_verbatim(absolute_url: str) -> None:
    transport = FakeTransport([json_response(payload(job(absolute_url=absolute_url)))])

    result = connector(transport).run()

    assert result.records_found == 1
    assert result.records[0].source.source_url == absolute_url


def test_http_403_stops_without_retry() -> None:
    transport = FakeTransport([json_response({}, status=403)])
    sleeps: list[float] = []

    result = connector(transport, sleeps=sleeps).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "http_403"
    assert len(transport.calls) == 1
    assert sleeps == []


def test_http_404_is_reported() -> None:
    transport = FakeTransport([json_response({}, status=404)])

    result = connector(transport).run()

    assert result.error_code == "http_404"


def test_other_4xx_is_reported() -> None:
    transport = FakeTransport([json_response({}, status=400)])

    result = connector(transport).run()

    assert result.error_code == "http_4xx"


def test_5xx_is_reported() -> None:
    transport = FakeTransport([json_response({}, status=503)])

    result = connector(transport).run()

    assert result.error_code == "http_5xx"


def test_429_is_retried_once_honouring_retry_after() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=429,
                content_type="application/json",
                body=b"{}",
                headers={"Retry-After": "2"},
            ),
            json_response(payload(job())),
        ]
    )
    sleeps: list[float] = []

    result = connector(transport, sleeps=sleeps).run()

    assert result.status is SourceRunStatus.COMPLETED
    assert result.records_found == 1
    assert len(transport.calls) == 2
    assert sleeps == [2.0]


def test_repeated_429_stops_after_the_bounded_attempt_budget() -> None:
    rate_limited = HttpResponse(
        status_code=429,
        content_type="application/json",
        body=b"{}",
        headers={"Retry-After": "1"},
    )
    transport = FakeTransport([rate_limited, rate_limited, rate_limited])
    sleeps: list[float] = []

    result = connector(transport, sleeps=sleeps).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == "http_429"
    assert len(transport.calls) == 2
    assert sleeps == [1.0]


def test_429_with_long_retry_after_is_not_retried_in_process() -> None:
    transport = FakeTransport(
        [
            HttpResponse(
                status_code=429,
                content_type="application/json",
                body=b"{}",
                headers={"Retry-After": "600"},
            )
        ]
    )
    sleeps: list[float] = []

    result = connector(transport, sleeps=sleeps).run()

    assert result.error_code == "http_429"
    assert len(transport.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("timeout", "timeout"),
        ("network_error", "network_error"),
        ("response_too_large", "response_too_large"),
        ("blocked_redirect", "blocked_redirect"),
        ("some_unexpected_code", "network_error"),
    ],
)
def test_transport_failures_are_mapped_to_stable_error_codes(
    code: str,
    expected: str,
) -> None:
    transport = FakeTransport([HttpTransportError(code, "transport failure")])

    result = connector(transport).run()

    assert result.status is SourceRunStatus.FAILED
    assert result.error_code == expected


def test_response_size_limit_is_passed_to_the_transport() -> None:
    transport = FakeTransport([json_response(payload())])

    connector(transport, max_bytes=1234).run()

    assert transport.calls[0]["max_bytes"] == 1234


def test_target_role_snapshot_controls_role_matching() -> None:
    transport = FakeTransport(
        [json_response(payload(job(title="Backend Developer"), job(title="Data Analyst")))]
    )

    result = connector(transport).run()

    assert result.records_found == 2
    assert result.records_relevant == 1
    assert result.records[0].role_match is not None
    assert result.records[0].role_match.match_type is RoleMatchType.APPROVED_ALIAS
    assert result.records[1].role_match is None


def test_connector_does_not_rewrite_the_original_title() -> None:
    transport = FakeTransport(
        [json_response(payload(job(title="Senior Backend Developer (Payments)")))]
    )

    result = connector(transport).run()

    assert result.records[0].job_title == "Senior Backend Developer (Payments)"


def test_html_entities_in_content_do_not_break_matching() -> None:
    transport = FakeTransport(
        [
            json_response(
                payload(
                    job(
                        title="Software Engineer",
                        content="&lt;p&gt;Work with our Backend Engineer team.&lt;/p&gt;",
                    )
                )
            )
        ]
    )

    result = connector(transport).run()

    match = result.records[0].role_match
    assert match is not None
    assert match.match_type is RoleMatchType.DESCRIPTION_KEYWORD


def test_summary_is_safe_to_persist() -> None:
    transport = FakeTransport([json_response(payload(job()))])

    summary = connector(transport).run().to_summary()

    assert summary["source_id"] == "company_1"
    assert summary["status"] == "completed"
    assert summary["records_found"] == 1
    assert summary["records_relevant"] == 1
    assert summary["records_filtered"] == 0
    assert summary["records_invalid"] == 0
    assert summary["error_code"] is None
    assert "records" not in summary


def test_provenance_note_identifies_the_public_job_board() -> None:
    transport = FakeTransport([json_response(payload(job()))])

    record = connector(transport).run().records[0]

    note = record.source.provenance_note.casefold()
    assert "greenhouse" in note
    assert "public" in note
