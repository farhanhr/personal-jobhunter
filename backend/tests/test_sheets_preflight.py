"""Tests for read-only spreadsheet preflight and explicit schema initialization."""

from __future__ import annotations

from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    REQUIRED_TABS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.preflight import (
    SpreadsheetPreflightService,
)


class StatefulSheetsTransport:
    def __init__(
        self,
        *,
        titles: list[str] | None = None,
        headers: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.titles = titles or []
        self.headers = headers or {}
        self.calls: list[tuple[str, Any]] = []
        self.metadata_error: Exception | None = None

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.calls.append(("get_spreadsheet", spreadsheet_id))
        if self.metadata_error is not None:
            raise self.metadata_error
        return {"sheets": [{"properties": {"title": title}} for title in self.titles]}

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("batch_get_values", (spreadsheet_id, tuple(ranges))))
        value_ranges: list[dict[str, Any]] = []
        for value_range in ranges:
            tab = value_range.split("!", 1)[0]
            headers = self.headers.get(tab, ())
            value_ranges.append({"values": [list(headers)]} if headers else {})
        return {"valueRanges": value_ranges}

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        self.calls.append(("batch_update_values", (spreadsheet_id, data, value_input_option)))
        assert value_input_option == "RAW"
        for item in data:
            tab = str(item["range"]).split("!", 1)[0]
            values = item["values"][0]
            self.headers[tab] = tuple(str(value) for value in values)
        return {}

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append(("batch_update_spreadsheet", (spreadsheet_id, requests)))
        for request in requests:
            title = request["addSheet"]["properties"]["title"]
            self.titles.append(title)
            self.headers.setdefault(title, ())
        return {}


def ready_transport() -> StatefulSheetsTransport:
    return StatefulSheetsTransport(
        titles=list(REQUIRED_TABS),
        headers={tab: headers for tab, headers in REQUIRED_TABS.items()},
    )


def update_calls(transport: StatefulSheetsTransport) -> list[tuple[str, Any]]:
    return [
        call
        for call in transport.calls
        if call[0] != "get_spreadsheet" and call[0] != "batch_get_values"
    ]


def test_required_schema_starts_with_stable_identity_columns() -> None:
    assert REQUIRED_TABS["Opportunities"] == OPPORTUNITY_HEADERS
    assert REQUIRED_TABS["Job_Sources"] == JOB_SOURCE_HEADERS
    assert OPPORTUNITY_HEADERS[0] == "opportunity_id"
    assert JOB_SOURCE_HEADERS[0] == "source_id"


def test_ready_spreadsheet_has_all_tabs_and_headers() -> None:
    transport = ready_transport()

    result = SpreadsheetPreflightService(transport).check("spreadsheet")

    assert result.is_ready
    assert result.missing_tabs == ()
    assert result.schema_errors == ()
    assert result.tab("Opportunities").header_matches
    assert update_calls(transport) == []


def test_missing_tab_is_reported_without_writing() -> None:
    transport = StatefulSheetsTransport(
        titles=["Opportunities"],
        headers={"Opportunities": OPPORTUNITY_HEADERS},
    )

    result = SpreadsheetPreflightService(transport).check("spreadsheet")

    assert not result.is_ready
    assert result.missing_tabs == ("Job_Sources",)
    assert result.tab("Job_Sources").exists is False
    assert update_calls(transport) == []


