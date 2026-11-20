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

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from personal_job_finder_api.integrations.google_sheets import dashboard_layout as layout
from personal_job_finder_api.integrations.google_sheets import dashboard_oracle as oracle
from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
    GoogleSheetsClient,
    GridCell,
    SpreadsheetMetadata,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)

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
    than through the spreadsheet locale's display format.
    """

    values = client.get_values(
        spreadsheet_id,
        ["Opportunities", "Job_Sources"],
        value_render_option=UNFORMATTED_RENDER_OPTION,
    )
    opportunities = _rows_to_dicts(values["Opportunities"], OPPORTUNITY_HEADERS)
    sources = _rows_to_dicts(values["Job_Sources"], JOB_SOURCE_HEADERS)
    return opportunities, sources


def read_metric_cells(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
) -> dict[str, GridCell]:
    """Read every metric cell with its formula, effective value, and error.

    One bounding rectangle is requested instead of 25 single-cell ranges. The
    provider groups grid data by sheet and does not preserve request order across
    sheets, so the smallest request that yields unambiguous addresses is the
    safest one.
    """

    rows = [int(address[1:]) for address in METRIC_CELLS]
    columns = sorted({address[0] for address in METRIC_CELLS})
    last_row = max(rows)
    # The sentinel lives to the right of the visible columns. Extending the
    # bounding rectangle by one column keeps it in the same request, which saves
    # one read per verification and keeps a polling loop inside the API quota.
    last_column = max(columns[-1], layout.SENTINEL_CELL[0])
    range_name = f"{layout.DASHBOARD_TAB}!A1:{last_column}{last_row}"
    grid = client.get_grid(spreadsheet_id, [range_name])
    wanted = set(METRIC_CELLS) | {layout.SENTINEL_CELL}
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
) -> tuple[MetricCheck, ...]:
    """Compare each metric cell against the oracle, cell by cell."""

    checks: list[MetricCheck] = []
    for address in METRIC_CELLS:
        cell = cells.get(address)
        want = expected.values.get(address)
        if cell is None:
            checks.append(MetricCheck(address, want, None, None, STATUS_MISSING))
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

    if layout.DASHBOARD_TAB not in metadata.titles:
        return VerifyReport(
            locale=metadata.locale,
            time_zone=metadata.time_zone,
            tabs=metadata.titles,
            checks=(),
            problems=("The Dashboard tab does not exist.",),
        )

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

    expected = oracle.compute(
        oracle.opportunity_rows(opportunities),
        oracle.source_rows(sources),
        as_of=as_of,
    )
    cells = read_metric_cells(client, spreadsheet_id)

    # The tab must be owned by this version before its numbers mean anything.
    # The sentinel is a literal, so it arrives in the same grid read.
    sentinel_cell = cells.get(layout.SENTINEL_CELL)
    sentinel = (sentinel_cell.literal or "") if sentinel_cell is not None else ""
    if sentinel != layout.DASHBOARD_SENTINEL:
        problems.append(
            "The Dashboard sentinel is missing or different, so the tab is not "
            "owned by this version. Delete the tab and run dashboard-init."
        )

    return VerifyReport(
        locale=metadata.locale,
        time_zone=metadata.time_zone,
        tabs=metadata.titles,
        checks=compare(expected, cells),
        problems=tuple(problems),
    )
