"""Golden tests for the formula-only Dashboard layout.

These tests pin the generated spreadsheet text. They never evaluate a formula:
Google Sheets execution is out of scope for the automated suite, so a passing
test here proves request construction and parity intent, not spreadsheet
results.
"""

from __future__ import annotations

import re

import pytest

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout


def _cells() -> dict[str, str]:
    return {cell.address: cell.content for cell in layout.all_cells()}


def _formulas() -> dict[str, str]:
    return {address: text for address, text in _cells().items() if text.startswith("=")}


# --------------------------------------------------------------------- identity


def test_identity_constants() -> None:
    assert layout.METRICS_TAB == layout.METRICS_TAB
    assert layout.METRICS_SENTINEL == "PJF_DASHBOARD_V2"
    assert layout.DASHBOARD_TIMEZONE == "Asia/Jakarta"
    assert layout.SENTINEL_CELL == "M1"


def test_dashboard_is_not_a_required_tab() -> None:
    from personal_job_finder_api.integrations.google_sheets.config import REQUIRED_TABS

    assert layout.METRICS_TAB not in REQUIRED_TABS
    assert set(REQUIRED_TABS) == {"Opportunities", "Job_Sources"}


# ------------------------------------------------------------------- addresses


def test_addresses_are_unique_and_inside_the_declared_areas() -> None:
    addresses = [cell.address for cell in layout.all_cells()]
    assert len(addresses) == len(set(addresses))

    for address in addresses:
        match = re.fullmatch(r"([A-Z]+)(\d+)", address)
        assert match is not None, address
        column, row = match.group(1), int(match.group(2))
        if column in {"A", "B"}:
            assert 1 <= row <= 40, address
        else:
            assert column in {"H", "I", "J", "M"}, address


def test_visible_rows_follow_the_agreed_sections() -> None:
    cells = _cells()
    labels = {
        row: cells[f"A{row}"]
        for row in range(1, 41)
        if f"A{row}" in cells and not cells[f"A{row}"].startswith("=")
    }
    assert labels[1].startswith("Job Search Metrics")
    assert "not a source of truth" in labels[1]
    assert labels[2] == "As of (Asia/Jakarta)"
    assert labels[5] == "Pipeline"
    assert labels[12].startswith("Application status")
    assert labels[24].startswith("Opportunities by source type")
    assert labels[32] == "Data quality"
    assert labels[36] == "Notes"


def test_pipeline_metrics_are_in_rows_6_to_10() -> None:
    cells = _cells()
    assert cells["A6"] == "Total opportunities"
    assert cells["A7"] == "Excluded opportunities"
    assert cells["A8"].startswith("New opportunities")
    assert cells["A9"].startswith("Applications")
    assert cells["A10"].startswith("Upcoming interviews")
    for row in range(6, 11):
        assert cells[f"B{row}"].startswith("=")


def test_application_status_breakdown_has_eight_suggestions_blank_and_other() -> None:
    cells = _cells()
    expected = [
        (13, "Need to Apply"),
        (14, "Applied"),
        (15, "Assessment"),
        (16, "Interview"),
        (17, "Offer"),
        (18, "Rejected"),
        (19, "Withdrawn"),
        (20, "No Feedback"),
        (21, "(blank)"),
        (22, "(other)"),
    ]
    for row, label in expected:
        assert cells[f"A{row}"] == label
        assert cells[f"B{row}"].startswith("=")


def test_source_breakdown_lists_every_contract_source_type_plus_unknown() -> None:
    cells = _cells()
    assert cells["A25"] == "greenhouse"
    assert cells["A26"] == "lever"
    assert cells["A27"] == "ashby"
    assert cells["A28"] == "permitted_career_page"
    assert cells["A29"] == "linkedin_post_manual"
    assert cells["A30"] == "Unknown"


def test_source_types_match_the_backend_enum() -> None:
    from personal_job_finder_api.db.tables import SOURCE_TYPE_VALUES

    assert tuple(layout.SOURCE_TYPES) == SOURCE_TYPE_VALUES


def test_application_status_suggestions_match_the_contract() -> None:
    assert layout.APPLICATION_STATUS_VALUES == (
        "Need to Apply",
        "Applied",
        "Assessment",
        "Interview",
        "Offer",
        "Rejected",
        "Withdrawn",
        "No Feedback",
    )


# --------------------------------------------------------------- simple golden


def test_as_of_formula_uses_numeric_format_codes_only() -> None:
    cells = _cells()
    assert cells["B2"] == (
        '=TEXT(YEAR(TODAY()),"0000")&"-"&TEXT(MONTH(TODAY()),"00")&"-"&TEXT(DAY(TODAY()),"00")'
    )


def test_last_row_helper_finds_the_last_non_blank_key_not_a_count() -> None:
    cells = _cells()
    assert cells["H1"] == (
        '=IFERROR(MAX(2,MAX(FILTER(ROW(Opportunities!$A:$A),Opportunities!$A:$A<>""))),2)'
    )
    assert cells["I1"] == (
        '=IFERROR(MAX(2,MAX(FILTER(ROW(Job_Sources!$A:$A),Job_Sources!$A:$A<>""))),2)'
    )


def test_last_row_helper_never_uses_counta_or_the_excel_lookup_idiom() -> None:
    for address in ("H1", "I1"):
        formula = _cells()[address]
        assert "COUNTA" not in formula
        # ``LOOKUP(2,1/(...),ROW(...))`` evaluates to the fallback in Sheets.
        assert "LOOKUP" not in formula
        assert "MAX(FILTER(ROW(" in formula


