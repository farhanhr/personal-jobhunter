"""Tests for the live-check harness.

The harness is the only code allowed to append fixture rows to a real
spreadsheet, so its safety properties are tested like production code: it must
refuse a non-disposable sheet, snapshot before writing, restore exactly, and stop
polling on a deadline.
"""

from __future__ import annotations

import json
import re
import stat
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from personal_job_finder_api.integrations.google_sheets import live_check
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_FIRST_DATA_ROW,
    OPPORTUNITY_HEADER_ROW,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

AS_OF = date(2026, 11, 20)
SPREADSHEET_ID = "spreadsheet_id_for_tests"


class FakeClient:
    """In-memory stand-in that records every write."""

    def __init__(self, tabs: dict[str, list[list[str]]]) -> None:
        self.tabs = {title: [list(row) for row in rows] for title, rows in tabs.items()}
        self.writes: list[dict[str, Any]] = []

    def get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
        *,
        value_render_option: str | None = None,
    ) -> dict[str, list[list[str]]]:
        return {name: [list(row) for row in self.tabs.get(name, [])] for name in ranges}

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.writes.append({"data": data})
        for entry in data:
            name = entry["range"].split("!")[0]
            first_cell = entry["range"].split("!")[1].split(":")[0]
            start = int(re.sub(r"[A-Z]+", "", first_cell))
            values = entry["values"]
            rows = self.tabs.setdefault(name, [])
            for offset, row in enumerate(values):
                index = start - 1 + offset
                while len(rows) <= index:
                    rows.append([])
                rows[index] = list(row)
        return {"totalUpdatedCells": 1}


def _header_only() -> dict[str, list[list[str]]]:
    """A workbook in the canonical layout: header at its contracted row."""

    return {
        "Opportunities": [[""] for _ in range(OPPORTUNITY_HEADER_ROW - 1)]
        + [list(OPPORTUNITY_HEADERS)],
        "Job_Sources": [list(JOB_SOURCE_HEADERS)],
    }


# ------------------------------------------------------------------ disposability


def test_a_header_only_sheet_is_accepted() -> None:
    client = FakeClient(_header_only())
    snapshot = live_check.capture(client, SPREADSHEET_ID)

    live_check.assert_disposable(snapshot)


def test_a_sheet_with_data_rows_is_refused() -> None:
    tabs = _header_only()
    tabs["Opportunities"].append(["opp-1"])
    snapshot = live_check.capture(FakeClient(tabs), SPREADSHEET_ID)

    with pytest.raises(live_check.LiveCheckError) as captured:
        live_check.assert_disposable(snapshot)

    assert "data row" in str(captured.value)
    assert "Nothing was written" in str(captured.value)


def test_a_mismatched_header_row_is_refused() -> None:
    tabs = _header_only()
    tabs["Opportunities"][OPPORTUNITY_HEADER_ROW - 1] = [
        "opportunity_id",
        "company_name",
    ]
    snapshot = live_check.capture(FakeClient(tabs), SPREADSHEET_ID)

    with pytest.raises(live_check.LiveCheckError) as captured:
        live_check.assert_disposable(snapshot)

    assert "header row" in str(captured.value)


def test_blank_padding_rows_are_not_treated_as_data() -> None:
    tabs = _header_only()
    tabs["Opportunities"].append(["", "", ""])
    snapshot = live_check.capture(FakeClient(tabs), SPREADSHEET_ID)

    live_check.assert_disposable(snapshot)


def test_data_rows_are_accepted_only_with_an_explicit_opt_in() -> None:
    tabs = _header_only()
    tabs["Job_Sources"].append(["s-a", "a", "greenhouse"])
    snapshot = live_check.capture(FakeClient(tabs), SPREADSHEET_ID)

    live_check.assert_disposable(snapshot, allow_data=True)


def test_the_opt_in_still_requires_canonical_headers() -> None:
    tabs = _header_only()
    tabs["Job_Sources"][0] = ["source_id", "opportunity_id"]
    tabs["Job_Sources"].append(["s-a", "a"])
    snapshot = live_check.capture(FakeClient(tabs), SPREADSHEET_ID)

    with pytest.raises(live_check.LiveCheckError) as captured:
        live_check.assert_disposable(snapshot, allow_data=True)

    assert "header row" in str(captured.value)


# --------------------------------------------------------------------- snapshot


