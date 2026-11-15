"""Tests for the bounded HTTP transport used by job-source connectors."""

from __future__ import annotations

import io
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from personal_job_finder_api.search.http import (
    HttpTransportError,
    SameHostRedirectHandler,
    UrllibTransport,
)


class FakeRawResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._buffer = io.BytesIO(body)
        self.status = 200
        self.headers = headers

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> FakeRawResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_response_body_is_read_with_content_type_and_headers() -> None:
    captured: dict[str, object] = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeRawResponse(
            b'{"jobs":[]}',
            {"Content-Type": "application/json; charset=utf-8"},
        )

    response = UrllibTransport(opener=opener).get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        timeout_seconds=7.5,
        max_bytes=1024,
    )

    assert response.status_code == 200
    assert response.content_type == "application/json; charset=utf-8"
    assert response.body == b'{"jobs":[]}'
    assert captured["timeout"] == 7.5
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "User-agent" in headers or "User-Agent" in headers


def test_oversized_response_is_rejected_before_it_is_returned() -> None:
    def opener(_request, _timeout):
        return FakeRawResponse(b"x" * 200, {"Content-Type": "application/json"})

    with pytest.raises(HttpTransportError) as raised:
        UrllibTransport(opener=opener).get(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            timeout_seconds=5,
            max_bytes=100,
        )

    assert raised.value.code == "response_too_large"


def test_timeout_is_reported_with_a_stable_code() -> None:
    def opener(_request, _timeout):
        raise TimeoutError("slow")

    with pytest.raises(HttpTransportError) as raised:
        UrllibTransport(opener=opener).get(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            timeout_seconds=5,
            max_bytes=1024,
        )

    assert raised.value.code == "timeout"


def test_network_failure_is_reported_with_a_stable_code() -> None:
    def opener(_request, _timeout):
        raise URLError("name resolution failed")

    with pytest.raises(HttpTransportError) as raised:
        UrllibTransport(opener=opener).get(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            timeout_seconds=5,
            max_bytes=1024,
        )

    assert raised.value.code == "network_error"


def test_http_error_status_and_retry_after_are_preserved() -> None:
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["Retry-After"] = "30"

    def opener(_request, _timeout):
        raise HTTPError(
            url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=io.BytesIO(b"{}"),
        )

    response = UrllibTransport(opener=opener).get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        timeout_seconds=5,
        max_bytes=1024,
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_http_error_body_is_still_bounded() -> None:
    headers = Message()
    headers["Content-Type"] = "application/json"

    def opener(_request, _timeout):
        raise HTTPError(
            url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            code=500,
            msg="Server Error",
            hdrs=headers,
            fp=io.BytesIO(b"y" * 500),
        )

    with pytest.raises(HttpTransportError) as raised:
        UrllibTransport(opener=opener).get(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            timeout_seconds=5,
            max_bytes=100,
        )

    assert raised.value.code == "response_too_large"


def test_non_positive_size_limit_is_rejected() -> None:
    transport = UrllibTransport(opener=lambda _request, _timeout: None)

    with pytest.raises(ValueError):
        transport.get(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            timeout_seconds=5,
            max_bytes=0,
        )


def test_same_host_redirect_is_allowed() -> None:
    handler = SameHostRedirectHandler()
    request = Request("https://boards-api.greenhouse.io/v1/boards/acme/jobs")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/",
    )

    assert redirected is not None
    assert redirected.full_url == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/steal",
        "http://boards-api.greenhouse.io/v1/boards/acme/jobs",
        "https://boards-api.greenhouse.io.evil.test/v1/boards/acme/jobs",
        "https://127.0.0.1:8000/api/v1/health",
    ],
)
def test_cross_host_or_downgrade_redirects_are_blocked(target: str) -> None:
    handler = SameHostRedirectHandler()
    request = Request("https://boards-api.greenhouse.io/v1/boards/acme/jobs")

    with pytest.raises(HttpTransportError) as raised:
        handler.redirect_request(request, None, 302, "Found", {}, target)

    assert raised.value.code == "blocked_redirect"
