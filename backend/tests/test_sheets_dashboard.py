"""Tests for optional Dashboard initialization.

Every test uses an injected fake transport. Nothing here talks to Google, and a
passing test proves request construction and refusal behavior, not spreadsheet
formula execution.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout
from personal_job_finder_api.integrations.google_sheets.client import GoogleSheetsClient
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADER_ROW,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.dashboard import (
    MetricsInitService,
    MetricsInitState,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.preflight import (
    SpreadsheetPreflightService,
)
from personal_job_finder_api.repositories.sync_outbox import (
    OUTBOX_ENTITY_TYPE,
    OUTBOX_ENTITY_TYPE_SOURCE,
)
from personal_job_finder_api.sync.dispatcher import _tab_for

SPREADSHEET_ID = "test_spreadsheet_id_1234567890"


class DashboardTransport:
    """A stateful fake of the subset of the Sheets API this feature uses.

    Two provider behaviors are modelled deliberately, because the implementation
    depends on them:

    - ``spreadsheets.batchUpdate`` validates every request before applying any of
      them, so one bad request leaves the spreadsheet untouched.
    - a whole-tab values read omits trailing empty rows and columns, and a
      ``FORMULA`` render returns the formula text for a formula cell.
    """

    def __init__(
        self,
        *,
        time_zone: str | None = "Asia/Jakarta",
        tabs: dict[str, list[list[str]]] | None = None,
    ) -> None:
        self.tabs = dict(tabs or {})
        self.time_zone = time_zone
        self.locale: str | None = "en_US"
        self.sheet_ids = {title: index for index, title in enumerate(self.tabs)}
        self._next_sheet_id = 900
        self.calls: list[str] = []
        self.updates: list[list[dict[str, Any]]] = []
        self.applied_batches = 0
        self.fail_before_apply = False
        self.fail_after_apply = False
        self.allow_raw_writes = False

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.calls.append("get_spreadsheet")
        properties: dict[str, Any] = {}
        if self.time_zone is not None:
            properties["timeZone"] = self.time_zone
        if self.locale is not None:
            properties["locale"] = self.locale
        return {
            "properties": properties,
            "sheets": [
                {"properties": {"sheetId": self.sheet_ids[title], "title": title}}
                for title in self.tabs
            ],
        }

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.calls.append("batch_get_values")
        value_ranges: list[dict[str, Any]] = []
        for requested in ranges:
            tab, _, a1 = requested.partition("!")
            rows = _slice_a1(self.tabs.get(tab, []), a1)
            value_ranges.append({"values": [list(row) for row in rows]} if rows else {})
        return {"valueRanges": value_ranges}

    def batch_get_values_with_render(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str,
    ) -> dict[str, Any]:
        self.calls.append(f"batch_get_values_with_render:{value_render_option}")
        if value_render_option != "FORMULA":
            raise AssertionError("only the FORMULA render is exercised by these tests")
        value_ranges: list[dict[str, Any]] = []
        for requested in ranges:
            tab, _, a1 = requested.partition("!")
            used = _used_block(self.tabs.get(tab, []))
            sliced = _slice_a1(used, a1)
            value_ranges.append({"values": sliced} if sliced else {})
        return {"valueRanges": value_ranges}

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        self.calls.append("batch_update_values")
        if not self.allow_raw_writes:
            raise AssertionError("dashboard-init must not use the raw values path")
        assert value_input_option == "RAW"
        for item in data:
            tab, _, a1 = str(item["range"]).partition("!")
            rows = self.tabs.setdefault(tab, [])
            first = a1.split(":")[0]
            match = _A1_PART.match(first)
            row_number = int(match.group(2)) if match and match.group(2) else 1
            column = _column_number(match.group(1)) if match and match.group(1) else 1
            while len(rows) < row_number:
                rows.append([])
            target = rows[row_number - 1]
            for offset, value in enumerate(item["values"][0]):
                index = column - 1 + offset
                while len(target) <= index:
                    target.append("")
                target[index] = str(value)
        return {}

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls.append("batch_update_spreadsheet")
        self.updates.append(requests)
        _validate_requests(requests)

        is_layout_batch = all("addSheet" not in request for request in requests)
        if self.fail_before_apply and is_layout_batch:
            self.fail_before_apply = False
            raise GoogleSheetsError("transport_failure", "simulated provider failure")

        for request in requests:
            if "addSheet" in request:
                title = request["addSheet"]["properties"]["title"]
                self.tabs[title] = []
                self.sheet_ids[title] = self._next_sheet_id
                self._next_sheet_id += 1
            elif "updateSheetProperties" in request:
                self._apply_sheet_properties(request["updateSheetProperties"])
            elif "updateCells" in request:
                self._apply_cells(request["updateCells"])
        if is_layout_batch:
            self.applied_batches += 1
        if self.fail_after_apply and is_layout_batch:
            # The provider applied the batch but the response never arrived.
            self.fail_after_apply = False
            raise GoogleSheetsError("transport_uncertain", "simulated lost response")
        return {}

    def _apply_sheet_properties(self, body: dict[str, Any]) -> None:
        """Model a title rename, which moves the tab and keeps its content."""

        properties = body.get("properties", {})
        sheet_id = properties.get("sheetId")
        new_title = properties.get("title")
        if sheet_id is None or new_title is None:
            return
        for title, value in list(self.sheet_ids.items()):
            if value != sheet_id:
                continue
            rows = self.tabs.pop(title, [])
            self.sheet_ids.pop(title, None)
            self.tabs[new_title] = rows
            self.sheet_ids[new_title] = sheet_id
            return

    def _apply_cells(self, body: dict[str, Any]) -> None:
        sheet_id = body["start"]["sheetId"]
        tab = next(title for title, value in self.sheet_ids.items() if value == sheet_id)
        start_row = body["start"]["rowIndex"]
        start_column = body["start"]["columnIndex"]
        grid = self.tabs.setdefault(tab, [])
        for row_offset, row in enumerate(body["rows"]):
            target_row = start_row + row_offset
            while len(grid) <= target_row:
                grid.append([])
            cells = grid[target_row]
            for column_offset, cell in enumerate(row["values"]):
                target_column = start_column + column_offset
                while len(cells) <= target_column:
                    cells.append("")
                value = cell.get("userEnteredValue")
                if value is None:
                    continue
                cells[target_column] = value.get("stringValue") or value.get("formulaValue") or ""


def _validate_requests(requests: list[dict[str, Any]]) -> None:
    """Reject a malformed batch before applying anything, like the provider."""

    for request in requests:
        if "__invalid__" in request:
            raise GoogleSheetsError("invalid_request", "simulated invalid request")
        body = request.get("updateCells")
        if body is None:
            continue
        if not {"start", "rows", "fields"} <= set(body):
            raise GoogleSheetsError("invalid_request", "simulated invalid updateCells")
        if "sheetId" not in body["start"]:
            raise GoogleSheetsError("invalid_request", "simulated missing sheetId")


_A1_PART = re.compile(r"^\$?([A-Za-z]*)\$?(\d*)$")


def _column_number(letters: str) -> int:
    """Convert column letters to a 1-based index."""

    value = 0
    for character in letters.upper():
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


def _slice_a1(rows: list[list[str]], a1: str) -> list[list[str]]:
    """Return the requested rectangle, as a real values read would.

    The fake used to ignore the A1 part and answer with the whole tab, which
    hid bugs in callers that read one cell. It now slices, so a caller asking
    for ``M1`` receives one cell.
    """

    if not a1:
        return rows
    parts = a1.split(":")
    start = _A1_PART.match(parts[0].strip())
    end = _A1_PART.match(parts[-1].strip()) if len(parts) > 1 else start
    if start is None or end is None:
        return rows

    start_column, start_row = start.group(1), start.group(2)
    end_column, end_row = end.group(1), end.group(2)
    first_row = int(start_row) if start_row else 1
    last_row = int(end_row) if end_row else len(rows)
    first_column = _column_number(start_column) if start_column else 1
    last_column = _column_number(end_column) if end_column else None

    sliced: list[list[str]] = []
    for row_number in range(first_row, min(last_row, len(rows)) + 1):
        row = rows[row_number - 1]
        if last_column is None:
            sliced.append(list(row[first_column - 1 :]))
        else:
            sliced.append(list(row[first_column - 1 : last_column]))
    return sliced


def _used_block(rows: list[list[str]]) -> list[list[str]]:
    """Trim trailing empty rows and cells, as a whole-tab values read does."""

    used: list[list[str]] = []
    for row in rows:
        cells = list(row)
        while cells and cells[-1] == "":
            cells.pop()
        used.append(cells)
    while used and not used[-1]:
        used.pop()
    return used


def _spreadsheet(
    *,
    time_zone: str | None = "Asia/Jakarta",
    dashboard: list[list[str]] | None = None,
    legacy_dashboard: list[list[str]] | None = None,
) -> DashboardTransport:
    tabs: dict[str, list[list[str]]] = {
        "Opportunities": [[""] for _ in range(OPPORTUNITY_HEADER_ROW - 1)]
        + [list(OPPORTUNITY_HEADERS)],
        "Job_Sources": [list(JOB_SOURCE_HEADERS)],
    }
    if dashboard is not None:
        tabs[layout.METRICS_TAB] = dashboard
    if legacy_dashboard is not None:
        tabs["Dashboard"] = legacy_dashboard
    return DashboardTransport(time_zone=time_zone, tabs=tabs)


def _service(transport: DashboardTransport) -> MetricsInitService:
    return MetricsInitService(
        GoogleSheetsClient(transport),
        SPREADSHEET_ID,
    )


def _cell(transport: DashboardTransport, address: str) -> str:
    row, column = layout.parse_address(address)
    grid = transport.tabs[layout.METRICS_TAB]
    if row >= len(grid) or column >= len(grid[row]):
        return ""
    return grid[row][column]


def _sentinel_grid() -> list[list[str]]:
    row = [""] * 12
    row.append(layout.METRICS_SENTINEL)
    return [row]


# --------------------------------------------------------------------- state A


def test_a_missing_dashboard_is_created_and_initialized() -> None:
    transport = _spreadsheet()

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.CREATED
    assert layout.METRICS_TAB in transport.tabs
    assert _cell(transport, "M1") == layout.METRICS_SENTINEL
    assert _cell(transport, "B6").startswith("=IF(SUMPRODUCT(")
    assert _cell(transport, "A1").startswith("Job Search Metrics")
    assert "batch_update_values" not in transport.calls


def test_a_missing_dashboard_hides_the_helper_columns_and_bolds_sections() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    requests = [request for batch in transport.updates for request in batch]
    hidden = [
        request["updateDimensionProperties"]
        for request in requests
        if "updateDimensionProperties" in request
        and request["updateDimensionProperties"]["properties"].get("hiddenByUser")
    ]
    assert len(hidden) == 1
    assert hidden[0]["range"]["startIndex"] == layout.column_index("H")
    assert hidden[0]["range"]["endIndex"] == layout.column_index("M") + 1


def test_every_layout_cell_is_written_exactly_once() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    expected = {cell.address for cell in layout.all_cells()} | {layout.SENTINEL_CELL}
    written: list[str] = []
    for batch in transport.updates:
        for request in batch:
            if "updateCells" not in request:
                continue
            body = request["updateCells"]
            row_start = body["start"]["rowIndex"]
            column_start = body["start"]["columnIndex"]
            for row_offset, row in enumerate(body["rows"]):
                for column_offset, cell in enumerate(row["values"]):
                    if cell.get("userEnteredValue") is None:
                        continue
                    row_index = row_start + row_offset
                    column_index = column_start + column_offset
                    written.append(f"{_letters(column_index)}{row_index + 1}")
    assert sorted(written) == sorted(expected)


def _letters(index: int) -> str:
    letters = ""
    remaining = index + 1
    while remaining:
        remaining, remainder = divmod(remaining - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


# --------------------------------------------------------------------- state B


def test_an_empty_dashboard_tab_is_initialized() -> None:
    transport = _spreadsheet(dashboard=[])

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.INITIALIZED
    assert _cell(transport, "M1") == layout.METRICS_SENTINEL


def test_a_dashboard_with_only_blank_cells_counts_as_empty() -> None:
    transport = _spreadsheet(dashboard=[["", "", ""], ["", ""]])

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.INITIALIZED


# --------------------------------------------------------------------- state C


def test_an_initialized_dashboard_is_a_no_op() -> None:
    transport = _spreadsheet(dashboard=_sentinel_grid())

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.ALREADY_INITIALIZED
    assert transport.updates == []


def test_a_no_op_preserves_user_edits_next_to_the_sentinel() -> None:
    grid = _sentinel_grid()
    grid.extend([] for _ in range(4))
    grid[4] = ["My own extra note"]
    transport = _spreadsheet(dashboard=grid)

    _service(transport).initialize()

    assert transport.updates == []
    assert _cell(transport, "A5") == "My own extra note"


# --------------------------------------------------------------------- state D


def test_a_foreign_non_empty_dashboard_is_refused() -> None:
    transport = _spreadsheet(dashboard=[["My own dashboard"]])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []


def test_a_foreign_dashboard_with_content_far_from_the_layout_is_refused() -> None:
    grid = [[""]] * 60
    grid[59] = ["", "", "", "kept"]
    transport = _spreadsheet(dashboard=grid)

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []


# --------------------------------------------------------------------- state E


def test_an_unsupported_sentinel_version_is_refused() -> None:
    row = [""] * 12
    row.append("PJF_DASHBOARD_V0")
    transport = _spreadsheet(dashboard=[row])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_unsupported_version"
    assert transport.updates == []


def test_the_previous_layout_version_is_refused_instead_of_trusted() -> None:
    """V1 helpers returned their fallback in Sheets, so V1 numbers were wrong."""

    row = [""] * 12
    row.append("PJF_DASHBOARD_V1")
    transport = _spreadsheet(dashboard=[row])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_unsupported_version"
    assert transport.updates == []


# ------------------------------------------------------------------- states F/G


def test_a_missing_time_zone_is_refused_before_any_write() -> None:
    transport = _spreadsheet(time_zone=None)

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_timezone_missing"
    assert transport.updates == []
    assert layout.METRICS_TAB not in transport.tabs


def test_an_incorrect_time_zone_is_refused_before_any_write() -> None:
    transport = _spreadsheet(time_zone="Europe/Berlin")

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_timezone_mismatch"
    assert transport.updates == []
    assert layout.METRICS_TAB not in transport.tabs


def test_the_time_zone_refusal_never_mentions_the_spreadsheet_id() -> None:
    transport = _spreadsheet(time_zone="Europe/Berlin")

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert SPREADSHEET_ID not in str(captured.value)


# ------------------------------------------------------- canonical tab safety


def test_canonical_tabs_keep_their_headers_and_ids() -> None:
    transport = _spreadsheet()
    before = {title: list(rows) for title, rows in transport.tabs.items()}
    canonical_ids = {transport.sheet_ids["Opportunities"], transport.sheet_ids["Job_Sources"]}

    _service(transport).initialize()

    assert transport.tabs["Opportunities"] == before["Opportunities"]
    assert transport.tabs["Job_Sources"] == before["Job_Sources"]
    for batch in transport.updates:
        for request in batch:
            body = next(iter(request.values()))
            if "range" in body:
                assert body["range"]["sheetId"] not in canonical_ids


def test_the_dashboard_is_never_created_by_canonical_init() -> None:
    transport = DashboardTransport(time_zone="Asia/Jakarta", tabs={})
    transport.allow_raw_writes = True

    SpreadsheetPreflightService(transport).initialize(SPREADSHEET_ID)

    assert set(transport.tabs) == {"Opportunities", "Job_Sources"}


# ------------------------------------------------------------ atomic write


def test_initialization_is_one_batch_with_the_sentinel_last() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    structural = [batch for batch in transport.updates if any("addSheet" not in r for r in batch)]
    assert len(structural) == 1, "layout, formatting, and sentinel must share one batch"

    batch = structural[0]
    assert "updateCells" in batch[-1]
    sentinel_body = batch[-1]["updateCells"]
    assert sentinel_body["start"]["rowIndex"] == layout.parse_address(layout.SENTINEL_CELL)[0]
    assert sentinel_body["start"]["columnIndex"] == layout.parse_address(layout.SENTINEL_CELL)[1]
    assert sentinel_body["rows"][0]["values"][0]["userEnteredValue"] == {
        "stringValue": layout.METRICS_SENTINEL
    }

    earlier_kinds = {next(iter(request)) for request in batch[:-1]}
    assert earlier_kinds == {"updateCells", "updateDimensionProperties", "repeatCell"}
    assert transport.applied_batches == 1


def test_the_tab_creation_is_a_separate_call_from_the_layout_batch() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    add_sheet_batches = [
        batch for batch in transport.updates if any("addSheet" in r for r in batch)
    ]
    layout_batches = [
        batch for batch in transport.updates if any("addSheet" not in r for r in batch)
    ]
    assert len(add_sheet_batches) == 1
    assert len(layout_batches) == 1
    # addSheet is not atomic with the layout batch: a failure between them leaves
    # an empty tab, which the next run initializes.
    assert transport.updates.index(add_sheet_batches[0]) < transport.updates.index(
        layout_batches[0]
    )


def test_a_successful_sentinel_implies_the_whole_layout_was_applied() -> None:
    transport = _spreadsheet()

    _service(transport).initialize()

    assert _cell(transport, "M1") == layout.METRICS_SENTINEL
    for cell in layout.all_cells():
        assert _cell(transport, cell.address) == cell.content, cell.address


def test_a_failed_layout_batch_writes_nothing_at_all() -> None:
    transport = _spreadsheet()
    transport.fail_before_apply = True

    with pytest.raises(GoogleSheetsError):
        _service(transport).initialize()

    assert transport.applied_batches == 0
    assert _cell(transport, "M1") == ""
    assert _cell(transport, "B6") == ""
    assert _cell(transport, "A1") == ""


def test_one_invalid_request_fails_the_whole_batch_without_partial_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _spreadsheet()
    original = layout.build_format_requests

    def with_invalid_request(sheet_id: int) -> list[dict[str, object]]:
        return [*original(sheet_id), {"__invalid__": True}]

    monkeypatch.setattr(layout, "build_format_requests", with_invalid_request)

    with pytest.raises(GoogleSheetsError):
        _service(transport).initialize()

    assert transport.applied_batches == 0
    assert _cell(transport, "M1") == ""
    assert _cell(transport, "B6") == ""


def test_a_retry_after_a_failed_batch_initializes_successfully() -> None:
    transport = _spreadsheet()
    transport.fail_before_apply = True

    with pytest.raises(GoogleSheetsError):
        _service(transport).initialize()

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.INITIALIZED
    assert _cell(transport, "M1") == layout.METRICS_SENTINEL
    assert _cell(transport, "B6").startswith("=IF(SUMPRODUCT(")


def test_a_lost_response_after_submission_is_resolved_by_the_next_inspection() -> None:
    """The provider applied the batch but the response never arrived.

    The client cannot tell "applied" from "not applied", so it reports a failure.
    The next run re-inspects the tab instead of trusting the error: the sentinel is
    present, so the state is ``already_initialized`` and nothing is rewritten.
    """

    transport = _spreadsheet()
    transport.fail_after_apply = True

    with pytest.raises(GoogleSheetsError):
        _service(transport).initialize()

    assert transport.applied_batches == 1
    assert _cell(transport, "M1") == layout.METRICS_SENTINEL

    batches_before = len(transport.updates)
    result = _service(transport).initialize()

    assert result.state is MetricsInitState.ALREADY_INITIALIZED
    assert len(transport.updates) == batches_before
    assert _cell(transport, "B6").startswith("=IF(SUMPRODUCT(")


# ------------------------------------------------------------- preflight parity


def test_preflight_is_ready_without_a_dashboard_tab() -> None:
    transport = _spreadsheet()

    result = SpreadsheetPreflightService(transport).check(SPREADSHEET_ID)

    assert result.is_ready is True
    assert layout.METRICS_TAB not in result.tabs


def test_preflight_is_ready_with_a_foreign_dashboard_tab() -> None:
    transport = _spreadsheet(dashboard=[["My own dashboard"]])

    result = SpreadsheetPreflightService(transport).check(SPREADSHEET_ID)

    assert result.is_ready is True
    assert layout.METRICS_TAB not in result.tabs


# ------------------------------------------------------- sync stays canonical


def test_the_dispatcher_tab_mapping_stays_canonical() -> None:
    assert _tab_for(SimpleNamespace(entity_type=OUTBOX_ENTITY_TYPE)) == "Opportunities"
    assert _tab_for(SimpleNamespace(entity_type=OUTBOX_ENTITY_TYPE_SOURCE)) == "Job_Sources"
    assert _tab_for(SimpleNamespace(entity_type="contact")) is None


def test_the_foundation_exposes_dashboard_initialization(tmp_path) -> None:
    from personal_job_finder_api.core.config import Settings
    from personal_job_finder_api.integrations.google_sheets.service import (
        build_google_sheets_foundation,
    )

    transport = _spreadsheet()
    settings = Settings(
        data_dir=tmp_path,
        google_sheets_spreadsheet_id=SPREADSHEET_ID,
    )

    foundation = build_google_sheets_foundation(settings, transport=transport)

    result = foundation.initialize_dashboard()

    assert result.state is MetricsInitState.CREATED
    assert _cell(transport, "M1") == layout.METRICS_SENTINEL


# ------------------------------------------------- whole-tab inspection


def _grid_with(address: str, content: str) -> list[list[str]]:
    """Build a Dashboard grid holding one value at an arbitrary address."""

    row, column = layout.parse_address(address)
    grid = [[""] for _ in range(row + 1)]
    grid[row] = [""] * (column + 1)
    grid[row][column] = content
    return grid


def test_inspection_reads_the_whole_tab_with_the_formula_render() -> None:
    transport = _spreadsheet(dashboard=[["My own dashboard"]])

    with pytest.raises(GoogleSheetsError):
        _service(transport).initialize()

    assert "batch_get_values_with_render:FORMULA" in transport.calls
    assert "batch_get_values" not in transport.calls


def test_a_value_at_a1001_is_detected_as_foreign_content() -> None:
    transport = _spreadsheet(dashboard=_grid_with("A1001", "kept"))

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []
    assert _cell(transport, "A1001") == "kept"


def test_a_value_at_ba1_is_detected_as_foreign_content() -> None:
    transport = _spreadsheet(dashboard=_grid_with("BA1", "far right"))

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []
    assert _cell(transport, "BA1") == "far right"


def test_a_formula_returning_an_empty_string_is_detected() -> None:
    transport = _spreadsheet(dashboard=[['=IF(1=2,"shown","")']])

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []
    assert _cell(transport, "A1") == '=IF(1=2,"shown","")'


def test_a_formula_outside_the_old_inspection_window_is_detected() -> None:
    transport = _spreadsheet(dashboard=_grid_with("B1200", "=SUM(A1:A2)"))

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_tab_not_owned"
    assert transport.updates == []


def test_a_truly_empty_existing_tab_is_initialized() -> None:
    transport = _spreadsheet(dashboard=[])

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.INITIALIZED
    assert _cell(transport, "M1") == layout.METRICS_SENTINEL


def test_a_refused_foreign_tab_is_left_completely_unchanged() -> None:
    grid = _grid_with("A1001", "kept")
    grid[0][0] = "header"
    transport = _spreadsheet(dashboard=[list(row) for row in grid])
    before = [list(row) for row in transport.tabs[layout.METRICS_TAB]]

    with pytest.raises(GoogleSheetsError):
        _service(transport).initialize()

    assert transport.tabs[layout.METRICS_TAB] == before
    assert transport.applied_batches == 0


def test_an_unreadable_inspection_is_refused_instead_of_treated_as_empty() -> None:
    transport = _spreadsheet(dashboard=[["My own dashboard"]])

    def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise GoogleSheetsError("transport_failure", "simulated read failure")

    transport.batch_get_values_with_render = fail  # type: ignore[method-assign]

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_inspection_failed"
    assert transport.updates == []


# ------------------------------------------------------- spreadsheet locale


def test_a_locale_aware_initialization_uses_semicolons() -> None:
    transport = _spreadsheet()
    transport.locale = "id_ID"

    _service(transport).initialize()

    assert _cell(transport, "B2") == (
        '=TEXT(YEAR(TODAY());"0000")&"-"&TEXT(MONTH(TODAY());"00")&"-"&TEXT(DAY(TODAY());"00")'
    )
    assert _cell(transport, "B6").startswith("=IF(SUMPRODUCT(")
    assert ";" in _cell(transport, "B6")
    assert _cell(transport, "B33").startswith("=IFERROR(SUM(")
    assert _cell(transport, "B34").startswith("=IFERROR(SUM(")


def test_the_written_cells_distinguish_formulas_from_labels() -> None:
    transport = _spreadsheet()
    transport.locale = "id_ID"

    _service(transport).initialize()

    for batch in transport.updates:
        for request in batch:
            body = request.get("updateCells")
            if body is None:
                continue
            for row in body["rows"]:
                for value in row["values"]:
                    entered = value.get("userEnteredValue")
                    if entered is None:
                        continue
                    if "formulaValue" in entered:
                        assert entered["formulaValue"].startswith("=")
                        assert "stringValue" not in entered
                    else:
                        assert not entered["stringValue"].startswith("=")


def test_an_unsupported_locale_is_refused_before_any_write() -> None:
    transport = _spreadsheet()
    transport.locale = "de_DE"

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_locale_unsupported"
    assert transport.updates == []
    assert layout.METRICS_TAB not in transport.tabs


def test_a_missing_locale_is_refused_before_any_write() -> None:
    transport = _spreadsheet()
    transport.locale = None

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert captured.value.code == "dashboard_locale_missing"
    assert transport.updates == []


def test_a_missing_locale_does_not_block_an_already_initialized_tab() -> None:
    """Inspection runs before the dialect is needed, so a no-op stays a no-op."""

    transport = _spreadsheet(dashboard=_sentinel_grid())
    transport.locale = None

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.ALREADY_INITIALIZED
    assert transport.updates == []


def test_the_locale_refusal_never_mentions_the_spreadsheet_id() -> None:
    transport = _spreadsheet()
    transport.locale = "de_DE"

    with pytest.raises(GoogleSheetsError) as captured:
        _service(transport).initialize()

    assert SPREADSHEET_ID not in str(captured.value)


def test_a_spreadsheet_reporting_in_id_initializes_with_indonesian_formulas() -> None:
    transport = _spreadsheet()
    transport.locale = "in_ID"

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.CREATED
    assert _cell(transport, "B2") == (
        '=TEXT(YEAR(TODAY());"0000")&"-"&TEXT(MONTH(TODAY());"00")&"-"&TEXT(DAY(TODAY());"00")'
    )
    assert ";" in _cell(transport, "B6")
    assert _cell(transport, "B33").startswith("=IFERROR(SUM(")
    assert _cell(transport, "B34").startswith("=IFERROR(SUM(")


def test_in_id_and_id_id_write_identical_batches() -> None:
    in_transport = _spreadsheet()
    in_transport.locale = "in_ID"
    id_transport = _spreadsheet()
    id_transport.locale = "id_ID"

    _service(in_transport).initialize()
    _service(id_transport).initialize()

    assert in_transport.updates == id_transport.updates


# ------------------------------------------------------------ legacy migration


def test_a_legacy_metric_board_is_renamed_to_metrics() -> None:
    """The old Dashboard board is moved, never deleted."""

    row = [""] * 12
    row.append(layout.METRICS_SENTINEL)
    transport = _spreadsheet(legacy_dashboard=[row])

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.ALREADY_INITIALIZED
    assert layout.METRICS_TAB in transport.tabs
    assert "Dashboard" not in transport.tabs
    assert transport.tabs[layout.METRICS_TAB][0][12] == layout.METRICS_SENTINEL
    rename_requests = [
        request
        for batch in transport.updates
        for request in batch
        if "updateSheetProperties" in request
    ]
    assert rename_requests
    assert rename_requests[0]["updateSheetProperties"]["properties"]["title"] == layout.METRICS_TAB


def test_a_foreign_dashboard_is_not_renamed() -> None:
    transport = _spreadsheet(legacy_dashboard=[["my own notes"]])

    result = _service(transport).initialize()

    assert result.state is MetricsInitState.CREATED
    assert "Dashboard" in transport.tabs
    assert transport.tabs["Dashboard"] == [["my own notes"]]
    assert layout.METRICS_TAB in transport.tabs