def test_a_snapshot_is_persisted_with_owner_only_permissions(tmp_path: Path) -> None:
    snapshot = live_check.capture(FakeClient(_header_only()), SPREADSHEET_ID)

    path = live_check.save_snapshot(snapshot, tmp_path / "artifacts")

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    payload = json.loads(path.read_text())
    assert payload["run_id"] == snapshot.run_id
    assert payload["tabs"]["Opportunities"] == [[""] for _ in range(OPPORTUNITY_HEADER_ROW - 1)] + [
        list(OPPORTUNITY_HEADERS)
    ]


def test_a_snapshot_never_contains_the_spreadsheet_id() -> None:
    snapshot = live_check.capture(FakeClient(_header_only()), SPREADSHEET_ID)

    assert SPREADSHEET_ID not in snapshot.to_json()
    assert snapshot.spreadsheet_fingerprint.startswith("sheet-")


def test_the_fingerprint_is_stable_and_not_reversible() -> None:
    assert live_check.fingerprint(SPREADSHEET_ID) == live_check.fingerprint(SPREADSHEET_ID)
    assert live_check.fingerprint(SPREADSHEET_ID) != live_check.fingerprint("other")


# ---------------------------------------------------------------------- restore


def test_restore_writes_the_captured_values_back() -> None:
    client = FakeClient(_header_only())
    snapshot = live_check.capture(client, SPREADSHEET_ID)
    client.tabs["Opportunities"].append(["opp-1"])

    live_check.restore(client, SPREADSHEET_ID, snapshot)

    header_row = OPPORTUNITY_HEADER_ROW - 1
    assert client.tabs["Opportunities"][header_row] == list(OPPORTUNITY_HEADERS)
    data_rows = client.tabs["Opportunities"][header_row + 1 :]
    assert all(not any(row) for row in data_rows)


def test_restore_clears_rows_the_run_appended() -> None:
    client = FakeClient(_header_only())
    snapshot = live_check.capture(client, SPREADSHEET_ID)
    client.tabs["Opportunities"].append(["opp-1", "x"])
    client.tabs["Opportunities"].append(["opp-2", "y"])

    written = live_check.restore(client, SPREADSHEET_ID, snapshot)

    assert any(f"A{OPPORTUNITY_FIRST_DATA_ROW}:" in name for name in written)
    data_rows = client.tabs["Opportunities"][OPPORTUNITY_HEADER_ROW:]
    assert all(row == [] or not any(row) for row in data_rows)


def test_restore_refuses_when_the_header_row_changed() -> None:
    tabs = _header_only()
    tabs["Opportunities"].append(["opp-1"])
    client = FakeClient(tabs)
    snapshot = live_check.capture(client, SPREADSHEET_ID)
    client.tabs["Opportunities"][OPPORTUNITY_HEADER_ROW - 1] = ["something", "else"]

    with pytest.raises(live_check.LiveCheckError) as captured:
        live_check.restore(client, SPREADSHEET_ID, snapshot)

    assert "header row changed" in str(captured.value)


def test_restore_rewrites_a_tab_that_was_shortened() -> None:
    tabs = _header_only()
    tabs["Job_Sources"].append(["s-a", "a", "greenhouse"])
    tabs["Job_Sources"].append(["s-b", "b", "lever"])
    client = FakeClient(tabs)
    snapshot = live_check.capture(client, SPREADSHEET_ID)
    client.tabs["Job_Sources"] = client.tabs["Job_Sources"][:2]

    live_check.restore(client, SPREADSHEET_ID, snapshot)

    assert client.tabs["Job_Sources"][1][:3] == ["s-a", "a", "greenhouse"]
    assert client.tabs["Job_Sources"][2][:3] == ["s-b", "b", "lever"]


def test_clear_data_rows_keeps_the_header_and_row_count() -> None:
    tabs = _header_only()
    tabs["Job_Sources"].append(["s-a", "a", "greenhouse"])
    tabs["Job_Sources"].append(["s-b", "b", "lever"])
    client = FakeClient(tabs)

    live_check.clear_data_rows(client, SPREADSHEET_ID)

    assert client.tabs["Job_Sources"][0] == list(JOB_SOURCE_HEADERS)
    assert len(client.tabs["Job_Sources"]) == 3
    assert all(not any(row) for row in client.tabs["Job_Sources"][1:])