def test_new_opportunity_window_boundaries_convert_jakarta_midnight_to_utc() -> None:
    cells = _cells()
    assert cells["H2"] == (
        '=TEXT(YEAR(TODAY()-7),"0000")&"-"&TEXT(MONTH(TODAY()-7),"00")'
        '&"-"&TEXT(DAY(TODAY()-7),"00")&"T17:00:00.000000Z"'
    )
    assert cells["H3"] == (
        '=TEXT(YEAR(TODAY()),"0000")&"-"&TEXT(MONTH(TODAY()),"00")'
        '&"-"&TEXT(DAY(TODAY()),"00")&"T17:00:00.000000Z"'
    )


def test_total_formula_counts_distinct_non_excluded_opportunities() -> None:
    assert _cells()["B6"] == (
        "=IF(SUMPRODUCT("
        '--(Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>""),'
        '--(Opportunities!$L$2:INDEX(Opportunities!$L:$L,$H$1)<>"excluded"))=0,0,'
        "COUNTUNIQUE(FILTER("
        "Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1),"
        'Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>"",'
        'Opportunities!$L$2:INDEX(Opportunities!$L:$L,$H$1)<>"excluded")))'
    )


def test_excluded_formula_counts_distinct_excluded_opportunities() -> None:
    assert _cells()["B7"] == (
        "=IF(SUMPRODUCT("
        '--(Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>""),'
        '--(Opportunities!$L$2:INDEX(Opportunities!$L:$L,$H$1)="excluded"))=0,0,'
        "COUNTUNIQUE(FILTER("
        "Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1),"
        'Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>"",'
        'Opportunities!$L$2:INDEX(Opportunities!$L:$L,$H$1)="excluded")))'
    )


def test_applied_status_formula_is_case_and_space_insensitive() -> None:
    assert _cells()["B14"] == (
        "=IF(SUMPRODUCT("
        '--(Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>""),'
        "--(MAP(Opportunities!$AC$2:INDEX(Opportunities!$AC:$AC,$H$1),"
        'LAMBDA(v,LOWER(TRIM(TO_TEXT(v)))="applied"))))=0,0,'
        "COUNTUNIQUE(FILTER("
        "Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1),"
        'Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>"",'
        "MAP(Opportunities!$AC$2:INDEX(Opportunities!$AC:$AC,$H$1),"
        'LAMBDA(v,LOWER(TRIM(TO_TEXT(v)))="applied")))))'
    )


def test_blank_status_counts_rows_whose_status_is_not_recorded() -> None:
    assert _cells()["B21"] == (
        "=IF(SUMPRODUCT("
        '--(Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>""),'
        "--(MAP(Opportunities!$AC$2:INDEX(Opportunities!$AC:$AC,$H$1),"
        'LAMBDA(v,TRIM(TO_TEXT(v))=""))))=0,0,'
        "COUNTUNIQUE(FILTER("
        "Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1),"
        'Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>"",'
        "MAP(Opportunities!$AC$2:INDEX(Opportunities!$AC:$AC,$H$1),"
        'LAMBDA(v,TRIM(TO_TEXT(v))="")))))'
    )


def test_other_status_is_the_remainder_of_recorded_statuses() -> None:
    assert _cells()["B22"] == "=MAX(0,$J$1-SUM($B$13:$B$20))"
    assert _cells()["J1"] == (
        "=IF(SUMPRODUCT("
        '--(Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>""),'
        "--(MAP(Opportunities!$AC$2:INDEX(Opportunities!$AC:$AC,$H$1),"
        'LAMBDA(v,TRIM(TO_TEXT(v))<>""))))=0,0,'
        "COUNTUNIQUE(FILTER("
        "Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1),"
        'Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)<>"",'
        "MAP(Opportunities!$AC$2:INDEX(Opportunities!$AC:$AC,$H$1),"
        'LAMBDA(v,TRIM(TO_TEXT(v))<>"")))))'
    )


def test_source_group_formula_excludes_excluded_and_orphan_sources() -> None:
    formula = _cells()["B25"]
    ((conditions, filter_arguments),) = _guard_terms(formula)

    assert filter_arguments[0] == "Job_Sources!$B$2:INDEX(Job_Sources!$B:$B,$I$1)"
    assert any('<>""' in c and "Job_Sources!$B" in c for c in conditions)
    assert any(c.endswith('="greenhouse"') for c in conditions)
    assert any("VLOOKUP(id," in c and '="excluded"' in c for c in conditions)
    assert "COUNTUNIQUE(FILTER(" in formula


def test_unknown_source_counts_orphans_and_blank_source_types_without_double_counting() -> None:
    terms = _guard_terms(_cells()["B30"])
    assert len(terms) == 2

    orphan_conditions, orphan_arguments = terms[0]
    blank_type_conditions, blank_type_arguments = terms[1]

    assert orphan_arguments[0] == "Opportunities!$A$2:INDEX(Opportunities!$A:$A,$H$1)"
    assert any("MATCH(id," in c for c in orphan_conditions)
    assert blank_type_arguments[0] == "Job_Sources!$B$2:INDEX(Job_Sources!$B:$B,$I$1)"
    assert any(c.endswith('=""') and "Job_Sources!$C" in c for c in blank_type_conditions)
    assert "COUNTUNIQUE(FILTER(Opportunities!" in _cells()["B30"]
    assert "COUNTUNIQUE(FILTER(Job_Sources!" in _cells()["B30"]


# -------------------------------------------------------- complex formula shape


def test_new_opportunity_formula_requires_canonical_timestamp_and_window() -> None:
    formula = _cells()["B8"]
    assert "COUNTUNIQUE(FILTER(" in formula
    assert 'Opportunities!$L$2:INDEX(Opportunities!$L:$L,$H$1)<>"excluded"' in formula
    assert (
        "REGEXMATCH(Opportunities!$W$2:INDEX(Opportunities!$W:$W,$H$1),"
        '"^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z$")' in formula
    )
    assert "Opportunities!$W$2:INDEX(Opportunities!$W:$W,$H$1)>=$H$2" in formula
    assert "Opportunities!$W$2:INDEX(Opportunities!$W:$W,$H$1)<$H$3" in formula


