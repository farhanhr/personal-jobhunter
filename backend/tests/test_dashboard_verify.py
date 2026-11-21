"""Tests for the read-only dashboard verification.

These tests use a fake client, so they prove the *diagnosis logic*: a written
formula that returns an error, a metric written as a constant, and a metric whose
number disagrees with the oracle must each be reported as a failure rather than as
a successful write.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout
from personal_job_finder_api.integrations.google_sheets import dashboard_verify as verify
from personal_job_finder_api.integrations.google_sheets.client import (
    GridCell,
    SheetProperties,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.dashboard_verify import (
    CARD_CELLS,
)

AS_OF = date(2026, 11, 20)


def _opportunity_row(**overrides: str) -> list[str]:
    row = [""] * len(OPPORTUNITY_HEADERS)
    values = {"opportunity_id": "opp-1", "location_status": "eligible"}
    values.update(overrides)
    for name, value in values.items():
        row[OPPORTUNITY_HEADERS.index(name)] = value
    return row


class FakeClient:
    """The narrow client surface ``verify_dashboard`` uses."""

    def __init__(
        self,
        *,
        opportunities: list[list[str]] | None = None,
        sources: list[list[str]] | None = None,
        cells: dict[str, GridCell] | None = None,
        strip: dict[str, GridCell] | None = None,
        sentinel: str = layout.METRICS_SENTINEL,
        time_zone: str | None = "Asia/Jakarta",
        locale: str | None = "in_ID",
        tabs: tuple[str, ...] = ("Opportunities", "Job_Sources", layout.METRICS_TAB, "Sheet1"),
    ) -> None:
        self._opportunities = opportunities or [list(OPPORTUNITY_HEADERS)]
        self._sources = sources or [list(JOB_SOURCE_HEADERS)]
        self._cells = cells or {}
        self._strip = strip or {
            address: GridCell(address=address, formula="=0", value="0") for address in CARD_CELLS
        }
        self._sentinel = sentinel
        self._time_zone = time_zone
        self._locale = locale
        self._tabs = tabs
        self.grid_ranges: list[str] = []

    def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        return SpreadsheetMetadata(
            titles=self._tabs,
            time_zone=self._time_zone,
            locale=self._locale,
            sheets=tuple(SheetProperties(title=title) for title in self._tabs),
        )

    def get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str | None = None,
    ) -> dict[str, list[list[str]]]:
        result: dict[str, list[list[str]]] = {}
        for requested in ranges:
            if requested.startswith("Opportunities"):
                result[requested] = self._opportunities
            elif requested.startswith("Job_Sources"):
                result[requested] = self._sources
            else:
                result[requested] = [[self._sentinel]]
        return result

    def get_grid(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, list[list[GridCell]]]:
        self.grid_ranges = list(ranges)
        result: dict[str, list[list[GridCell]]] = {}
        for requested in ranges:
            if requested.startswith("Opportunities"):
                cells = dict(self._strip)
            else:
                cells = dict(self._cells)
                cells[layout.SENTINEL_CELL] = GridCell(
                    address=layout.SENTINEL_CELL, literal=self._sentinel
                )
            ordered = sorted(cells, key=lambda value: (value[0], int(value[1:])))
            result[requested] = [[cells[address] for address in ordered]]
        return result


def _all_zero_cells() -> dict[str, GridCell]:
    cells: dict[str, GridCell] = {}
    for address in verify.METRIC_CELLS:
        if address == "B2":
            cells[address] = GridCell(address=address, formula="=TODAY()", value="2026-11-20")
        else:
            cells[address] = GridCell(address=address, formula="=0", value="0")
    return cells


def _verify(client: Any) -> verify.VerifyReport:
    return verify.verify_dashboard(client, "spreadsheet", as_of=AS_OF)


def test_a_correctly_calculated_dashboard_passes() -> None:
    report = _verify(FakeClient(cells=_all_zero_cells()))

    assert report.ok, report.failures
    metric_checks = [check for check in report.checks if check.tab == layout.METRICS_TAB]
    total = len(metric_checks) + len(CARD_CELLS)
    assert report.summary().startswith(f"{total}/{total}")
    assert report.problems == ()


def test_a_header_only_sheet_is_expected_to_be_all_zero() -> None:
    report = _verify(FakeClient(cells=_all_zero_cells()))

    assert all(check.expected in (0, "2026-11-20") for check in report.checks)


def test_every_metric_is_read_in_one_bounding_range() -> None:
    client = FakeClient(cells=_all_zero_cells())

    _verify(client)

    # The strip read comes first; the metric board is still one rectangle.
    assert f"{layout.METRICS_TAB}!A1:M34" in client.grid_ranges


def test_a_formula_error_is_reported_as_a_failure_not_a_write_success() -> None:
    cells = _all_zero_cells()
    cells["B6"] = GridCell(address="B6", formula="=IF(", value=None, error="ERROR")

    report = _verify(FakeClient(cells=cells))

    assert not report.ok
    failing = {check.address: check for check in report.failures}
    assert failing["B6"].status == "error"
    assert failing["B6"].error == "ERROR"


def test_a_metric_written_as_a_constant_is_reported() -> None:
    cells = _all_zero_cells()
    cells["B30"] = GridCell(address="B30", literal="0", value="0")

    report = _verify(FakeClient(cells=cells))

    failing = {check.address: check for check in report.failures}
    assert failing["B30"].status == "not_a_formula"


def test_a_missing_metric_cell_is_reported() -> None:
    cells = _all_zero_cells()
    del cells["B33"]

    report = _verify(FakeClient(cells=cells))

    failing = {check.address: check for check in report.failures}
    assert failing["B33"].status == "missing"


def test_a_stale_value_is_reported_as_a_mismatch() -> None:
    opportunities = [list(OPPORTUNITY_HEADERS), _opportunity_row()]
    cells = _all_zero_cells()  # B6 still shows 0 although one row exists

    report = _verify(FakeClient(opportunities=opportunities, cells=cells))

    failing = {check.address: check for check in report.failures}
    assert failing["B6"].status == "mismatch"
    assert failing["B6"].expected == 1
    assert failing["B6"].actual == "0"


def test_the_oracle_expected_value_is_used_for_comparison() -> None:
    opportunities = [
        list(OPPORTUNITY_HEADERS),
        _opportunity_row(opportunity_id="opp-1", application_status="Applied"),
        _opportunity_row(opportunity_id="opp-2", application_status="Applied"),
    ]
    cells = _all_zero_cells()
    cells["B6"] = GridCell(address="B6", formula="=2", value="2")
    cells["B14"] = GridCell(address="B14", formula="=2", value="2")
    cells["B21"] = GridCell(address="B21", formula="=0", value="0")
    cells["B30"] = GridCell(address="B30", formula="=2", value="2")
    cells["J1"] = GridCell(address="J1", formula="=2", value="2")

    report = _verify(FakeClient(opportunities=opportunities, cells=cells))

    metric_checks = [check for check in report.checks if check.tab == layout.METRICS_TAB]
    assert all(check.ok for check in metric_checks), metric_checks


def test_numbers_are_compared_numerically_not_as_text() -> None:
    cells = _all_zero_cells()
    cells["B6"] = GridCell(address="B6", formula="=0", value="0.0")

    report = _verify(FakeClient(cells=cells))

    assert report.ok, report.failures


def test_a_missing_metrics_tab_is_a_problem_not_a_crash() -> None:
    report = _verify(FakeClient(tabs=("Opportunities", "Job_Sources")))

    assert any("Metrics board does not exist" in problem for problem in report.problems)
    assert not report.ok


def test_a_foreign_sentinel_is_reported() -> None:
    report = _verify(FakeClient(cells=_all_zero_cells(), sentinel="something else"))

    assert any("sentinel" in problem for problem in report.problems)
    assert not report.ok


def test_a_non_jakarta_time_zone_is_reported() -> None:
    report = _verify(FakeClient(cells=_all_zero_cells(), time_zone="Europe/Berlin"))

    assert any("time zone" in problem for problem in report.problems)


def test_an_incomplete_canonical_header_row_is_reported() -> None:
    report = _verify(
        FakeClient(
            opportunities=[["opportunity_id", "company_name"]],
            cells=_all_zero_cells(),
        )
    )

    assert report.checks == ()
    assert any("header row is incomplete" in problem for problem in report.problems)


def test_the_verify_module_never_writes() -> None:
    client = FakeClient(cells=_all_zero_cells())

    _verify(client)

    assert not hasattr(client, "batch_update")


# ------------------------------------------------------------------ KPI strip


def test_the_strip_is_verified_against_its_own_oracle() -> None:
    client = FakeClient(cells=_all_zero_cells())

    report = _verify(client)

    strip_checks = [check for check in report.checks if check.tab == "Opportunities"]
    assert len(strip_checks) == len(CARD_CELLS)
    assert {check.address for check in strip_checks} == set(CARD_CELLS)
    assert report.ok, report.failures


def test_a_card_that_disagrees_is_reported() -> None:
    strip = {address: GridCell(address=address, formula="=0", value="0") for address in CARD_CELLS}
    strip["E2"] = GridCell(address="E2", formula="=99", value="99")

    report = _verify(FakeClient(cells=_all_zero_cells(), strip=strip))

    failing = {check.address: check for check in report.failures if check.tab == "Opportunities"}
    assert failing["E2"].expected == 0
    assert failing["E2"].actual == "99"


def test_a_card_written_as_a_constant_is_reported() -> None:
    strip = {address: GridCell(address=address, formula="=0", value="0") for address in CARD_CELLS}
    strip["A2"] = GridCell(address="A2", literal="0", value="0")

    report = _verify(FakeClient(cells=_all_zero_cells(), strip=strip))

    failing = {check.address: check for check in report.failures if check.tab == "Opportunities"}
    assert failing["A2"].status == "not_a_formula"


def test_a_card_that_errors_is_reported() -> None:
    strip = {address: GridCell(address=address, formula="=0", value="0") for address in CARD_CELLS}
    strip["M2"] = GridCell(address="M2", formula="=IF(", value=None, error="ERROR")

    report = _verify(FakeClient(cells=_all_zero_cells(), strip=strip))

    failing = {check.address: check for check in report.failures if check.tab == "Opportunities"}
    assert failing["M2"].status == "error"