def test_restore_clears_cells_written_beyond_a_short_baseline_row() -> None:
    """``values.get`` trims trailing blanks, so a naive restore leaves residue."""

    tabs = _header_only()
    tabs["Job_Sources"].append(["s-a", "a", "greenhouse"])
    client = FakeClient(tabs)
    snapshot = live_check.capture(client, SPREADSHEET_ID)
    client.tabs["Job_Sources"][1] = ["s-a", "a", "greenhouse", *["leak"] * 10]

    live_check.restore(client, SPREADSHEET_ID, snapshot)

    restored = client.tabs["Job_Sources"][1]
    assert restored[:3] == ["s-a", "a", "greenhouse"]
    assert restored[3:] == [""] * (len(JOB_SOURCE_HEADERS) - 3)


# --------------------------------------------------------------------- fixtures


def test_the_fixture_covers_every_documented_scenario() -> None:
    fixture = live_check.build_fixture(as_of=AS_OF, run_id="run-1")

    statuses = {row.get("application_status") for row in fixture.opportunities}
    assert "Applied" in statuses
    assert "Ghosted" in statuses
    assert "" in statuses

    location_statuses = {row.get("location_status") for row in fixture.opportunities}
    assert "excluded" in location_statuses

    source_types = {row.get("source_type") for row in fixture.sources}
    assert "" in source_types
    assert "greenhouse" in source_types
    assert "linkedin_post_manual" in source_types

    ids = [row["opportunity_id"] for row in fixture.opportunities]
    assert len(ids) != len(set(ids)), "the fixture must include a duplicate ID"


def test_every_fixture_id_carries_the_run_id() -> None:
    fixture = live_check.build_fixture(as_of=AS_OF, run_id="run-1")

    for row in fixture.opportunities:
        assert row["opportunity_id"].startswith("run-1-")
    for row in fixture.sources:
        assert row["opportunity_id"].startswith("run-1-")


def test_the_fixture_expected_metrics_are_the_documented_numbers() -> None:
    fixture = live_check.build_fixture(as_of=AS_OF, run_id="run-1")
    metrics = live_check.expected_metrics(fixture, as_of=AS_OF)

    # Seven distinct opportunity IDs: eight rows, one duplicate ID.
    assert metrics["B6"] == 6  # seven distinct minus the excluded one
    assert metrics["B7"] == 1
    assert metrics["B8"] == 2  # only the two rows seen inside the window
    # Tracker metrics include excluded rows: applied, need-to-apply, excluded.
    assert metrics["B9"] == 3
    assert metrics["B10"] == 2  # applied (today+3) and interview (today+7)
    assert metrics["B14"] == 1  # Applied
    assert metrics["B22"] == 1  # Ghosted is recorded but unlisted
    assert metrics["B25"] == 1  # greenhouse, duplicate collapsed
    assert metrics["B26"] == 1  # lever
    assert metrics["B27"] == 1  # ashby
    assert metrics["B28"] == 1  # permitted_career_page
    assert metrics["B29"] == 1  # linkedin_post_manual
    assert metrics["B30"] == 2  # one orphan opportunity + one blank source type
    assert metrics["B33"] == 1
    assert metrics["B34"] == 1


def test_the_fixture_rows_match_the_canonical_column_order() -> None:
    fixture = live_check.build_fixture(as_of=AS_OF, run_id="run-1")

    opportunity_row = fixture.opportunity_rows()[0]
    source_row = fixture.source_rows()[0]

    assert len(opportunity_row) == len(OPPORTUNITY_HEADERS)
    assert opportunity_row[OPPORTUNITY_HEADERS.index("opportunity_id")] == "run-1-applied"
    assert len(source_row) == len(JOB_SOURCE_HEADERS)
    assert source_row[JOB_SOURCE_HEADERS.index("source_type")] == "greenhouse"


# ------------------------------------------------------------------ rate limits


def _rate_limited(retry_after: int | None = 3) -> GoogleSheetsError:
    return GoogleSheetsError(
        "rate_limited",
        "Google Sheets rate-limited the request.",
        retry_after_seconds=retry_after,
    )


def test_a_rate_limited_call_is_retried_and_succeeds() -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise _rate_limited()
        return "ok"

    result = live_check.with_retry(operation, sleep=sleeps.append)

    assert result == "ok"
    assert calls["count"] == 3
    assert sleeps == [3, 3]


def test_a_rate_limited_call_gives_up_after_the_last_attempt() -> None:
    with pytest.raises(GoogleSheetsError) as captured:
        live_check.with_retry(
            lambda: (_ for _ in ()).throw(_rate_limited()), attempts=2, sleep=lambda _: None
        )

    assert captured.value.code == "rate_limited"


