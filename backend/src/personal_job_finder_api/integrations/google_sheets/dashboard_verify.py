"""Read-only verification of the optional Dashboard against a live spreadsheet.

The point of this module is to answer one question without a human reading cells:
*does every Dashboard metric agree with an independent calculation of the same
canonical rows?* A formula that was written successfully but shows ``#ERROR!``,
``#REF!``, or a stale number is therefore reported as a defect rather than as a
successful write.

Nothing here mutates a spreadsheet. Writes belong to the explicit commands and to
the supervised live test, which snapshots and restores its own data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout
from personal_job_finder_api.integrations.google_sheets import dashboard_oracle as oracle
from personal_job_finder_api.integrations.google_sheets import kpi_strip
from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
    GoogleSheetsClient,
    GridCell,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    header_row_for,
)

CARD_CELLS: tuple[str, ...] = oracle.KPI_STRIP_CELLS

#: Cells whose value is a metric. Everything else in the tab is a label or a note.
METRIC_CELLS: tuple[str, ...] = tuple(
    sorted(
        (
            {"B2", "B6", "B7", "B8", "B9", "B10", "B21", "B22", "B30", "J1"}
            | {f"B{row}" for row in range(13, 21)}
            | {f"B{row}" for row in range(25, 30)}
            | {"B33", "B34"}
        ),
        key=lambda address: (address[0], int(address[1:])),
    )
)

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_ERROR = "error"
STATUS_NOT_A_FORMULA = "not_a_formula"
STATUS_MISSING = "missing"


@dataclass(frozen=True, slots=True)
class MetricCheck:
    """One Dashboard cell compared against the oracle."""

    address: str
    expected: int | str | None
    actual: str | None
    error: str | None
    status: str
    formula: str | None = None
    #: The tab the cell lives on. Two tabs share cell addresses (``B6`` is both a
    #: summary card and a metric), so a bare address is ambiguous in a report.
    tab: str = ""

    @property
    def label(self) -> str:
        return f"{self.tab}!{self.address}" if self.tab else self.address

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """The outcome of one read-only Dashboard verification."""

    locale: str | None
    time_zone: str | None
    tabs: tuple[str, ...]
    checks: tuple[MetricCheck, ...]
    problems: tuple[str, ...] = field(default=())

    @property
    def failures(self) -> tuple[MetricCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.problems

    def summary(self) -> str:
        total = len(self.checks)
        failed = len(self.failures)
        return f"{total - failed}/{total} metrics agree; {len(self.problems)} problem(s)"


def _rows_to_dicts(
    rows: Sequence[Sequence[str]],
    headers: Sequence[str],
) -> list[dict[str, str]]:
    """Map a tab's rows onto header names, ignoring fully empty rows."""

    if not rows:
        return []
    header_row = [cell.strip() for cell in rows[0]]
    index_by_name = {name: index for index, name in enumerate(header_row) if name}
    missing = [name for name in headers if name not in index_by_name]
    if missing:
        raise ValueError("canonical header row is incomplete: " + ", ".join(sorted(missing)))
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(cell != "" for cell in row):
            continue
        records.append(
            {
                name: (row[index_by_name[name]] if index_by_name[name] < len(row) else "")
                for name in headers
            }
        )
    return records


