"""Tests for the ``Dashboard`` summary-card layout.

The cards are the numbers a user reads without opening the app, so the assertions
here are about what the formula *does*: which columns it reads, that it guards a
zero match before ``FILTER``, that it counts distinct opportunities, and that it
never reaches outside the canonical tabs.
"""

from __future__ import annotations

import pytest

from personal_job_finder_api.integrations.google_sheets import workspace_layout as layout

DIALECTS = ("en_US", "id_ID", "in_ID")


def _cells(locale: str = "en_US") -> dict[str, str]:
    dialect = layout.metrics.FORMULA_DIALECTS[layout.metrics.LOCALE_ALIASES.get(locale, locale)]
    return {cell.address: cell.content for cell in layout.all_cells(dialect)}


# ------------------------------------------------------------------- identity


def test_identity_constants() -> None:
    assert layout.DASHBOARD_TAB == "Dashboard"
    assert layout.DASHBOARD_SENTINEL == "PJF_DASHBOARD_CARDS_V1"
    assert layout.SENTINEL_CELL == "M1"


def test_the_cards_sentinel_differs_from_the_metric_board() -> None:
    """Two tabs, two layouts, so one sentinel must not vouch for both."""

    assert layout.DASHBOARD_SENTINEL != layout.metrics.METRICS_SENTINEL


def test_the_sentinel_is_not_written_with_the_visible_cells() -> None:
    assert layout.SENTINEL_CELL not in {cell.address for cell in layout.visible_cells()}
    assert layout.sentinel_cell().content == layout.DASHBOARD_SENTINEL


# --------------------------------------------------------------------- cards


def test_every_card_has_a_label_a_formula_and_a_caption() -> None:
    cells = _cells()

    for offset, label in enumerate(layout.CARD_LABELS):
        row = 3 + offset
        assert cells[f"A{row}"] == label
        assert cells[f"B{row}"].startswith("=")
        assert cells[f"C{row}"]


def test_the_as_of_cell_is_a_formula() -> None:
    assert _cells()["B2"].startswith("=TEXT(YEAR(TODAY())")


def test_every_card_counts_distinct_opportunities() -> None:
    cells = _cells()

    for address in layout.CARD_CELLS:
        formula = cells[address]
        assert "COUNTUNIQUE(" in formula, address
        assert "COUNTA(" not in formula, address


def test_every_card_guards_a_zero_match_before_filter() -> None:
    """Without the guard, ``FILTER``'s ``#N/A`` counts as one value."""

    cells = _cells()

    for address in layout.CARD_CELLS:
        formula = cells[address]
        assert "SUMPRODUCT(" in formula, address
        guard = formula.index("SUMPRODUCT(")
        filtered = formula.index("FILTER(")
        assert guard < filtered, address


def test_no_card_catches_a_genuine_error() -> None:
    """An outer ``IFERROR`` would turn a broken reference into a plausible zero."""

    cells = _cells()

    for address in layout.CARD_CELLS:
        assert not cells[address].startswith("=IFERROR("), address


def test_the_need_to_apply_card_counts_blank_and_literal_statuses() -> None:
    formula = _cells()["B3"]

    assert "TRIM(TO_TEXT(" in formula
    assert '"need to apply"' in formula
    # Two disjoint groups, added.
    assert formula.count("COUNTUNIQUE(") == 2
    assert "+IF(" in formula


def test_the_applied_card_uses_the_seven_day_window() -> None:
    formula = _cells()["B4"]

    assert "TODAY()-6" in formula
    assert "TODAY()" in formula


def test_the_interview_card_looks_forward_seven_days() -> None:
    formula = _cells()["B5"]

    assert "TODAY()" in formula
    assert "TODAY()+7" in formula


def test_the_offers_card_matches_the_offer_status() -> None:
    assert '"offer"' in _cells()["B6"]


def test_active_excludes_both_archived_and_excluded() -> None:
    formula = _cells()["B7"]

    assert '"excluded"' in formula
    assert '"ignored"' in formula