def test_a_retry_without_a_hint_uses_increasing_backoff() -> None:
    sleeps: list[float] = []
    calls = {"count": 0}

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise _rate_limited(retry_after=None)
        return "ok"

    live_check.with_retry(operation, backoff_seconds=2.0, sleep=sleeps.append)

    assert sleeps == [2.0, 4.0]


def test_a_non_rate_limit_error_is_never_retried() -> None:
    calls = {"count": 0}

    def operation() -> str:
        calls["count"] += 1
        raise GoogleSheetsError("permission_denied", "no access")

    with pytest.raises(GoogleSheetsError) as captured:
        live_check.with_retry(operation, sleep=lambda _: None)

    assert captured.value.code == "permission_denied"
    assert calls["count"] == 1


def test_restore_retries_a_rate_limited_read_instead_of_abandoning() -> None:
    client = FakeClient(_header_only())
    snapshot = live_check.capture(client, SPREADSHEET_ID)
    client.tabs["Opportunities"].append(["opp-1"])

    original = client.get_values
    state = {"failed": False}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if not state["failed"]:
            state["failed"] = True
            raise _rate_limited()
        return original(*args, **kwargs)

    client.get_values = flaky  # type: ignore[method-assign]
    live_check.restore(client, SPREADSHEET_ID, snapshot, sleep=lambda _: None)

    data_rows = client.tabs["Opportunities"][OPPORTUNITY_HEADER_ROW:]
    assert all(not any(row) for row in data_rows)


# ---------------------------------------------------------------------- polling


def test_polling_stops_as_soon_as_two_reads_agree() -> None:
    reads = iter([("a",), ("b",), ("b",)])

    value, attempts, stable = live_check.wait_until_settled(
        lambda: next(reads), attempts=5, sleep=lambda _: None
    )

    assert value == ("b",)
    assert attempts == 3
    assert stable is True


def test_polling_is_bounded_and_reports_instability() -> None:
    counter = {"value": 0}

    def read() -> tuple[int, ...]:
        counter["value"] += 1
        return (counter["value"],)

    value, attempts, stable = live_check.wait_until_settled(read, attempts=3, sleep=lambda _: None)

    assert attempts == 3
    assert stable is False
    assert value == (3,)


def test_polling_requires_at_least_one_attempt() -> None:
    with pytest.raises(live_check.LiveCheckError):
        live_check.wait_until_settled(lambda: (), attempts=0)


def test_polling_stops_immediately_when_the_predicate_is_satisfied() -> None:
    calls = {"count": 0}

    def read() -> tuple[str, ...]:
        calls["count"] += 1
        return ("correct",)

    value, attempts, stable = live_check.wait_until_settled(
        read, is_settled=lambda current: current == ("correct",), attempts=5
    )

    assert value == ("correct",)
    assert attempts == 1
    assert stable is True
    assert calls["count"] == 1


def test_polling_keeps_trying_a_stable_but_wrong_read_until_the_deadline() -> None:
    value, attempts, stable = live_check.wait_until_settled(
        lambda: ("wrong",),
        is_settled=lambda current: current == ("correct",),
        attempts=3,
        sleep=lambda _: None,
    )

    assert value == ("wrong",)
    assert attempts == 3
    assert stable is False


def test_polling_never_sleeps_after_the_last_attempt() -> None:
    sleeps: list[float] = []

    live_check.wait_until_settled(
        lambda: (id(sleeps), len(sleeps)),
        attempts=3,
        interval_seconds=0.5,
        sleep=sleeps.append,
    )

    assert len(sleeps) == 2


def test_every_fixture_source_row_has_an_identity() -> None:
    """``source_id`` is the column the metric row bound reads.

    A blank one pins every source range to row 2, so only the first source would
    ever be counted. This was a live failure, not a theoretical one.
    """

    fixture = live_check.build_fixture(as_of=AS_OF, run_id="run-1")
    for record in fixture.sources:
        assert record.get("source_id"), record


def test_every_scenario_source_row_has_an_identity() -> None:
    scenarios = live_check.build_scenarios(as_of=AS_OF, run_id="run-1")
    for name, fixture in scenarios.items():
        for record in fixture.sources:
            assert record.get("source_id"), f"{name}: {record}"


def test_every_scenario_opportunity_row_has_an_identity() -> None:
    scenarios = live_check.build_scenarios(as_of=AS_OF, run_id="run-1")
    for name, fixture in scenarios.items():
        for record in fixture.opportunities:
            assert record.get("opportunity_id"), f"{name}: {record}"
