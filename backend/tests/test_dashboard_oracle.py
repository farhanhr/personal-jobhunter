"""Tests for the independent Dashboard oracle.

Every expected number here is computed by hand from the rules, not by calling the
formula builders. The oracle exists to disagree with
:mod:`personal_job_finder_api.integrations.google_sheets.dashboard_layout`, so a
test that derived its expectation from that module would prove nothing.
"""

from __future__ import annotations

from datetime import date

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout
from personal_job_finder_api.integrations.google_sheets import dashboard_oracle as oracle

AS_OF = date(2026, 11, 20)


def _opportunity(**overrides: object) -> oracle.OpportunityRow:
    base: dict[str, object] = {
        "opportunity_id": "opp-1",
        "location_status": "eligible",
        "first_seen_at_utc": "2026-11-01T03:00:00.000000Z",
        "date_applied": "",
        "application_status": "",
        "interview_date": "",
    }
    base.update(overrides)
    return oracle.OpportunityRow(**base)  # type: ignore[arg-type]


def _source(opportunity_id: str | None, source_type: str | None) -> oracle.SourceRow:
    return oracle.SourceRow(opportunity_id=opportunity_id, source_type=source_type)


# --------------------------------------------------------------- empty cases


def test_header_only_data_yields_zero_everywhere() -> None:
    metrics = oracle.header_only(as_of=AS_OF)

    for address in (
        "B6",
        "B7",
        "B8",
        "B9",
        "B10",
        "B13",
        "B14",
        "B15",
        "B16",
        "B17",
        "B18",
        "B19",
        "B20",
        "B21",
        "B22",
        "B25",
        "B26",
        "B27",
        "B28",
        "B29",
        "B30",
        "J1",
    ):
        assert metrics[address] == 0, address
    assert metrics["B2"] == "2026-11-20"


def test_an_empty_id_row_contributes_nothing() -> None:
    metrics = oracle.compute(
        [_opportunity(opportunity_id="", application_status="Applied")],
        [],
        as_of=AS_OF,
    )

    assert metrics["B6"] == 0
    assert metrics["B14"] == 0
    assert metrics["B21"] == 0
    assert metrics["J1"] == 0


def test_a_row_with_an_id_but_no_matching_status_counts_only_where_it_matches() -> None:
    metrics = oracle.compute([_opportunity(application_status="Applied")], [], as_of=AS_OF)

    assert metrics["B6"] == 1
    assert metrics["B14"] == 1
    assert metrics["B13"] == 0
    assert metrics["B21"] == 0
    assert metrics["B22"] == 0
    assert metrics["J1"] == 1


# ------------------------------------------------------------ totals/exclusion