def test_header_mismatch_is_reported_without_overwriting_data() -> None:
    broken_headers = ("job_title", "opportunity_id")
    transport = StatefulSheetsTransport(
        titles=list(REQUIRED_TABS),
        headers={
            "Opportunities": broken_headers,
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
    )

    result = SpreadsheetPreflightService(transport).check("spreadsheet")

    assert not result.is_ready
    assert result.schema_errors == ("Opportunities",)
    opportunities = result.tab("Opportunities")
    assert opportunities.exists
    assert opportunities.header_matches is False
    assert opportunities.empty_header_row is False
    assert "opportunity_id" in opportunities.missing_headers
    assert update_calls(transport) == []
    assert transport.headers["Opportunities"] == broken_headers


def test_extra_trailing_columns_do_not_break_a_matching_prefix() -> None:
    transport = StatefulSheetsTransport(
        titles=list(REQUIRED_TABS),
        headers={
            "Opportunities": OPPORTUNITY_HEADERS + ("user_note",),
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
    )

    result = SpreadsheetPreflightService(transport).check("spreadsheet")

    assert result.is_ready
    assert result.tab("Opportunities").unexpected_headers == ("user_note",)


def test_empty_header_row_is_safe_to_initialize_later() -> None:
    transport = StatefulSheetsTransport(
        titles=list(REQUIRED_TABS),
        headers={"Opportunities": (), "Job_Sources": JOB_SOURCE_HEADERS},
    )

    result = SpreadsheetPreflightService(transport).check("spreadsheet")

    assert not result.is_ready
    assert result.tab("Opportunities").empty_header_row is True
    assert result.schema_errors == ()
    assert update_calls(transport) == []


def test_initialize_creates_missing_tabs_and_writes_raw_headers() -> None:
    transport = StatefulSheetsTransport(
        titles=["Opportunities"],
        headers={"Opportunities": OPPORTUNITY_HEADERS},
    )

    result = SpreadsheetPreflightService(transport).initialize("spreadsheet")

    assert result.is_ready
    assert transport.headers["Job_Sources"] == JOB_SOURCE_HEADERS
    add_tab_calls = [
        index for index, call in enumerate(transport.calls) if call[0] == "batch_update_spreadsheet"
    ]
    assert len(add_tab_calls) == 1
    assert transport.calls[add_tab_calls[0]] == (
        "batch_update_spreadsheet",
        (
            "spreadsheet",
            [{"addSheet": {"properties": {"title": "Job_Sources"}}}],
        ),
    )
    header_write_calls = [call for call in transport.calls if call[0] == "batch_update_values"]
    assert header_write_calls
    assert header_write_calls[0][1][2] == "RAW"


def test_initialize_fills_an_empty_existing_header_without_creating_a_tab() -> None:
    transport = StatefulSheetsTransport(
        titles=list(REQUIRED_TABS),
        headers={"Opportunities": (), "Job_Sources": JOB_SOURCE_HEADERS},
    )

    result = SpreadsheetPreflightService(transport).initialize("spreadsheet")

    assert result.is_ready
    assert transport.headers["Opportunities"] == OPPORTUNITY_HEADERS
    assert not any(call[0] == "batch_update_spreadsheet" for call in transport.calls)


def test_initialize_refuses_to_overwrite_an_incompatible_nonempty_header() -> None:
    broken_headers = ("job_title", "opportunity_id")
    transport = StatefulSheetsTransport(
        titles=list(REQUIRED_TABS),
        headers={
            "Opportunities": broken_headers,
            "Job_Sources": JOB_SOURCE_HEADERS,
        },
    )

    with pytest.raises(GoogleSheetsError) as captured:
        SpreadsheetPreflightService(transport).initialize("spreadsheet")

    assert captured.value.code == "schema_mismatch"
    assert "Opportunities" in str(captured.value)
    assert update_calls(transport) == []
    assert transport.headers["Opportunities"] == broken_headers


def test_access_errors_propagate_as_sanitized_errors() -> None:
    transport = ready_transport()
    transport.metadata_error = GoogleSheetsError(
        "spreadsheet_not_found",
        "The configured spreadsheet could not be found.",
    )

    with pytest.raises(GoogleSheetsError) as captured:
        SpreadsheetPreflightService(transport).check("spreadsheet")

    assert captured.value.code == "spreadsheet_not_found"


def test_preflight_result_does_not_expose_the_spreadsheet_id() -> None:
    result = SpreadsheetPreflightService(ready_transport()).check(
        "private_spreadsheet_id_1234567890"
    )

    assert "private_spreadsheet_id_1234567890" not in repr(result)
