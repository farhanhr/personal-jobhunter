"""Tests for validating untrusted source URLs before they are rendered."""

from __future__ import annotations

import pytest

from personal_job_finder_api.domain.source_urls import validate_source_url


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/example/jobs/1",
        "https://job-boards.greenhouse.io/example/jobs/1?gh_jid=1",
        "https://careers.example.test/jobs/1",
    ],
)
def test_valid_https_urls_are_accepted(url: str) -> None:
    result = validate_source_url(url)

    assert result.is_safe is True
    assert result.safe_url == url
    assert result.original_url == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://boards.greenhouse.io/example/jobs/1",
        "ftp://example.test/jobs/1",
        "data:text/html;base64,PHNjcmlwdD4=",
        "not-a-url",
        "",
        None,
    ],
)
def test_unsafe_schemes_and_malformed_urls_are_rejected(url: str | None) -> None:
    result = validate_source_url(url)

    assert result.is_safe is False
    assert result.safe_url is None
    assert result.original_url == url


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@boards.greenhouse.io/jobs/1",
        "https://user@boards.greenhouse.io/jobs/1",
    ],
)
def test_credentials_in_the_url_are_rejected(url: str) -> None:
    result = validate_source_url(url)

    assert result.is_safe is False
    # The original value is preserved for provenance even when it is unsafe.
    assert result.original_url == url


@pytest.mark.parametrize(
    "url",
    [
        "https:///jobs/1",
        "https://",
        "https://boards greenhouse.io/jobs/1",
        "https://-example.test/jobs/1",
    ],
)
def test_malformed_hostnames_are_rejected(url: str) -> None:
    result = validate_source_url(url)

    assert result.is_safe is False
    assert result.safe_url is None


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8443/jobs/1",
        "https://10.0.0.5/jobs/1",
        "https://192.168.1.10/jobs/1",
    ],
)
def test_ip_literal_hosts_are_rejected(url: str) -> None:
    result = validate_source_url(url)

    assert result.is_safe is False
    assert result.safe_url is None


def test_unicode_homograph_host_is_rejected_by_default() -> None:
    # A non-ASCII host cannot be verified as the expected domain.
    result = validate_source_url("https://gооgle.com/jobs/1")

    assert result.is_safe is False


def test_control_characters_in_the_url_are_rejected() -> None:
    result = validate_source_url("https://example.test/jobs/1\nHost: evil.test")

    assert result.is_safe is False


def test_fragment_and_query_are_preserved_verbatim() -> None:
    url = "https://careers.example.test/jobs/1?gh_jid=1&ref=abc#apply"

    result = validate_source_url(url)

    assert result.is_safe is True
    assert result.safe_url == url