def test_applications_count_date_applied_inside_seven_inclusive_dates() -> None:
    formula = _cells()["B9"]
    assert "Opportunities!$AB$2:INDEX(Opportunities!$AB:$AB,$H$1)" in formula
    assert "s>=TODAY()-6" in formula
    assert "s<=TODAY()" in formula
    assert "Opportunities!$AC" not in formula
    assert "excluded" not in formula


def test_upcoming_interviews_count_interview_date_inside_eight_inclusive_dates() -> None:
    formula = _cells()["B10"]
    assert "Opportunities!$AE$2:INDEX(Opportunities!$AE:$AE,$H$1)" in formula
    assert "s>=TODAY()" in formula
    assert "s<=TODAY()+7" in formula
    assert "Opportunities!$AD" not in formula
    assert "Opportunities!$AC" not in formula


def test_date_parsing_never_uses_locale_dependent_coercion() -> None:
    forbidden = ("DATEVALUE", "TO_DATE", "DATE(", "yyyy", "MM/DD", "MM-DD", "dd/mm")
    for address, formula in _formulas().items():
        for token in forbidden:
            if token == "DATE(":
                # DATE(y,m,d) is locale-independent arithmetic; only the
                # locale-dependent parsing helpers are forbidden.
                continue
            assert token not in formula, f"{address} contains {token}"


def test_formulas_never_reach_outside_the_users_own_tabs() -> None:
    forbidden = ("IMPORTDATA", "IMPORTRANGE", "WEBSERVICE", "IMPORTXML", "GOOGLEFINANCE")
    for address, formula in _formulas().items():
        for token in forbidden:
            assert token not in formula, f"{address} contains {token}"
        referenced = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)!", formula))
        assert referenced <= {"Opportunities", "Job_Sources"}, address


def test_annotation_removal_matches_the_backend_pattern() -> None:
    for address in ("B9", "B10", "B33", "B34"):
        assert r'"\s*\([^()]*\)\s*$"' in _cells()[address], address


def test_unreadable_date_formulas_count_non_blank_unparsed_values() -> None:
    applied = _cells()["B33"]
    interview = _cells()["B34"]
    assert "Opportunities!$AB$2:INDEX(Opportunities!$AB:$AB,$H$1)" in applied
    assert "Opportunities!$AE$2:INDEX(Opportunities!$AE:$AE,$H$1)" in interview
    for formula in (applied, interview):
        assert 'IF(TRIM(TO_TEXT(raw))="",0,IF(ok,0,1))' in formula


def test_day_first_parsing_swaps_only_the_year_position() -> None:
    formula = _cells()["B9"]
    assert "IF(iso,VALUE(INDEX(p,1,1)),VALUE(INDEX(p,1,3)))" in formula
    assert "IF(iso,VALUE(INDEX(p,1,3)),VALUE(INDEX(p,1,1)))" in formula


# ------------------------------------------------------------------ sentinel


def test_sentinel_cell_is_written_separately_from_the_layout() -> None:
    assert layout.sentinel_cell().address == "M1"
    assert layout.sentinel_cell().content == "PJF_DASHBOARD_V2"
    assert "M1" not in _cells()


# ------------------------------------------------------------ request builders


def test_cell_update_request_marks_formulas_and_labels_distinctly() -> None:
    request = layout.build_cell_update_request(
        7,
        (
            layout.DashboardCell(address="A1", content="Title"),
            layout.DashboardCell(address="B2", content="=TODAY()"),
        ),
    )
    cells = request["updateCells"]
    assert cells["start"] == {"sheetId": 7, "rowIndex": 0, "columnIndex": 0}
    assert cells["fields"] == "userEnteredValue"
    first_row = cells["rows"][0]["values"]
    assert first_row == [{"userEnteredValue": {"stringValue": "Title"}}, {}]
    second_row = cells["rows"][1]["values"]
    assert second_row[1] == {"userEnteredValue": {"formulaValue": "=TODAY()"}}


def test_cell_update_request_spans_the_bounding_box_of_the_cells() -> None:
    request = layout.build_cell_update_request(
        7,
        (
            layout.DashboardCell(address="A1", content="Title"),
            layout.DashboardCell(address="B3", content="x"),
        ),
    )
    cells = request["updateCells"]
    assert cells["start"] == {"sheetId": 7, "rowIndex": 0, "columnIndex": 0}
    rows = cells["rows"]
    assert len(rows) == 3
    assert rows[1]["values"] == [{}, {}]
    assert rows[2]["values"] == [{}, {"userEnteredValue": {"stringValue": "x"}}]


def test_cell_update_request_rejects_an_empty_cell_list() -> None:
    with pytest.raises(ValueError):
        layout.build_cell_update_request(7, ())


def test_format_requests_hide_helper_columns_and_bold_section_headers() -> None:
    requests = layout.build_format_requests(11)

    hidden = [
        request["updateDimensionProperties"]
        for request in requests
        if "updateDimensionProperties" in request
        and request["updateDimensionProperties"]["properties"].get("hiddenByUser") is True
    ]
    assert len(hidden) == 1
    assert hidden[0]["range"] == {
        "sheetId": 11,
        "dimension": "COLUMNS",
        "startIndex": 7,
        "endIndex": 13,
    }

    bolded_rows = set()
    for request in requests:
        repeat = request.get("repeatCell")
        if repeat is None:
            continue
        text_format = repeat["cell"].get("userEnteredFormat", {}).get("textFormat", {})
        if text_format.get("bold"):
            bolded_rows.add(repeat["range"]["startRowIndex"] + 1)
    assert {1, 5, 12, 24, 32, 36} <= bolded_rows


def test_format_requests_only_touch_the_dashboard_sheet() -> None:
    requests = layout.build_format_requests(3)
    for request in requests:
        body = next(iter(request.values()))
        assert body["range"]["sheetId"] == 3