def test_archived_counts_either_reason() -> None:
    formula = _cells()["B8"]

    assert '"excluded"' in formula
    assert '"ignored"' in formula
    assert ">0" in formula


# ------------------------------------------------------------------- bounds


def test_the_cards_carry_their_own_row_bound() -> None:
    """The cards must not depend on the optional metric board."""

    cells = _cells()

    assert cells[layout.BOUND_CELL].startswith("=IFERROR(MAX(2,MAX(FILTER(ROW(")
    for address in layout.CARD_CELLS:
        assert "$H$1" in cells[address], address


def test_the_row_bound_never_uses_the_excel_lookup_idiom() -> None:
    formula = _cells()[layout.BOUND_CELL]

    assert "LOOKUP" not in formula
    assert "COUNTA" not in formula


# ------------------------------------------------------------------ safety


@pytest.mark.parametrize("locale", DIALECTS)
def test_formulas_are_parenthesis_balanced(locale: str) -> None:
    """A literal-aware scan: a quote inside a regex must not confuse the count."""

    for address, content in _cells(locale).items():
        if not content.startswith("="):
            continue
        depth = 0
        in_string = False
        index = 0
        while index < len(content):
            character = content[index]
            if character == '"':
                if in_string and index + 1 < len(content) and content[index + 1] == '"':
                    index += 2
                    continue
                in_string = not in_string
            elif not in_string:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    assert depth >= 0, f"{address} closes before it opens"
            index += 1
        assert depth == 0, f"{address} is unbalanced"
        assert not in_string, f"{address} leaves a string open"


@pytest.mark.parametrize("locale", DIALECTS)
def test_only_canonical_tabs_are_referenced(locale: str) -> None:
    for address, content in _cells(locale).items():
        if not content.startswith("="):
            continue
        for forbidden in ("IMPORTDATA", "IMPORTRANGE", "IMPORTXML", "IMPORTHTML"):
            assert forbidden not in content, address


def test_the_cards_never_reference_the_metric_board() -> None:
    for address, content in _cells().items():
        if not content.startswith("="):
            continue
        assert f"{layout.metrics.METRICS_TAB}!" not in content, address


@pytest.mark.parametrize("locale", DIALECTS)
def test_the_dialect_separator_is_used(locale: str) -> None:
    cells = _cells(locale)
    separator = layout.metrics.FORMULA_DIALECTS[
        layout.metrics.LOCALE_ALIASES.get(locale, locale)
    ].argument_separator

    assert separator in cells["B3"]
    if separator == ";":
        # No comma may survive outside a quoted literal.
        for content in cells.values():
            if not content.startswith("="):
                continue
            outside = []
            in_string = False
            for character in content:
                if character == '"':
                    in_string = not in_string
                elif not in_string:
                    outside.append(character)
            assert "," not in "".join(outside), content[:60]


# --------------------------------------------------------------- requests


def test_the_initialization_batch_writes_the_sentinel_last() -> None:
    requests = layout.build_initialization_requests(7)

    assert requests
    assert "updateCells" in requests[0]
    sentinel = requests[-1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]
    assert sentinel == {"stringValue": layout.DASHBOARD_SENTINEL}


def test_every_request_targets_the_cards_sheet_only() -> None:
    for request in layout.build_initialization_requests(7):
        body = next(iter(request.values()))
        if "start" in body:
            assert body["start"]["sheetId"] == 7
        elif "range" in body:
            assert body["range"]["sheetId"] == 7


def test_the_helper_column_is_hidden() -> None:
    requests = layout.build_format_requests(7)

    hidden = [
        request["updateDimensionProperties"]
        for request in requests
        if "updateDimensionProperties" in request
        and request["updateDimensionProperties"]["properties"].get("hiddenByUser")
    ]
    assert hidden
    assert hidden[0]["range"]["startIndex"] == layout.metrics.column_index("H")


def test_metric_cells_are_the_card_values() -> None:
    assert layout.metric_cells() == ("B3", "B4", "B5", "B6", "B7", "B8")
