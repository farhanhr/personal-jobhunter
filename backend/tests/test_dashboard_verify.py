"""Tests for the read-only Dashboard verification.

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
from personal_job_finder_api.integrations.google_sheets import workspace_layout as cards_layout
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
        sentinel: str = layout.METRICS_SENTINEL,
        time_zone: str | None = "Asia/Jakarta",
        locale: str | None = "in_ID",
        tabs: tuple[str, ...] = ("Opportunities", "Job_Sources", layout.METRICS_TAB, "Sheet1"),
        cards: bool = False,
        cards_sentinel: str = cards_layout.DASHBOARD_SENTINEL,
    ) -> None:
        self._opportunities = opportunities or [list(OPPORTUNITY_HEADERS)]
        self._sources = sources or [list(JOB_SOURCE_HEADERS)]
        self._cells = cells or {}
        self._sentinel = sentinel
        self._time_zone = time_zone
        self._locale = locale
        self._tabs = tabs
        self._cards_enabled = cards
        self._cards_sentinel = cards_sentinel
        self._cards = {
            address: GridCell(address=address, formula="=0", value="0") for address in CARD_CELLS
        }
        self.grid_ranges: list[str] = []

    def get_metadata(self, spreadsheet_id: str) -> SpreadsheetMetadata:
        titles = self._tabs
        if self._cards_enabled and cards_layout.DASHBOARD_TAB not in titles:
            titles = (*titles, cards_layout.DASHBOARD_TAB)
        return SpreadsheetMetadata(
            titles=titles,
            time_zone=self._time_zone,
            locale=self._locale,
            sheets=tuple(SheetProperties(title=title) for title in titles),
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
            if requested.startswith(cards_layout.DASHBOARD_TAB):
                cells = dict(self._cards)
                cells[cards_layout.SENTINEL_CELL] = GridCell(
                    address=cards_layout.SENTINEL_CELL, literal=self._cards_sentinel
                )
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
    assert report.summary().startswith(f"{len(verify.METRIC_CELLS)}/{len(verify.METRIC_CELLS)}")
    assert report.problems == ()


def test_a_header_only_sheet_is_expected_to_be_all_zero() -> None:
    report = _verify(FakeClient(cells=_all_zero_cells()))

    assert all(check.expected in (0, "2026-11-20") for check in report.checks)


def test_every_metric_is_read_in_one_bounding_range() -> None:
    client = FakeClient(cells=_all_zero_cells())

    _verify(client)

    # The sentinel sits in column M, so the rectangle reaches it in the same read.
    assert client.grid_ranges == [f"{layout.METRICS_TAB}!A1:M34"]


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

    assert report.ok, report.failures


def test_numbers_are_compared_numerically_not_as_text() -> None:
    cells = _all_zero_cells()
    cells["B6"] = GridCell(address="B6", formula="=0", value="0.0")

    report = _verify(FakeClient(cells=cells))

    assert report.ok, report.failures


def test_a_missing_dashboard_tab_is_a_problem_not_a_crash() -> None:
    report = _verify(FakeClient(tabs=("Opportunities", "Job_Sources")))

    assert report.checks == ()
    assert any("Neither the Dashboard" in problem for problem in report.problems)
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


# --------------------------------------------------------------- cards tab


def test_the_cards_tab_is_verified_against_the_card_oracle() -> None:
    cells = _all_zero_cells()
    client = FakeClient(cells=cells, cards=True)

    report = _verify(client)

    card_checks = [check for check in report.checks if check.tab == cards_layout.DASHBOARD_TAB]
    assert len(card_checks) == len(CARD_CELLS)
    assert {check.address for check in card_checks} == set(CARD_CELLS)
    assert report.ok, report.failures


def test_a_card_that_disagrees_is_reported() -> None:
    cells = _all_zero_cells()
    client = FakeClient(cells=cells, cards=True)
    client._cards["B7"] = GridCell(address="B7", formula="=99", value="99")

    report = _verify(client)

    failing = {
        check.address: check for check in report.failures if check.tab == cards_layout.DASHBOARD_TAB
    }
    assert failing["B7"].expected == 0
    assert failing["B7"].actual == "99"


def test_a_cards_tab_without_a_sentinel_is_reported() -> None:
    client = FakeClient(cells=_all_zero_cells(), cards=True, cards_sentinel="")

    report = _verify(client)

    assert any("cards sentinel" in problem for problem in report.problems)
    assert not report.ok