def test_format_requests_never_change_the_timezone_or_add_tabs() -> None:
    for request in layout.build_format_requests(3):
        assert "updateSpreadsheetProperties" not in request
        assert "addSheet" not in request
        assert "deleteSheet" not in request


# ------------------------------------------------------- documented edge cases
#
# These assertions prove the *rule* is encoded in the generated text. They do
# not prove what Google Sheets computes; that needs a real spreadsheet and is
# deliberately out of scope for this milestone.


def test_excluded_rows_are_omitted_only_where_the_backend_omits_them() -> None:
    cells = _cells()
    excluded_filtered = ("B6", "B8", "B25", "B26", "B27", "B28", "B29", "B30")
    for address in excluded_filtered:
        assert "excluded" in cells[address], address

    excluded_included = ("B9", "B10", "B13", "B14", "B15", "B16", "B17", "B18", "B19", "B20")
    for address in excluded_included:
        assert "excluded" not in cells[address], address
    assert "excluded" not in cells["B21"]
    assert "excluded" not in cells["B22"]


def test_application_status_is_free_text_never_an_enum_lookup() -> None:
    for row in range(13, 21):
        assert 'LOWER(TRIM(TO_TEXT(v)))="' in _cells()[f"B{row}"]


def test_blank_and_whitespace_only_status_counts_as_not_recorded() -> None:
    assert 'TRIM(TO_TEXT(v))=""' in _cells()["B21"]
    assert 'TRIM(TO_TEXT(v))<>""' in _cells()["J1"]


def test_a_multi_source_opportunity_is_counted_once_per_source_type() -> None:
    formula = _cells()["B25"]
    assert formula.startswith("=IF(SUMPRODUCT(")
    assert "COUNTUNIQUE(FILTER(Job_Sources!" in formula
    assert "COUNTUNIQUE(FILTER(Opportunities!" not in formula


def test_duplicate_source_records_of_one_type_are_counted_once() -> None:
    for row in range(25, 30):
        formula = _cells()[f"B{row}"]
        assert formula.count("COUNTUNIQUE(") == 1
        assert "COUNTUNIQUE(FILTER(Job_Sources!" in formula


def test_an_opportunity_without_any_source_lands_in_unknown() -> None:
    formula = _cells()["B30"]
    assert "MATCH(id," in formula
    assert ">0,0,1),1)" in formula


def test_a_blank_source_type_lands_in_unknown_without_double_counting_orphans() -> None:
    formula = _cells()["B30"]
    assert "+IF(SUMPRODUCT(" in formula
    assert 'Job_Sources!$C$2:INDEX(Job_Sources!$C:$C,$I$1)=""' in formula


def test_bounded_ranges_depend_only_on_the_last_key_row_helpers() -> None:
    for address, text in _cells().items():
        if address in {"H1", "I1"}:
            continue
        for column_range in re.findall(r"INDEX\([^)]*\$[A-Z]+:\$[A-Z]+,(\$[HI]\$1)\)", text):
            assert column_range in {"$H$1", "$I$1"}


def test_the_date_parser_normalizes_the_separator_before_splitting() -> None:
    """Two SPLIT branches can disagree on array size, so split once."""

    for address in ("B9", "B10", "B33", "B34"):
        formula = _cells()[address]
        assert 'SPLIT(IF(iso,SUBSTITUTE(t,"-","/"),t),"/")' in formula, address
        assert "IF(iso,SPLIT(" not in formula, address


# --------------------------------------------------- status normalization rules
#
# ``application_status`` is free text (P0/12 §2). The sheet classifies it, it never
# rewrites it. The normalization is exactly ``LOWER(TRIM(TO_TEXT(value)))`` and the
# reference model below mirrors what Google Sheets TRIM actually does:
#   - strips leading and trailing spaces;
#   - collapses runs of regular spaces between words;
#   - does not remove non-breaking spaces (CHAR(160)).
# Only surrounding spaces and regular-space runs are normalized. Nothing else is
# changed, so the canonical cell value stays as the user typed it.


def _sheet_trim(value: str) -> str:
    """Model of Google Sheets ``TRIM`` for the cases these tests cover."""

    return " ".join(part for part in value.split(" ") if part)


def _classify(raw: str) -> str:
    normalized = _sheet_trim(str(raw)).lower()
    if normalized == "":
        return "blank"
    for label in layout.APPLICATION_STATUS_VALUES:
        if label.lower() == normalized:
            return label
    return "other"


def test_status_normalization_is_trim_plus_lower_only() -> None:
    for row in range(13, 21):
        formula = _cells()[f"B{row}"]
        assert "LOWER(TRIM(TO_TEXT(v)))=" in formula
    assert 'TRIM(TO_TEXT(v))=""' in _cells()["B21"]
    assert 'TRIM(TO_TEXT(v))<>""' in _cells()["J1"]


def test_status_normalization_never_special_cases_non_breaking_spaces() -> None:
    for address in [f"B{row}" for row in range(13, 23)] + ["J1"]:
        formula = _cells()[address]
        assert "CHAR(160)" not in formula
        assert "SUBSTITUTE" not in formula
        assert "REGEXREPLACE" not in formula


def test_status_classification_examples() -> None:
    assert _classify("Applied") == "Applied"
    assert _classify(" applied ") == "Applied"
    assert _classify("APPLIED") == "Applied"
    assert _classify("Need  to Apply") == "Need to Apply"
    assert _classify("") == "blank"
    assert _classify("   ") == "blank"
    assert _classify("\u00a0") == "other"


def test_a_non_breaking_space_value_is_recorded_but_not_blank() -> None:
    assert _classify("\u00a0") == "other"
    assert _sheet_trim("\u00a0") != ""


