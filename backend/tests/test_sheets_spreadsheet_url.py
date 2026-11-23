"""Offline tests for spreadsheet URL parsing using synthetic identifiers."""

from __future__ import annotations

import pytest

from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.spreadsheet_url import (
    extract_spreadsheet_id,
)

SYNTHETIC_ID = "1SyntheticSheetId0123456789abcdefXYZ"


def test_full_url_yields_identifier() -> None:
    url = f"https://docs.google.com/spreadsheets/d/{SYNTHETIC_ID}/edit#gid=0"

    assert extract_spreadsheet_id(url) == SYNTHETIC_ID


def test_url_without_suffix_is_accepted() -> None:
    assert extract_spreadsheet_id(f"  https://docs.google.com/spreadsheets/d/{SYNTHETIC_ID}  ") == (
        SYNTHETIC_ID
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "http://docs.google.com/spreadsheets/d/" + SYNTHETIC_ID,
        "https://evil.test/spreadsheets/d/" + SYNTHETIC_ID,
        "https://docs.google.com.evil.test/spreadsheets/d/" + SYNTHETIC_ID,
        "https://user@docs.google.com/spreadsheets/d/" + SYNTHETIC_ID,
        "https://docs.google.com/spreadsheets/u/0/" + SYNTHETIC_ID,
        "https://docs.google.com/document/d/" + SYNTHETIC_ID,
        "https://docs.google.com/spreadsheets/d/short",
    ],
)
def test_malformed_or_foreign_urls_are_rejected_without_echo(url: str) -> None:
    with pytest.raises(GoogleSheetsError) as caught:
        extract_spreadsheet_id(url)

    assert caught.value.code == "spreadsheet_url_invalid"
    if url.strip():
        assert url.strip() not in str(caught.value)
    assert SYNTHETIC_ID not in str(caught.value)