def test_excluded_opportunities_are_removed_only_from_the_excluded_metrics() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-1"),
            _opportunity(opportunity_id="opp-2", location_status="excluded"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B6"] == 1
    assert metrics["B7"] == 1
    # Excluded rows still count for tracker metrics, exactly like the backend.
    assert metrics["B21"] == 2
    assert metrics["J1"] == 0


def test_duplicate_ids_are_counted_once() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-1", application_status="Applied"),
            _opportunity(opportunity_id="opp-1", application_status="Applied"),
            _opportunity(opportunity_id="opp-2", application_status="Applied"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B6"] == 2
    assert metrics["B14"] == 2


def test_multiple_matching_rows_are_all_counted() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-1", application_status="Applied"),
            _opportunity(opportunity_id="opp-2", application_status=" applied "),
            _opportunity(opportunity_id="opp-3", application_status="APPLIED"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B14"] == 3


# ------------------------------------------------------------------- statuses


def test_every_suggested_status_maps_to_its_own_row() -> None:
    rows = [
        _opportunity(opportunity_id=f"opp-{index}", application_status=label)
        for index, label in enumerate(oracle.APPLICATION_STATUS_VALUES)
    ]

    metrics = oracle.compute(rows, [], as_of=AS_OF)

    for index, _label in enumerate(oracle.APPLICATION_STATUS_VALUES):
        assert metrics[f"B{13 + index}"] == 1
    assert metrics["B21"] == 0
    assert metrics["B22"] == 0
    assert metrics["J1"] == 8


def test_a_free_text_status_lands_in_other_not_in_a_label_row() -> None:
    metrics = oracle.compute([_opportunity(application_status="Ghosted")], [], as_of=AS_OF)

    assert sum(metrics[f"B{row}"] for row in range(13, 21)) == 0
    assert metrics["B21"] == 0
    assert metrics["J1"] == 1
    assert metrics["B22"] == 1


def test_blank_and_whitespace_only_status_is_blank_not_other() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-1", application_status=""),
            _opportunity(opportunity_id="opp-2", application_status="   "),
            _opportunity(opportunity_id="opp-3", application_status="Applied"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B21"] == 2
    assert metrics["J1"] == 1
    assert metrics["B22"] == 0
    assert metrics["B14"] == 1


def test_sheets_trim_collapses_inner_spaces_but_keeps_non_breaking_spaces() -> None:
    assert oracle.normalize_status("Need  to Apply") == "need to apply"
    assert oracle.normalize_status("  applied  ") == "applied"
    assert oracle.normalize_status("\u00a0") == "\u00a0"
    assert oracle.normalize_status("\u00a0") != ""


def test_a_non_breaking_space_only_status_is_recorded_and_goes_to_other() -> None:
    metrics = oracle.compute([_opportunity(application_status="\u00a0")], [], as_of=AS_OF)

    assert metrics["B21"] == 0
    assert metrics["J1"] == 1
    assert metrics["B22"] == 1


# --------------------------------------------------------------- date windows


def test_iso_and_day_first_dates_are_both_accepted() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-1", date_applied="2026-11-20"),
            _opportunity(opportunity_id="opp-2", date_applied="19/11/2026"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B9"] == 2
    assert metrics.unreadable_date_applied == 0


def test_a_trailing_parenthetical_annotation_is_ignored() -> None:
    metrics = oracle.compute([_opportunity(date_applied="20/11/2026 (pagi)")], [], as_of=AS_OF)

    assert metrics["B9"] == 1
    assert metrics.unreadable_date_applied == 0


def test_an_invalid_calendar_date_is_unreadable_and_not_counted() -> None:
    metrics = oracle.compute([_opportunity(date_applied="31/02/2026")], [], as_of=AS_OF)

    assert metrics["B9"] == 0
    assert metrics.unreadable_date_applied == 1


def test_a_blank_tracker_date_is_not_reported_as_invalid() -> None:
    metrics = oracle.compute([_opportunity(date_applied="")], [], as_of=AS_OF)

    assert metrics["B9"] == 0
    assert metrics.unreadable_date_applied == 0


def test_applications_window_is_inclusive_over_seven_jakarta_dates() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="in-start", date_applied="2026-11-14"),
            _opportunity(opportunity_id="in-end", date_applied="2026-11-20"),
            _opportunity(opportunity_id="too-early", date_applied="2026-11-13"),
            _opportunity(opportunity_id="future", date_applied="2026-11-21"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B9"] == 2


def test_interviews_window_runs_from_today_to_today_plus_seven() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="today", interview_date="2026-11-20"),
            _opportunity(opportunity_id="last-day", interview_date="2026-11-27"),
            _opportunity(opportunity_id="past", interview_date="2026-11-19"),
            _opportunity(opportunity_id="too-far", interview_date="2026-11-28"),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B10"] == 2


def test_new_opportunities_use_jakarta_midnight_boundaries_in_utc() -> None:
    # Jakarta midnight on 2026-11-14 is 2026-11-13T17:00:00Z, and the exclusive
    # end is Jakarta midnight on 2026-11-21 = 2026-11-20T17:00:00Z.
    metrics = oracle.compute(
        [
            _opportunity(
                opportunity_id="first-instant",
                first_seen_at_utc="2026-11-13T17:00:00.000000Z",
            ),
            _opportunity(
                opportunity_id="last-instant",
                first_seen_at_utc="2026-11-20T16:59:59.999999Z",
            ),
            _opportunity(
                opportunity_id="one-microsecond-early",
                first_seen_at_utc="2026-11-13T16:59:59.999999Z",
            ),
            _opportunity(
                opportunity_id="at-exclusive-end",
                first_seen_at_utc="2026-11-20T17:00:00.000000Z",
            ),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B8"] == 2


def test_a_non_canonical_timestamp_is_never_counted_as_new() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-1", first_seen_at_utc="2026-11-19"),
            _opportunity(opportunity_id="opp-2", first_seen_at_utc="2026-11-19T10:00:00Z"),
            _opportunity(opportunity_id="opp-3", first_seen_at_utc=""),
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B8"] == 0


def test_excluded_opportunities_are_never_new() -> None:
    metrics = oracle.compute(
        [
            _opportunity(
                opportunity_id="opp-1",
                location_status="excluded",
                first_seen_at_utc="2026-11-19T03:00:00.000000Z",
            )
        ],
        [],
        as_of=AS_OF,
    )

    assert metrics["B8"] == 0
    assert metrics["B7"] == 1


# -------------------------------------------------------------------- sources


def test_every_source_type_gets_its_own_count() -> None:
    rows = [_opportunity(opportunity_id=f"opp-{index}") for index in range(5)]
    sources = [
        _source(f"opp-{index}", source_type)
        for index, source_type in enumerate(oracle.SOURCE_TYPES)
    ]

    metrics = oracle.compute(rows, sources, as_of=AS_OF)

    for index, _source_type in enumerate(oracle.SOURCE_TYPES):
        assert metrics[f"B{25 + index}"] == 1
    assert metrics["B30"] == 0


def test_one_opportunity_with_several_sources_counts_once_per_type() -> None:
    sources = [
        _source("opp-1", "greenhouse"),
        _source("opp-1", "lever"),
        _source("opp-1", "greenhouse"),
    ]

    metrics = oracle.compute([_opportunity(opportunity_id="opp-1")], sources, as_of=AS_OF)

    assert metrics["B25"] == 1
    assert metrics["B26"] == 1
    assert metrics["B27"] == 0
    assert metrics["B30"] == 0


def test_an_orphan_source_row_is_not_counted_in_any_group() -> None:
    sources = [_source("missing-opp", "greenhouse")]

    metrics = oracle.compute([], sources, as_of=AS_OF)

    assert metrics["B25"] == 0
    assert metrics["B30"] == 0


def test_an_opportunity_without_any_source_is_unknown() -> None:
    metrics = oracle.compute([_opportunity(opportunity_id="opp-1")], [], as_of=AS_OF)

    assert metrics["B30"] == 1
    assert metrics["B6"] == 1


def test_a_blank_source_type_is_unknown_and_not_double_counted_as_orphan() -> None:
    metrics = oracle.compute(
        [_opportunity(opportunity_id="opp-1")],
        [_source("opp-1", "")],
        as_of=AS_OF,
    )

    assert metrics["B30"] == 1
    assert metrics["B25"] == 0


def test_orphans_and_blank_types_add_up_independently() -> None:
    metrics = oracle.compute(
        [
            _opportunity(opportunity_id="opp-orphan"),
            _opportunity(opportunity_id="opp-blank"),
        ],
        [_source("opp-blank", "")],
        as_of=AS_OF,
    )

    assert metrics["B30"] == 2


def test_a_source_pointing_at_an_excluded_opportunity_is_not_counted() -> None:
    metrics = oracle.compute(
        [_opportunity(opportunity_id="opp-1", location_status="excluded")],
        [_source("opp-1", "greenhouse")],
        as_of=AS_OF,
    )

    assert metrics["B25"] == 0
    assert metrics["B7"] == 1
    # The excluded opportunity is not an orphan either: exclusion is not "unknown".
    assert metrics["B30"] == 0


# ------------------------------------------------------------------- parity


def test_the_oracle_vocabularies_match_the_layout_vocabularies() -> None:
    assert oracle.SOURCE_TYPES == layout.SOURCE_TYPES
    assert oracle.APPLICATION_STATUS_VALUES == layout.APPLICATION_STATUS_VALUES


def test_the_oracle_covers_every_metric_cell_the_layout_writes() -> None:
    metric_addresses = {cell.address for cell in layout.all_cells() if cell.address.startswith("B")}
    computed = set(oracle.header_only(as_of=AS_OF).values)

    assert metric_addresses == computed - {"J1"}
    assert "J1" in computed