def read_canonical_rows(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Read both canonical tabs as header-keyed rows.

    ``UNFORMATTED_VALUE`` is used so a tracker date is read as stored text rather
    than through the spreadsheet locale's display format. Each read starts at the
    tab's canonical header row, so the KPI strip above ``Opportunities`` is never
    mistaken for a header.
    """

    ranges = [f"{tab}!A{header_row_for(tab)}:ZZ" for tab in ("Opportunities", "Job_Sources")]
    values = client.get_values(
        spreadsheet_id,
        ranges,
        value_render_option=UNFORMATTED_RENDER_OPTION,
    )
    opportunities = _rows_to_dicts(values[ranges[0]], OPPORTUNITY_HEADERS)
    sources = _rows_to_dicts(values[ranges[1]], JOB_SOURCE_HEADERS)
    return opportunities, sources


def read_cells(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    *,
    tab: str,
    addresses: Sequence[str],
    sentinel_address: str,
) -> dict[str, GridCell]:
    """Read cells with their formula, effective value, and error.

    One bounding rectangle is requested instead of one range per cell. The
    provider groups grid data by sheet and does not preserve request order across
    sheets, so the smallest request that yields unambiguous addresses is the
    safest one.
    """

    rows = [int(address[1:]) for address in addresses]
    columns = sorted({address[0] for address in addresses})
    last_row = max(rows)
    # The sentinel lives to the right of the visible columns. Extending the
    # bounding rectangle by one column keeps it in the same request, which saves
    # one read per verification and keeps a polling loop inside the API quota.
    last_column = max(columns[-1], sentinel_address[0])
    range_name = f"{tab}!A1:{last_column}{last_row}"
    grid = client.get_grid(spreadsheet_id, [range_name])
    wanted = set(addresses) | {sentinel_address}
    cells: dict[str, GridCell] = {}
    for row in grid.get(range_name) or []:
        for cell in row:
            if cell.address in wanted:
                cells[cell.address] = cell
    return cells


def read_metric_cells(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
) -> dict[str, GridCell]:
    """Read every metric-board cell."""

    return read_cells(
        client,
        spreadsheet_id,
        tab=layout.METRICS_TAB,
        addresses=METRIC_CELLS,
        sentinel_address=layout.SENTINEL_CELL,
    )


def read_card_cells(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
) -> dict[str, GridCell]:
    """Read the four KPI value cells on ``Opportunities``."""

    range_name = "Opportunities!A1:P3"
    grid = client.get_grid(spreadsheet_id, [range_name])
    wanted = set(CARD_CELLS)
    cells: dict[str, GridCell] = {}
    for row in grid.get(range_name) or []:
        for cell in row:
            if cell.address in wanted:
                cells[cell.address] = cell
    return cells


def _normalize_actual(value: str | None) -> str:
    """Compare numbers as numbers and text as text, ignoring display padding."""

    if value is None:
        return ""
    text = value.strip()
    if text == "":
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return repr(number)


def compare(
    expected: oracle.ExpectedMetrics,
    cells: dict[str, GridCell],
    addresses: Sequence[str] = METRIC_CELLS,
    *,
    tab: str = "",
) -> tuple[MetricCheck, ...]:
    """Compare each requested cell against the oracle, cell by cell."""

    checks: list[MetricCheck] = []
    for address in addresses:
        cell = cells.get(address)
        want = expected.values.get(address)
        if cell is None:
            checks.append(MetricCheck(address, want, None, None, STATUS_MISSING, tab=tab))
            continue
        if cell.error is not None:
            checks.append(
                MetricCheck(
                    address,
                    want,
                    cell.value,
                    cell.error,
                    STATUS_ERROR,
                    formula=cell.formula,
                    tab=tab,
                )
            )
            continue
        if cell.formula is None:
            checks.append(
                MetricCheck(
                    address,
                    want,
                    cell.value,
                    None,
                    STATUS_NOT_A_FORMULA,
                    formula=None,
                    tab=tab,
                )
            )
            continue
        actual = _normalize_actual(cell.value)
        wanted = _normalize_actual(None if want is None else str(want))
        status = STATUS_OK if actual == wanted else STATUS_MISMATCH
        checks.append(
            MetricCheck(
                address,
                want,
                cell.value,
                None,
                status,
                formula=cell.formula,
                tab=tab,
            )
        )
    return tuple(checks)


def verify_dashboard(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    *,
    as_of: date,
    metadata: SpreadsheetMetadata | None = None,
) -> VerifyReport:
    """Verify every Dashboard metric against the canonical rows.

    The Dashboard tab is read only. A missing tab, a missing metric cell, or a
    metric written as a constant is reported as a problem instead of raising, so
    one diagnostic run describes the whole tab.

    ``metadata`` may be supplied by a caller that already read it, which saves one
    API request per poll in a verification loop.
    """

    problems: list[str] = []
    metadata = metadata or client.get_metadata(spreadsheet_id)

    if metadata.time_zone != layout.DASHBOARD_TIMEZONE:
        problems.append(
            f"The spreadsheet time zone is {metadata.time_zone or 'unset'}, "
            f"not {layout.DASHBOARD_TIMEZONE}; TODAY() boundaries are unreliable."
        )

    try:
        opportunities, sources = read_canonical_rows(client, spreadsheet_id)
    except ValueError as exc:
        return VerifyReport(
            locale=metadata.locale,
            time_zone=metadata.time_zone,
            tabs=metadata.titles,
            checks=(),
            problems=(*problems, str(exc)),
        )

    projected = oracle.opportunity_rows(opportunities)
    checks: list[MetricCheck] = []

    strip_expected = oracle.compute_kpi_strip(projected, as_of=as_of)
    strip_cells = read_card_cells(client, spreadsheet_id)
    checks.extend(compare(strip_expected, strip_cells, CARD_CELLS, tab=kpi_strip.OPPORTUNITY_TAB))

    if layout.METRICS_TAB not in metadata.titles:
        problems.append("The Metrics board does not exist.")
        return VerifyReport(
            locale=metadata.locale,
            time_zone=metadata.time_zone,
            tabs=metadata.titles,
            checks=tuple(checks),
            problems=tuple(problems),
        )

    metrics_expected = oracle.compute(
        projected,
        oracle.source_rows(sources),
        as_of=as_of,
    )
    metrics = read_metric_cells(client, spreadsheet_id)
    sentinel = _literal(metrics, layout.SENTINEL_CELL)
    if sentinel != layout.METRICS_SENTINEL:
        problems.append(
            "The Metrics sentinel is missing or different, so the tab is not "
            "owned by this version. Delete the tab and run metrics-init."
        )
    checks.extend(compare(metrics_expected, metrics, METRIC_CELLS, tab=layout.METRICS_TAB))

    return VerifyReport(
        locale=metadata.locale,
        time_zone=metadata.time_zone,
        tabs=metadata.titles,
        checks=tuple(checks),
        problems=tuple(problems),
    )


def _literal(cells: Mapping[str, GridCell], address: str) -> str:
    """Return a literal cell value, or an empty string when absent."""

    cell = cells.get(address)
    return (cell.literal or "") if cell is not None else ""
