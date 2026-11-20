"""Tests for the Google Sheets API adapter and bounded batch behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from personal_job_finder_api.integrations.google_sheets import client as client_module
from personal_job_finder_api.integrations.google_sheets.client import (
    GoogleApiSheetsTransport,
    GoogleSheetsClient,
)
from personal_job_finder_api.integrations.google_sheets.config import SHEETS_API_ORIGIN
from personal_job_finder_api.integrations.google_sheets.errors import (
    GoogleSheetsError,
    translate_http_status,
    translate_transport_exception,
)


class FakeTransport:
    def __init__(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        value_ranges: list[dict[str, Any]] | None = None,
    ) -> None:
        self.metadata = metadata or {}
        self.value_ranges = value_ranges or []
        self.calls: list[tuple[str, Any]] = []

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.calls.append(("get_spreadsheet", spreadsheet_id))
        return self.metadata

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("batch_get_values", (spreadsheet_id, tuple(ranges))))
        return {"valueRanges": self.value_ranges}

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        self.calls.append(("batch_update_values", (spreadsheet_id, data, value_input_option)))
        return {}

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append(("batch_update_spreadsheet", (spreadsheet_id, requests)))
        return {}


def test_metadata_lists_tab_titles() -> None:
    transport = FakeTransport(
        metadata={
            "sheets": [
                {"properties": {"title": "Opportunities"}},
                {"properties": {"title": "Job_Sources"}},
            ]
        }
    )

    metadata = GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert metadata.titles == ("Opportunities", "Job_Sources")
    assert transport.calls == [("get_spreadsheet", "spreadsheet")]


def test_malformed_metadata_is_a_sanitized_error() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(FakeTransport(metadata={"unexpected": []})).get_metadata("spreadsheet")

    assert captured.value.code == "malformed_response"


def test_header_rows_are_read_in_one_batch_and_trimmed() -> None:
    transport = FakeTransport(
        value_ranges=[
            {"values": [["opportunity_id", "company_name", ""]]},
            {"values": [["source_id", "opportunity_id"]]},
        ]
    )

    headers = GoogleSheetsClient(transport).get_header_rows(
        "spreadsheet",
        ["Opportunities", "Job_Sources"],
    )

    assert headers == {
        "Opportunities": ("opportunity_id", "company_name"),
        "Job_Sources": ("source_id", "opportunity_id"),
    }
    assert transport.calls == [
        (
            "batch_get_values",
            ("spreadsheet", ("Opportunities!1:1", "Job_Sources!1:1")),
        )
    ]


def test_missing_header_row_is_an_empty_tuple() -> None:
    transport = FakeTransport(value_ranges=[{}, {"values": []}])

    headers = GoogleSheetsClient(transport).get_header_rows(
        "spreadsheet",
        ["Opportunities", "Job_Sources"],
    )

    assert headers == {"Opportunities": (), "Job_Sources": ()}


def test_header_read_rejects_more_ranges_than_the_configured_bound() -> None:
    transport = FakeTransport()

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport, max_batch_ranges=1).get_header_rows(
            "spreadsheet",
            ["Opportunities", "Job_Sources"],
        )

    assert captured.value.code == "batch_too_large"
    assert transport.calls == []


def test_add_tabs_rejects_more_tabs_than_the_configured_bound() -> None:
    transport = FakeTransport()

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport, max_batch_ranges=1).add_tabs(
            "spreadsheet",
            ["Opportunities", "Job_Sources"],
        )

    assert captured.value.code == "batch_too_large"
    assert transport.calls == []


def test_header_read_rejects_a_malformed_value_range_count() -> None:
    transport = FakeTransport(value_ranges=[{"values": [["one"]]}])

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).get_header_rows(
            "spreadsheet",
            ["Opportunities", "Job_Sources"],
        )

    assert captured.value.code == "malformed_response"


def test_batch_update_always_uses_raw_input() -> None:
    transport = FakeTransport()
    data = [
        {
            "range": "Opportunities!A1:B1",
            "values": [["opportunity_id", "=not-a-formula"]],
        }
    ]

    GoogleSheetsClient(transport).batch_update_values("spreadsheet", data)

    assert transport.calls == [("batch_update_values", ("spreadsheet", data, "RAW"))]


def test_batch_update_rejects_empty_data() -> None:
    transport = FakeTransport()

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).batch_update_values("spreadsheet", [])

    assert captured.value.code == "invalid_request"
    assert transport.calls == []


def test_batch_update_rejects_more_ranges_than_the_configured_bound() -> None:
    transport = FakeTransport()
    data = [{"range": f"Opportunities!A{index}:A{index}", "values": [["x"]]} for index in range(3)]

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport, max_batch_ranges=2).batch_update_values(
            "spreadsheet",
            data,
        )

    assert captured.value.code == "batch_too_large"
    assert transport.calls == []


def test_add_tabs_uses_one_batch_update_request() -> None:
    transport = FakeTransport()

    GoogleSheetsClient(transport).add_tabs(
        "spreadsheet",
        ["Opportunities", "Job_Sources"],
    )

    assert transport.calls == [
        (
            "batch_update_spreadsheet",
            (
                "spreadsheet",
                [
                    {"addSheet": {"properties": {"title": "Opportunities"}}},
                    {"addSheet": {"properties": {"title": "Job_Sources"}}},
                ],
            ),
        )
    ]


def test_write_headers_uses_one_raw_batch_update() -> None:
    transport = FakeTransport()

    GoogleSheetsClient(transport).write_headers(
        "spreadsheet",
        {
            "Opportunities": ("opportunity_id", "company_name"),
            "Job_Sources": ("source_id",),
        },
    )

    assert transport.calls == [
        (
            "batch_update_values",
            (
                "spreadsheet",
                [
                    {
                        "range": "Opportunities!A1",
                        "values": [["opportunity_id", "company_name"]],
                    },
                    {"range": "Job_Sources!A1", "values": [["source_id"]]},
                ],
                "RAW",
            ),
        )
    ]


@pytest.mark.parametrize(
    ("status", "code", "reconnect_required"),
    [
        (401, "reconnect_required", True),
        (403, "permission_denied", True),
        (404, "spreadsheet_not_found", False),
        (400, "invalid_request", False),
        (409, "invalid_request", False),
        (422, "invalid_request", False),
        (429, "rate_limited", False),
        (500, "server_error", False),
        (503, "server_error", False),
    ],
)
def test_http_status_mapping(
    status: int,
    code: str,
    reconnect_required: bool,
) -> None:
    error = translate_http_status(status)

    assert error.code == code
    assert error.reconnect_required is reconnect_required


def test_timeout_and_connection_errors_are_distinct() -> None:
    assert translate_transport_exception(TimeoutError("slow")).code == "timeout"
    assert translate_transport_exception(ConnectionError("reset")).code == "network_error"


class FakeRequest:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def execute(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.result


class FakeValuesResource:
    def __init__(self) -> None:
        self.body: dict[str, Any] | None = None
        self.error: Exception | None = None

    def batchUpdate(
        self,
        *,
        spreadsheetId: str,
        body: dict[str, Any],
    ) -> FakeRequest:
        self.body = body
        return FakeRequest(result={}, error=self.error)


class FakeSpreadsheetsResource:
    def __init__(self, values: FakeValuesResource) -> None:
        self._values = values

    def values(self) -> FakeValuesResource:
        return self._values


class FakeService:
    def __init__(self, values: FakeValuesResource) -> None:
        self._spreadsheets = FakeSpreadsheetsResource(values)

    def spreadsheets(self) -> FakeSpreadsheetsResource:
        return self._spreadsheets


def test_transport_delegates_raw_body_to_the_official_client() -> None:
    values = FakeValuesResource()
    transport = GoogleApiSheetsTransport(FakeService(values))
    data = [{"range": "Opportunities!A1", "values": [["opportunity_id"]]}]

    transport.batch_update_values("spreadsheet", data, value_input_option="RAW")

    assert values.body == {"valueInputOption": "RAW", "data": data}


def test_transport_maps_http_errors_without_exposing_provider_body() -> None:
    values = FakeValuesResource()
    values.error = HttpError(
        SimpleNamespace(status=403, reason="Forbidden"),
        b'{"error":"private provider detail"}',
    )
    transport = GoogleApiSheetsTransport(FakeService(values))

    with pytest.raises(GoogleSheetsError) as captured:
        transport.batch_update_values(
            "spreadsheet",
            [{"range": "Opportunities!A1", "values": [["x"]]}],
            value_input_option="RAW",
        )

    assert captured.value.code == "permission_denied"
    assert "private provider detail" not in str(captured.value)


def test_transport_maps_timeouts() -> None:
    values = FakeValuesResource()
    values.error = TimeoutError("private transport detail")
    transport = GoogleApiSheetsTransport(FakeService(values))

    with pytest.raises(GoogleSheetsError) as captured:
        transport.batch_update_values(
            "spreadsheet",
            [{"range": "Opportunities!A1", "values": [["x"]]}],
            value_input_option="RAW",
        )

    assert captured.value.code == "timeout"
    assert "private transport detail" not in str(captured.value)


def test_service_builder_pins_the_official_origin_and_timeout(monkeypatch) -> None:
    from google_auth_httplib2 import AuthorizedHttp

    captured: dict[str, Any] = {}

    def fake_build(service_name: str, version: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        captured["service"] = (service_name, version)
        return object()

    monkeypatch.setattr(client_module, "build", fake_build)
    credentials = FakeUserCredentials()

    client_module.build_google_sheets_service(
        credentials=credentials,
        timeout_seconds=7.5,
    )

    assert captured["service"] == ("sheets", "v4")
    assert captured["client_options"] == {"api_endpoint": SHEETS_API_ORIGIN}
    assert captured["static_discovery"] is True
    assert captured["cache_discovery"] is False

    authorized = captured["http"]
    assert isinstance(authorized, AuthorizedHttp)
    assert authorized.credentials is credentials
    assert isinstance(authorized.http, httplib2.Http)
    assert authorized.http.timeout == 7.5
    assert "credentials" not in captured


def test_metadata_exposes_the_time_zone_and_sheet_ids() -> None:
    transport = FakeTransport(
        metadata={
            "properties": {"timeZone": "Asia/Jakarta"},
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Opportunities"}},
                {"properties": {"sheetId": 1730000123, "title": "Job_Sources"}},
            ],
        }
    )

    metadata = GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert metadata.titles == ("Opportunities", "Job_Sources")
    assert metadata.time_zone == "Asia/Jakarta"
    assert metadata.sheet_id_for("Job_Sources") == 1730000123
    assert metadata.sheet_id_for("Dashboard") is None


def test_metadata_without_optional_fields_keeps_preflight_working() -> None:
    transport = FakeTransport(metadata={"sheets": [{"properties": {"title": "Opportunities"}}]})

    metadata = GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert metadata.titles == ("Opportunities",)
    assert metadata.time_zone is None
    assert metadata.sheet_id_for("Opportunities") is None


def test_metadata_rejects_a_non_integer_sheet_id() -> None:
    transport = FakeTransport(
        metadata={"sheets": [{"properties": {"sheetId": "0", "title": "Opportunities"}}]}
    )

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert captured.value.code == "malformed_response"


def test_metadata_rejects_a_non_string_time_zone() -> None:
    transport = FakeTransport(
        metadata={
            "properties": {"timeZone": 7},
            "sheets": [{"properties": {"title": "Opportunities"}}],
        }
    )

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert captured.value.code == "malformed_response"


def test_batch_update_sends_structured_requests_in_one_call() -> None:
    transport = FakeTransport()
    requests = [{"updateCells": {"start": {"sheetId": 4}}}]

    GoogleSheetsClient(transport).batch_update("spreadsheet", requests)

    assert transport.calls == [
        ("batch_update_spreadsheet", ("spreadsheet", requests)),
    ]


def test_batch_update_rejects_empty_requests() -> None:
    transport = FakeTransport()

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).batch_update("spreadsheet", [])

    assert captured.value.code == "invalid_request"
    assert transport.calls == []


def test_batch_update_rejects_more_requests_than_the_configured_bound() -> None:
    transport = FakeTransport()
    client = GoogleSheetsClient(transport, max_batch_ranges=1)

    with pytest.raises(GoogleSheetsError) as captured:
        client.batch_update("spreadsheet", [{"a": 1}, {"b": 2}])

    assert captured.value.code == "batch_too_large"
    assert transport.calls == []


def test_structured_updates_never_touch_the_raw_values_path() -> None:
    transport = FakeTransport()
    client = GoogleSheetsClient(transport)

    client.batch_update("spreadsheet", [{"updateCells": {}}])

    assert [name for name, _ in transport.calls] == ["batch_update_spreadsheet"]


def test_formula_render_read_is_routed_to_the_render_transport() -> None:
    transport = FakeTransport(value_ranges=[{"values": [['=IF(1=2,"a","")']]}])
    transport.render_calls = []  # type: ignore[attr-defined]

    def batch_get_values_with_render(
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]:
        transport.render_calls.append(  # type: ignore[attr-defined]
            (spreadsheet_id, tuple(ranges), value_render_option)
        )
        return {"valueRanges": transport.value_ranges}

    transport.batch_get_values_with_render = batch_get_values_with_render  # type: ignore[attr-defined]

    values = GoogleSheetsClient(transport).get_values(
        "spreadsheet",
        ["Dashboard"],
        value_render_option="FORMULA",
    )

    assert values == {"Dashboard": [['=IF(1=2,"a","")']]}
    assert transport.render_calls == [("spreadsheet", ("Dashboard",), "FORMULA")]  # type: ignore[attr-defined]
    assert transport.calls == []


def test_the_default_read_never_uses_the_formula_render() -> None:
    transport = FakeTransport(value_ranges=[{"values": [["Applied"]]}])

    values = GoogleSheetsClient(transport).get_values("spreadsheet", ["Dashboard!A1"])

    assert values == {"Dashboard!A1": [["Applied"]]}
    assert transport.calls == [
        ("batch_get_values", ("spreadsheet", ("Dashboard!A1",))),
    ]


def test_transport_passes_the_render_option_to_the_official_client() -> None:
    values = FakeValuesResource()
    values.kwargs = {}
    transport = GoogleApiSheetsTransport(FakeService(values))

    def batchGet(**kwargs: Any) -> Any:
        values.kwargs = kwargs
        return FakeRequest(result={"valueRanges": []})

    values.batchGet = batchGet  # type: ignore[attr-defined]

    transport.batch_get_values_with_render(
        "spreadsheet",
        ["Dashboard"],
        value_render_option="FORMULA",
    )

    assert values.kwargs == {  # type: ignore[attr-defined]
        "spreadsheetId": "spreadsheet",
        "ranges": ["Dashboard"],
        "valueRenderOption": "FORMULA",
    }


# ------------------------------------- service construction: http vs credentials


class FakeUserCredentials:
    """A credentials double that can never reach the network."""

    def __init__(self) -> None:
        self.before_request_calls: list[object] = []
        self.expired = False
        self.refresh_token = "test-refresh-token"

    def before_request(self, request: object, method: str, url: str, headers: object) -> None:
        self.before_request_calls.append((request, method, url, headers))


def test_the_service_is_built_with_authorized_http() -> None:
    from google_auth_httplib2 import AuthorizedHttp

    credentials = FakeUserCredentials()

    service = client_module.build_google_sheets_service(
        credentials=credentials,
        timeout_seconds=7.5,
    )

    assert isinstance(service._http, AuthorizedHttp)


def test_authorized_http_wraps_the_configured_httplib2_client() -> None:
    from google_auth_httplib2 import AuthorizedHttp

    service = client_module.build_google_sheets_service(
        credentials=FakeUserCredentials(),
        timeout_seconds=7.5,
    )
    authorized = service._http

    assert isinstance(authorized, AuthorizedHttp)
    assert isinstance(authorized.http, httplib2.Http)
    assert not isinstance(authorized.http, AuthorizedHttp)


def test_the_underlying_http_timeout_is_preserved() -> None:
    service = client_module.build_google_sheets_service(
        credentials=FakeUserCredentials(),
        timeout_seconds=12.25,
    )

    assert service._http.http.timeout == 12.25


def test_the_google_credentials_are_attached_to_authorized_http() -> None:
    credentials = FakeUserCredentials()

    service = client_module.build_google_sheets_service(
        credentials=credentials,
        timeout_seconds=7.5,
    )

    assert service._http.credentials is credentials


def test_build_receives_http_but_never_credentials(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(service_name: str, version: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        captured["service"] = (service_name, version)
        return object()

    monkeypatch.setattr(client_module, "build", fake_build)

    client_module.build_google_sheets_service(
        credentials=FakeUserCredentials(),
        timeout_seconds=7.5,
    )

    assert "http" in captured
    assert "credentials" not in captured, "http and credentials are mutually exclusive"


def test_the_official_origin_and_discovery_flags_are_unchanged(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(service_name: str, version: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(client_module, "build", fake_build)

    client_module.build_google_sheets_service(
        credentials=FakeUserCredentials(),
        timeout_seconds=7.5,
    )

    assert captured["client_options"] == {"api_endpoint": SHEETS_API_ORIGIN}
    assert captured["static_discovery"] is True
    assert captured["cache_discovery"] is False


def test_the_real_build_accepts_the_constructed_arguments() -> None:
    """Call the real googleapiclient build with static discovery.

    This is the assertion the mock-only test was missing: the real builder
    rejects ``http`` together with ``credentials`` before any network call, so a
    regression cannot hide behind a fake ``build``. No Google request is made.
    """

    credentials = FakeUserCredentials()

    service = client_module.build_google_sheets_service(
        credentials=credentials,
        timeout_seconds=7.5,
    )

    assert service is not None
    assert service._http.credentials is credentials
    # Static discovery is in effect, so the resource tree is already populated.
    assert service.spreadsheets() is not None


def test_metadata_exposes_the_spreadsheet_locale() -> None:
    transport = FakeTransport(
        metadata={
            "properties": {"timeZone": "Asia/Jakarta", "locale": "id_ID"},
            "sheets": [{"properties": {"sheetId": 0, "title": "Opportunities"}}],
        }
    )

    metadata = GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert metadata.locale == "id_ID"
    assert metadata.time_zone == "Asia/Jakarta"


def test_metadata_without_a_locale_reports_none() -> None:
    transport = FakeTransport(
        metadata={
            "properties": {"timeZone": "Asia/Jakarta"},
            "sheets": [{"properties": {"title": "Opportunities"}}],
        }
    )

    metadata = GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert metadata.locale is None


def test_metadata_rejects_a_non_string_locale() -> None:
    transport = FakeTransport(
        metadata={
            "properties": {"locale": 42},
            "sheets": [{"properties": {"title": "Opportunities"}}],
        }
    )

    with pytest.raises(GoogleSheetsError) as captured:
        GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert captured.value.code == "malformed_response"


def test_metadata_keeps_the_raw_indonesian_locale_alias() -> None:
    transport = FakeTransport(
        metadata={
            "properties": {"timeZone": "Asia/Jakarta", "locale": "in_ID"},
            "sheets": [{"properties": {"sheetId": 0, "title": "Opportunities"}}],
        }
    )

    metadata = GoogleSheetsClient(transport).get_metadata("spreadsheet")

    assert metadata.locale == "in_ID"
    assert metadata.time_zone == "Asia/Jakarta"