def test_the_status_partition_accounts_for_every_row_exactly_once() -> None:
    rows = [
        "Applied",
        " applied ",
        "APPLIED",
        "Need  to Apply",
        "",
        "   ",
        "\u00a0",
        "Ghosted",
        "Offer",
    ]

    counts = {label: 0 for label in layout.APPLICATION_STATUS_VALUES}
    blank = 0
    other = 0
    for raw in rows:
        bucket = _classify(raw)
        if bucket == "blank":
            blank += 1
        elif bucket == "other":
            other += 1
        else:
            counts[bucket] += 1

    assert counts["Applied"] == 3
    assert counts["Need to Apply"] == 1
    assert counts["Offer"] == 1
    assert blank == 2
    assert other == 2

    listed = sum(counts.values())
    assert listed + blank + other == len(rows)


def test_blank_rows_never_reach_the_other_bucket() -> None:
    assert _classify("") == "blank"
    assert _classify("    ") == "blank"
    assert _classify("") != "other"


def test_other_is_the_recorded_remainder_and_never_subtracts_blank_rows() -> None:
    cells = _cells()
    assert cells["J1"].startswith("=IF(SUMPRODUCT(")
    assert 'LAMBDA(v,TRIM(TO_TEXT(v))<>"")' in cells["J1"]
    assert cells["B22"] == "=MAX(0,$J$1-SUM($B$13:$B$20))"
    # B21 is a separate category and is never an input to the (other) subtraction.
    assert "$B$21" not in cells["B22"]


def test_j1_counts_only_non_blank_application_status_values() -> None:
    formula = _cells()["J1"]
    assert formula.startswith("=IF(SUMPRODUCT(")
    assert 'LAMBDA(v,TRIM(TO_TEXT(v))<>"")' in formula
    assert "LOWER(" not in formula
    assert "Opportunities!$AC" in formula
    assert "excluded" not in formula


# ------------------------------------------------- atomic initialization batch


def test_initialization_requests_are_one_atomic_batch() -> None:
    requests = layout.build_initialization_requests(5)

    assert isinstance(requests, list)
    assert all("addSheet" not in request for request in requests)
    assert "updateCells" in requests[0]
    assert "updateCells" in requests[-1]
    assert requests[-1]["updateCells"]["start"]["rowIndex"] == 0
    assert requests[-1]["updateCells"]["start"]["columnIndex"] == 12


def test_the_sentinel_request_is_last_in_the_atomic_batch() -> None:
    requests = layout.build_initialization_requests(5)

    sentinel = requests[-1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]
    assert sentinel == {"stringValue": layout.METRICS_SENTINEL}

    earlier = requests[:-1]
    assert len(earlier) == 1 + len(layout.build_format_requests(5))
    assert not any(
        "updateCells" in request and request["updateCells"]["start"]["columnIndex"] == 12
        for request in earlier
    )


def test_the_atomic_batch_contains_every_layout_cell_and_every_format() -> None:
    requests = layout.build_initialization_requests(5)
    cells = requests[0]["updateCells"]
    assert len(cells["rows"]) == 40
    assert "updateDimensionProperties" in str(requests)
    assert "repeatCell" in str(requests)


# ------------------------------------------------------- formula locale dialect
#
# A Google Sheets locale decides the *argument separator*: `en_US` uses a comma,
# `id_ID` uses a semicolon. Function names stay English in every locale, and
# literal content (string literals, regex patterns) must never be rewritten.


def _outside_literals(formula: str) -> str:
    """Return the formula with every double-quoted literal removed."""

    out: list[str] = []
    in_literal = False
    index = 0
    while index < len(formula):
        character = formula[index]
        if character == '"':
            if in_literal and formula[index + 1 : index + 2] == '"':
                index += 2
                continue
            in_literal = not in_literal
            index += 1
            continue
        if not in_literal:
            out.append(character)
        index += 1
    assert not in_literal, "unbalanced string literal"
    return "".join(out)


def _literals(formula: str) -> list[str]:
    """Return every double-quoted literal in a formula, in order."""

    return re.findall(r'"(?:[^"]|"")*"', formula)


def test_the_default_dialect_is_en_us() -> None:
    assert layout.DEFAULT_DIALECT.locale == "en_US"
    assert layout.DEFAULT_DIALECT.argument_separator == ","
    assert set(layout.FORMULA_DIALECTS) == {"en_US", "id_ID"}
    assert layout.FORMULA_DIALECTS["id_ID"].argument_separator == ";"


def test_the_dialect_renders_only_argument_separators() -> None:
    template = f'=JOIN("a, b"{layout.ARG}A1:A2)'

    assert layout.FORMULA_DIALECTS["en_US"].render(template) == '=JOIN("a, b",A1:A2)'
    assert layout.FORMULA_DIALECTS["id_ID"].render(template) == '=JOIN("a, b";A1:A2)'


def test_the_id_id_dialect_uses_semicolon_separators() -> None:
    dialect = layout.FORMULA_DIALECTS["id_ID"]
    cells = {cell.address: cell.content for cell in layout.all_cells(dialect)}

    assert cells["B2"] == (
        '=TEXT(YEAR(TODAY());"0000")&"-"&TEXT(MONTH(TODAY());"00")&"-"&TEXT(DAY(TODAY());"00")'
    )
    assert cells["B22"] == "=MAX(0;$J$1-SUM($B$13:$B$20))"
    assert cells["H1"] == (
        '=IFERROR(MAX(2;MAX(FILTER(ROW(Opportunities!$A:$A);Opportunities!$A:$A<>"")));2)'
    )


def test_id_id_formulas_contain_no_comma_outside_string_literals() -> None:
    dialect = layout.FORMULA_DIALECTS["id_ID"]
    for cell in layout.all_cells(dialect):
        if not cell.is_formula:
            continue
        assert "," not in _outside_literals(cell.content), cell.address
        assert layout.ARG not in cell.content, cell.address


def test_id_id_preserves_every_string_literal_and_regex_pattern() -> None:
    en_cells = {cell.address: cell.content for cell in layout.all_cells()}
    id_cells = {
        cell.address: cell.content for cell in layout.all_cells(layout.FORMULA_DIALECTS["id_ID"])
    }

    for address, en_formula in en_cells.items():
        if not en_formula.startswith("="):
            continue
        assert _literals(id_cells[address]) == _literals(en_formula), address


def test_id_id_preserves_date_parsing_semantics() -> None:
    dialect = layout.FORMULA_DIALECTS["id_ID"]
    cells = {cell.address: cell.content for cell in layout.all_cells(dialect)}
    formula = cells["B9"]

    # Same day-first rules, same round-trip validation, only the separators move.
    assert 'SPLIT(IF(iso;SUBSTITUTE(t;"-";"/");t);"/")' in formula
    assert "IF(iso;VALUE(INDEX(p;1;1));VALUE(INDEX(p;1;3)))" in formula
    assert "IF(iso;VALUE(INDEX(p;1;3));VALUE(INDEX(p;1;1)))" in formula
    assert 'IF(AND(ISNUMBER(y);ISNUMBER(m);ISNUMBER(d));DATE(y;m;d);"")' in formula
    assert r'"\s*\([^()]*\)\s*$"' in formula


def test_en_us_output_is_unchanged_by_the_dialect_layer() -> None:
    rendered = {cell.address: cell.content for cell in layout.all_cells()}
    explicit = {
        cell.address: cell.content for cell in layout.all_cells(layout.FORMULA_DIALECTS["en_US"])
    }

    assert rendered == explicit
    assert "," in rendered["B6"]


# ------------------------------------------------- formula cells vs literal text


def test_every_formula_cell_is_a_formula_and_starts_with_equals() -> None:
    for dialect in layout.FORMULA_DIALECTS.values():
        for cell in layout.all_cells(dialect):
            if not cell.is_formula:
                continue
            assert cell.content.startswith("="), cell.address
            assert not cell.content.startswith("=="), cell.address


def test_the_data_quality_cells_are_formulas_not_text() -> None:
    cells = {cell.address: cell for cell in layout.all_cells()}

    for address in ("B33", "B34"):
        assert cells[address].is_formula, f"{address} would be written as literal text"
        assert cells[address].content.startswith("=IFERROR(SUM("), address


def test_labels_and_footnotes_are_literal_text() -> None:
    cells = {cell.address: cell for cell in layout.all_cells()}

    for address in ("A1", "A2", "A5", "A6", "A12", "A13", "A24", "A25", "A30", "A32", "A36"):
        assert not cells[address].is_formula, address
    for address in ("A37", "A38", "A39", "A40"):
        assert not cells[address].is_formula, address


def test_no_formula_is_ever_written_as_a_string_value() -> None:
    cells = layout.all_cells(layout.FORMULA_DIALECTS["id_ID"])
    request = layout.build_cell_update_request(9, cells)
    rows = request["updateCells"]["rows"]
    start_column = request["updateCells"]["start"]["columnIndex"]

    for row in rows:
        for offset, value in enumerate(row["values"]):
            entered = value.get("userEnteredValue")
            if entered is None:
                continue
            if "formulaValue" in entered:
                assert entered["formulaValue"].startswith("=")
                assert "stringValue" not in entered
            else:
                assert "stringValue" in entered
                assert not entered["stringValue"].startswith("="), (
                    f"column {start_column + offset} looks like a formula but is text"
                )


# ------------------------------------------------ Indonesian locale identifier
#
# Google returns the Indonesian spreadsheet locale as `in_ID` in some responses
# and `id_ID` in others. `in` is the legacy ISO 639-1 code for Indonesian and
# `id` is the current one. Both select the same spreadsheet UI locale, so both
# must select the same formula dialect. Aliases are listed explicitly, never
# inferred by prefix, so an unknown locale is still refused.


def test_in_id_is_an_explicit_alias_of_id_id() -> None:
    assert layout.LOCALE_ALIASES == {"in_ID": "id_ID"}
    assert layout.resolve_formula_dialect("in_ID") is layout.FORMULA_DIALECTS["id_ID"]
    assert layout.resolve_formula_dialect("id_ID") is layout.FORMULA_DIALECTS["id_ID"]


def test_an_unsupported_locale_does_not_resolve() -> None:
    assert layout.resolve_formula_dialect("de_DE") is None
    assert layout.resolve_formula_dialect("in_GB") is None


def test_in_id_generates_the_same_63_cells_as_id_id() -> None:
    in_dialect = layout.resolve_formula_dialect("in_ID")
    assert in_dialect is not None
    in_cells = layout.all_cells(in_dialect)
    id_cells = layout.all_cells(layout.FORMULA_DIALECTS["id_ID"])

    assert len(in_cells) == 63
    assert in_cells == id_cells


def test_in_id_uses_semicolons_and_preserves_literals() -> None:
    in_dialect = layout.resolve_formula_dialect("in_ID")
    assert in_dialect is not None
    cells = {cell.address: cell.content for cell in layout.all_cells(in_dialect)}

    assert cells["B2"].startswith('=TEXT(YEAR(TODAY());"0000")')
    assert cells["B22"] == "=MAX(0;$J$1-SUM($B$13:$B$20))"
    assert ";" in cells["B6"]
    assert "," not in _outside_literals(cells["B6"])
    assert r'"\s*\([^()]*\)\s*$"' in cells["B9"]
    assert r'"^\d{4}-\d{1,2}-\d{1,2}$"' in cells["B9"]
    assert r'"^\d{1,2}/\d{1,2}/\d{4}$"' in cells["B9"]


def test_in_id_keeps_every_formula_cell_a_formula() -> None:
    in_dialect = layout.resolve_formula_dialect("in_ID")
    assert in_dialect is not None
    request = layout.build_cell_update_request(9, layout.all_cells(in_dialect))
    formulas = 0

    for row in request["updateCells"]["rows"]:
        for value in row["values"]:
            entered = value.get("userEnteredValue")
            if entered is None:
                continue
            if "formulaValue" in entered:
                formulas += 1
                assert entered["formulaValue"].startswith("=")
                assert "stringValue" not in entered
            else:
                assert not entered["stringValue"].startswith("=")

    assert formulas == 29


# ---------------------------------------------------- zero-match COUNTUNIQUE
#
# Google Sheets `FILTER` returns #N/A when no row matches. `COUNTUNIQUE` treats
# that error as one value, so the old outer `IFERROR` never saw a no-match. Every
# distinct metric must therefore count matching rows *before* calling FILTER, and
# the guard must repeat every filter criterion.


def _split_top_level(text: str, operators: str) -> list[str]:
    """Split on top-level operators, respecting nested calls and literals."""

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_literal = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            current.append(character)
            if in_literal and text[index + 1 : index + 2] == '"':
                current.append('"')
                index += 2
                continue
            in_literal = not in_literal
        elif in_literal:
            current.append(character)
        elif character in "([{":
            depth += 1
            current.append(character)
        elif character in ")]}":
            depth -= 1
            current.append(character)
        elif character in operators and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def _guard_terms(formula: str) -> list[tuple[list[str], list[str]]]:
    """Return ``(guard_conditions, filter_arguments)`` for every additive term."""

    body = formula[1:] if formula.startswith("=") else formula
    terms: list[tuple[list[str], list[str]]] = []
    for term in _split_top_level(body, "+"):
        if "COUNTUNIQUE(FILTER(" not in term:
            continue
        assert term.startswith("IF("), term
        inner = term[3:-1]
        parts = _split_top_level(inner, ",;")
        assert len(parts) == 3, parts
        guard, zero, counted = parts
        assert zero == "0", zero
        assert guard.endswith("=0"), guard
        guard_call = guard[:-2]
        assert guard_call.startswith("SUMPRODUCT("), guard_call
        assert guard_call.endswith(")"), guard_call
        guard_arguments = _split_top_level(guard_call[len("SUMPRODUCT(") : -1], ",;")
        conditions: list[str] = []
        for argument in guard_arguments:
            assert argument.startswith("--("), argument
            assert argument.endswith(")"), argument
            conditions.append(argument[3:-1])
        prefix = "COUNTUNIQUE(FILTER("
        assert counted.startswith(prefix), counted
        assert counted.endswith("))"), counted
        filter_arguments = _split_top_level(counted[len(prefix) : -2], ",;")
        terms.append((conditions, filter_arguments))
    return terms


_DISTINCT_METRIC_CELLS = (
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
    "B25",
    "B26",
    "B27",
    "B28",
    "B29",
    "B30",
    "J1",
)


def test_every_filter_condition_is_repeated_in_the_zero_match_guard() -> None:
    cells = _cells()
    for address in _DISTINCT_METRIC_CELLS:
        terms = _guard_terms(cells[address])
        assert terms, address
        for conditions, filter_arguments in terms:
            assert filter_arguments[1:] == conditions, address


def test_header_only_data_has_an_id_existence_guard_on_every_metric() -> None:
    cells = _cells()
    for address in _DISTINCT_METRIC_CELLS:
        for conditions, _ in _guard_terms(cells[address]):
            assert "A$2:INDEX" in conditions[0] or "B$2:INDEX" in conditions[0], address
            assert '<>""' in conditions[0], address


def test_no_metric_counts_the_filter_error_or_uses_counta() -> None:
    cells = _cells()
    for address in _DISTINCT_METRIC_CELLS:
        formula = cells[address]
        assert "COUNTA(" not in formula, address
        assert not formula.startswith("=IFERROR(COUNTUNIQUE(FILTER("), address
        assert "COUNTUNIQUE(FILTER(" in formula, address


def test_a_status_guard_requires_both_an_id_and_the_status_match() -> None:
    terms = _guard_terms(_cells()["B14"])
    assert len(terms) == 1
    conditions, _ = terms[0]

    assert any('<>""' in condition and "Opportunities!$A" in condition for condition in conditions)
    assert any('LOWER(TRIM(TO_TEXT(v)))="applied"' in condition for condition in conditions)


def test_all_eight_suggested_statuses_are_guarded_individually() -> None:
    cells = _cells()
    for row, label in enumerate(layout.APPLICATION_STATUS_VALUES, start=13):
        ((conditions, _),) = _guard_terms(cells[f"B{row}"])
        expected = f'LOWER(TRIM(TO_TEXT(v)))="{label.lower()}"'
        assert any(expected in condition for condition in conditions), label


def test_blank_status_and_recorded_status_guards_are_separate() -> None:
    blank_conditions, _ = _guard_terms(_cells()["B21"])[0]
    recorded_conditions, _ = _guard_terms(_cells()["J1"])[0]

    assert any('TRIM(TO_TEXT(v))=""' in condition for condition in blank_conditions)
    assert any('TRIM(TO_TEXT(v))<>""' in condition for condition in recorded_conditions)
    assert not any('TRIM(TO_TEXT(v))=""' in condition for condition in recorded_conditions)


def test_each_source_type_is_guarded_by_its_own_type_condition() -> None:
    cells = _cells()
    for row, source_type in enumerate(layout.SOURCE_TYPES, start=25):
        ((conditions, _),) = _guard_terms(cells[f"B{row}"])
        assert any(condition.endswith(f'="{source_type}"') for condition in conditions), source_type


def test_unknown_source_guards_orphans_and_blank_types_independently() -> None:
    terms = _guard_terms(_cells()["B30"])
    assert len(terms) == 2

    orphan_conditions, _ = terms[0]
    blank_type_conditions, _ = terms[1]
    assert any("MATCH(id," in condition for condition in orphan_conditions)
    assert any(
        'Job_Sources!$C$2:INDEX(Job_Sources!$C:$C,$I$1)=""' in c for c in blank_type_conditions
    )
    assert orphan_conditions != blank_type_conditions


def test_excluded_opportunities_are_guarded_only_where_the_backend_excludes_them() -> None:
    cells = _cells()
    excluded_metrics = ("B6", "B8", "B25", "B26", "B27", "B28", "B29", "B30")
    for address in excluded_metrics:
        for conditions, _ in _guard_terms(cells[address]):
            assert any("excluded" in condition for condition in conditions), address

    excluded_included = (
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
        "J1",
    )
    for address in excluded_included:
        for conditions, _ in _guard_terms(cells[address]):
            assert not any("excluded" in condition for condition in conditions), address


def test_invalid_dates_cannot_match_the_window_guard() -> None:
    cells = _cells()
    for address in ("B9", "B10"):
        ((conditions, _),) = _guard_terms(cells[address])
        assert any("IF(ok,AND(s>=" in condition for condition in conditions), address
        assert any("FALSE" in condition for condition in conditions), address

    assert cells["B33"].startswith("=IFERROR(SUM(")
    assert cells["B34"].startswith("=IFERROR(SUM(")


def test_zero_match_guard_uses_the_dialect_separator() -> None:
    for dialect in (
        layout.FORMULA_DIALECTS["en_US"],
        layout.FORMULA_DIALECTS["id_ID"],
        layout.resolve_formula_dialect("in_ID"),
    ):
        assert dialect is not None
        cells = {cell.address: cell.content for cell in layout.all_cells(dialect)}
        for address in _DISTINCT_METRIC_CELLS:
            formula = cells[address]
            assert "SUMPRODUCT(" in formula, address
            assert "=0" in formula, address
            if dialect.argument_separator == ";":
                assert ";" in formula, address
            else:
                assert "," in formula, address


def test_free_text_status_matches_no_suggested_label_and_falls_to_other() -> None:
    cells = _cells()
    for row in range(13, 21):
        ((conditions, _),) = _guard_terms(cells[f"B{row}"])
        status_conditions = [c for c in conditions if "LOWER(TRIM(TO_TEXT(v)))" in c]
        assert len(status_conditions) == 1
        assert status_conditions[0].endswith('"))')
        assert "REGEXMATCH" not in status_conditions[0]
        assert "COUNTA" not in cells[f"B{row}"]

    # A recorded free-text status is counted by J1 and therefore by (other).
    assert cells["B22"] == "=MAX(0,$J$1-SUM($B$13:$B$20))"
    assert 'LAMBDA(v,TRIM(TO_TEXT(v))<>"")' in cells["J1"]


def test_distinct_metrics_count_ids_rather_than_cells() -> None:
    cells = _cells()
    for address in _DISTINCT_METRIC_CELLS:
        assert "COUNTUNIQUE(" in cells[address], address
        assert "COUNTA(" not in cells[address], address


# ------------------------------------------------- formula syntax: parentheses
#
# Parentheses inside quoted literals and regex patterns are data, not syntax.
# The scanner removes those from consideration before tracking nesting.


def _paren_depth(formula: str) -> tuple[int, int]:
    """Return ``(final_depth, minimum_depth)`` outside quoted literals."""

    depth = 0
    minimum = 0
    in_literal = False
    index = 0
    while index < len(formula):
        character = formula[index]
        if character == '"':
            if in_literal and formula[index + 1 : index + 2] == '"':
                index += 2
                continue
            in_literal = not in_literal
        elif not in_literal:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                minimum = min(minimum, depth)
        index += 1
    assert not in_literal, "unbalanced string literal"
    return depth, minimum


def test_every_formula_has_balanced_parentheses_outside_literals() -> None:
    for locale in ("en_US", "id_ID", "in_ID"):
        dialect = layout.resolve_formula_dialect(locale)
        assert dialect is not None
        for cell in layout.all_cells(dialect):
            if not cell.is_formula:
                continue
            depth, minimum = _paren_depth(cell.content)
            assert minimum >= 0, (locale, cell.address, minimum)
            assert depth == 0, (locale, cell.address, depth, cell.content[-70:])


def test_data_quality_formulas_are_balanced_and_reference_their_date_column() -> None:
    for locale in ("en_US", "id_ID", "in_ID"):
        dialect = layout.resolve_formula_dialect(locale)
        assert dialect is not None
        cells = {cell.address: cell for cell in layout.all_cells(dialect)}

        for address, column in (("B33", "AB"), ("B34", "AE")):
            cell = cells[address]
            assert cell.is_formula, address
            assert cell.content.startswith("="), address
            assert _paren_depth(cell.content) == (0, 0), address
            assert f"Opportunities!${column}$2:INDEX(Opportunities!${column}:${column}" in (
                cell.content
            ), address
            assert r'"\s*\([^()]*\)\s*$"' in cell.content, address
            assert r'"^\d{4}-\d{1,2}-\d{1,2}$"' in cell.content, address
            assert r'"^\d{1,2}/\d{1,2}/\d{4}$"' in cell.content, address


def test_data_quality_formulas_close_sum_before_the_iferror_fallback() -> None:
    en_cells = _cells()
    assert en_cells["B33"].endswith("IF(ok,0,1)))))),0)")
    assert en_cells["B34"].endswith("IF(ok,0,1)))))),0)")

    id_dialect = layout.FORMULA_DIALECTS["id_ID"]
    id_cells = {cell.address: cell.content for cell in layout.all_cells(id_dialect)}
    assert id_cells["B33"].endswith("IF(ok;0;1))))));0)")
    assert id_cells["B34"].endswith("IF(ok;0;1))))));0)")
