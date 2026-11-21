"""Reusable harness for supervised live checks against a scratch spreadsheet.

Two hard rules shape this module:

1. **Nothing is written without a snapshot.** :func:`capture` reads every range a
   run may touch and :func:`save_snapshot` persists it with owner-only
   permissions before the first write.
2. **Nothing is written to a sheet that holds real data.** :func:`assert_disposable`
   refuses a run unless both canonical tabs contain only their header row, so a
   fixture can never be appended to a spreadsheet that someone actually uses.

The harness is deliberately small: it uses the same client the application uses,
adds no dependency, and reports the ranges it touched.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from personal_job_finder_api.integrations.google_sheets import dashboard_oracle as oracle
from personal_job_finder_api.integrations.google_sheets import dashboard_verify as verify
from personal_job_finder_api.integrations.google_sheets.client import (
    UNFORMATTED_RENDER_OPTION,
    GoogleSheetsClient,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError

CANONICAL_TABS: tuple[str, ...] = ("Opportunities", "Job_Sources")

DEFAULT_POLL_ATTEMPTS = 10
DEFAULT_POLL_INTERVAL_SECONDS = 1.5

#: The Sheets API allows roughly 60 reads and 60 writes per minute per user. A
#: scenario matrix exceeds that, so the harness retries a throttled call with the
#: provider's own hint instead of failing the run — and, critically, so a
#: *restore* cannot be abandoned half-done because the quota ran out.
DEFAULT_RATE_LIMIT_ATTEMPTS = 6
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 5.0


class LiveCheckError(Exception):
    """A live check cannot proceed safely."""


def with_retry(
    operation: Callable[[], Any],
    *,
    attempts: int = DEFAULT_RATE_LIMIT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """Run ``operation``, retrying only when the provider rate-limited it.

    Every other error is re-raised immediately: a permission problem or a missing
    spreadsheet must not be retried, and a retry loop must not hide it.
    """

    if attempts < 1:
        raise LiveCheckError("At least one attempt is required.")
    wait = sleep or time.sleep

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except GoogleSheetsError as exc:
            if exc.code != "rate_limited" or attempt == attempts:
                raise
            hinted = exc.retry_after_seconds
            wait(hinted if hinted else backoff_seconds * attempt)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The captured values of every tab a run may modify."""

    run_id: str
    spreadsheet_fingerprint: str
    captured_at_utc: str
    tabs: dict[str, list[list[str]]]
    ranges: tuple[str, ...] = field(default=())

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "spreadsheet_fingerprint": self.spreadsheet_fingerprint,
                "captured_at_utc": self.captured_at_utc,
                "ranges": list(self.ranges),
                "tabs": self.tabs,
            },
            indent=2,
            sort_keys=True,
        )


def fingerprint(spreadsheet_id: str) -> str:
    """A short, non-reversible label for logs and artifact names."""

    import hashlib

    digest = hashlib.sha256(spreadsheet_id.encode("utf-8")).hexdigest()
    return f"sheet-{digest[:12]}"


def capture(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    *,
    tabs: Sequence[str] = CANONICAL_TABS,
    run_id: str | None = None,
) -> Snapshot:
    """Read every tab a run may touch, before anything is written."""

    values = with_retry(
        lambda: client.get_values(
            spreadsheet_id,
            list(tabs),
            value_render_option=UNFORMATTED_RENDER_OPTION,
        )
    )
    identifier = run_id or datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")
    return Snapshot(
        run_id=identifier,
        spreadsheet_fingerprint=fingerprint(spreadsheet_id),
        captured_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        tabs={title: values.get(title, []) for title in tabs},
        ranges=tuple(tabs),
    )


def save_snapshot(snapshot: Snapshot, directory: Path) -> Path:
    """Persist a snapshot with owner-only permissions."""

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{snapshot.run_id}-snapshot.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(snapshot.to_json())
    return path


def assert_disposable(snapshot: Snapshot, *, allow_data: bool = False) -> None:
    """Refuse to run unless the target is provably a disposable scratch sheet.

    A header-only sheet is the default because it cannot hold user data. A
    baseline that already holds rows is only accepted with an explicit
    ``allow_data`` opt-in, and only because the caller has already persisted a
    snapshot it must restore afterwards.
    """

    for title, headers in (
        ("Opportunities", OPPORTUNITY_HEADERS),
        ("Job_Sources", JOB_SOURCE_HEADERS),
    ):
        rows = snapshot.tabs.get(title, [])
        if rows and [cell.strip() for cell in rows[0]] != list(headers):
            raise LiveCheckError(
                f"The {title} header row does not match the canonical contract. "
                "Run `init` first. Nothing was written."
            )
        data_rows = [row for row in rows[1:] if any(cell != "" for cell in row)]
        if data_rows and not allow_data:
            raise LiveCheckError(
                f"The {title} tab already holds {len(data_rows)} data row(s). "
                "A live fixture run only touches a disposable spreadsheet whose "
                "canonical tabs are header-only. Nothing was written."
            )


def write_rows(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    tab: str,
    rows: Sequence[Sequence[str]],
    *,
    start_row: int = 1,
    sleep: Callable[[float], None] | None = None,
) -> str:
    if not rows:
        return ""
    end_row = start_row + len(rows) - 1
    end_column = max(len(row) for row in rows)
    range_name = f"{tab}!A{start_row}:{_letters(end_column)}{end_row}"
    with_retry(
        lambda: client.batch_update_values(
            spreadsheet_id,
            [{"range": range_name, "values": [list(row) for row in rows]}],
        ),
        sleep=sleep,
    )
    return range_name


def _letters(index: int) -> str:
    from personal_job_finder_api.integrations.google_sheets.client import column_letter

    return column_letter(index)


def restore(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    snapshot: Snapshot,
    *,
    sleep: Callable[[float], None] | None = None,
) -> list[str]:
    """Write the captured values back and clear rows the run appended.

    The snapshot holds the full tab content, so writing it back from row 1 is an
    exact restore whether the run added rows, blanked rows, or shortened the tab.
    Only a *structural* change is refused: if the header row no longer matches the
    captured header row, someone replaced the tab and writing the snapshot back
    would overwrite their work.
    """

    current = with_retry(
        lambda: client.get_values(
            spreadsheet_id,
            list(snapshot.tabs),
            value_render_option=UNFORMATTED_RENDER_OPTION,
        ),
        sleep=sleep,
    )
    written: list[str] = []
    for title, original in snapshot.tabs.items():
        now = current.get(title, [])
        if original:
            captured_header = [cell.strip() for cell in original[0]]
            current_header = [cell.strip() for cell in (now[0] if now else [])]
            if current_header != captured_header:
                raise LiveCheckError(
                    f"The {title} header row changed during the run, so the tab was "
                    "replaced rather than edited. Nothing further was written."
                )
            # Pad to the captured header width so cells the fixture wrote beyond
            # a short baseline row are cleared again. ``values.get`` trims
            # trailing blanks, so an unpadded write would leave those cells
            # behind and the tab would not be restored exactly.
            width = len(original[0])
            padded = [list(row) + [""] * (width - len(row)) for row in original]
            range_name = write_rows(client, spreadsheet_id, title, padded, sleep=sleep)
            if range_name:
                written.append(range_name)
        surplus = len(now) - len(original)
        if surplus > 0:
            columns = len(original[0]) if original else 1
            start = len(original) + 1
            end = len(now)
            range_name = f"{title}!A{start}:{_letters(columns)}{end}"
            with_retry(
                lambda name=range_name, count=surplus, width=columns: client.batch_update_values(
                    spreadsheet_id,
                    [{"range": name, "values": [[""] * width for _ in range(count)]}],
                ),
                sleep=sleep,
            )
            written.append(range_name)
    return written


def clear_data_rows(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    *,
    tabs: Sequence[str] = CANONICAL_TABS,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Blank every data row below the header, keeping the header and row count.

    A scenario that starts from "no opportunities" needs an empty tab. Deleting
    rows would change the tab structure, so the rows are blanked instead; the
    caller must have captured a snapshot first, because this is destructive.
    """

    for title in tabs:
        rows = with_retry(
            lambda name=title: client.get_values(
                spreadsheet_id,
                [name],
                value_render_option=UNFORMATTED_RENDER_OPTION,
            ),
            sleep=sleep,
        ).get(title, [])
        if len(rows) <= 1:
            continue
        width = len(rows[0])
        end = len(rows)
        blanked = [[""] * width for _ in range(end - 1)]
        range_name = f"{title}!A2:{_letters(width)}{end}"

        def _blank(
            name: str = range_name,
            payload: list[list[str]] = blanked,
        ) -> dict[str, Any]:
            return client.batch_update_values(
                spreadsheet_id,
                [{"range": name, "values": payload}],
            )

        with_retry(_blank, sleep=sleep)


# ------------------------------------------------------------------- fixtures


@dataclass(frozen=True, slots=True)
class LiveFixture:
    """A deterministic dataset plus the ranges it occupies."""

    run_id: str
    opportunities: tuple[dict[str, str], ...]
    sources: tuple[dict[str, str], ...]

    def opportunity_rows(self) -> list[list[str]]:
        return [
            [record.get(name, "") for name in OPPORTUNITY_HEADERS] for record in self.opportunities
        ]

    def source_rows(self) -> list[list[str]]:
        return [[record.get(name, "") for name in JOB_SOURCE_HEADERS] for record in self.sources]


def build_fixture(*, as_of: date, run_id: str) -> LiveFixture:
    """Build the documented fixture dataset.

    Every value here is synthetic. The IDs carry the run id so a stray row can be
    recognised, and every scenario the Dashboard distinguishes is represented:

    - eligible, excluded, and duplicate-ID opportunities;
    - every source type, a duplicate source, an orphan, and a blank type;
    - every suggested application status, a free-text status, and blanks;
    - ISO, day-first, annotated, and invalid tracker dates;
    - first-seen timestamps inside, at, and outside the Jakarta window.
    """

    def iso(days: int) -> str:
        from datetime import timedelta

        return (as_of + timedelta(days=days)).isoformat()

    def day_first(days: int) -> str:
        from datetime import timedelta

        value = as_of + timedelta(days=days)
        return f"{value.day:02d}/{value.month:02d}/{value.year}"

    def seen(days: int, hour_utc: int = 3) -> str:
        from datetime import timedelta

        value = as_of + timedelta(days=days)
        return f"{value.isoformat()}T{hour_utc:02d}:00:00.000000Z"

    tag = run_id
    opportunities = (
        # Inside the new-opportunity window, in the applications window.
        {
            "opportunity_id": f"{tag}-applied",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-1),
            "date_applied": iso(-1),
            "application_status": "Applied",
            "interview_date": iso(3),
        },
        # Day-first date, annotated, inside the applications window.
        {
            "opportunity_id": f"{tag}-need-to-apply",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-2),
            "date_applied": f"{day_first(-2)} (pagi)",
            "application_status": "Need to Apply",
            "interview_date": "",
        },
        # Excluded: never in total/new/source, always in tracker metrics.
        {
            "opportunity_id": f"{tag}-excluded",
            "location_status": "excluded",
            "first_seen_at_utc": seen(-1),
            "date_applied": iso(0),
            "application_status": "Rejected",
            "interview_date": "",
        },
        # Free-text status, no tracker dates, outside the new window.
        {
            "opportunity_id": f"{tag}-free-text",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-40),
            "date_applied": "",
            "application_status": "Ghosted",
            "interview_date": "",
        },
        # Invalid calendar date: unreadable, not counted, not blank.
        {
            "opportunity_id": f"{tag}-invalid-date",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-40),
            "date_applied": "31/02/2026",
            "application_status": "",
            "interview_date": "31/02/2026",
        },
        # Interview inside the upcoming window.
        {
            "opportunity_id": f"{tag}-interview",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-40),
            "date_applied": "",
            "application_status": "Interview",
            "interview_date": iso(7),
        },
        # Duplicate of an existing ID: must collapse to one distinct opportunity.
        {
            "opportunity_id": f"{tag}-applied",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-1),
            "date_applied": "",
            "application_status": "",
            "interview_date": "",
        },
        # No source row at all: lands in Unknown.
        {
            "opportunity_id": f"{tag}-orphan",
            "location_status": "eligible",
            "first_seen_at_utc": seen(-40),
            "date_applied": "",
            "application_status": "Offer",
            "interview_date": "",
        },
    )

    def source(identifier: str, source_type: str, index: int) -> dict[str, str]:
        return {
            "source_id": f"{tag}-src-{index}",
            "opportunity_id": f"{tag}-{identifier}",
            "source_type": source_type,
        }

    sources = (
        source("applied", "greenhouse", 1),
        source("applied", "lever", 2),
        # A duplicate of one type must not double count.
        source("applied", "greenhouse", 3),
        source("need-to-apply", "ashby", 4),
        source("free-text", "permitted_career_page", 5),
        source("interview", "linkedin_post_manual", 6),
        # Blank type: Unknown, not an orphan.
        source("invalid-date", "", 7),
        # Orphan source row: its opportunity does not exist, so it counts nowhere.
        source("missing-opportunity", "greenhouse", 8),
    )

    return LiveFixture(run_id=tag, opportunities=opportunities, sources=sources)


def expected_metrics(fixture: LiveFixture, *, as_of: date) -> oracle.ExpectedMetrics:
    """The oracle's expectation for a fixture, computed from its records."""

    return oracle.compute(
        oracle.opportunity_rows(fixture.opportunities),
        oracle.source_rows(fixture.sources),
        as_of=as_of,
    )


# ------------------------------------------------------------------ scenarios

#: The scenarios the live matrix exercises, in order. Each one isolates a
#: behaviour the Dashboard has to get right, so a failure names the behaviour
#: instead of pointing at a single large fixture.
SCENARIO_NAMES: tuple[str, ...] = (
    "empty",
    "single",
    "excluded",
    "duplicate_ids",
    "all_statuses",
    "source_types",
    "sources_unknown",
    "tracker_dates",
    "windows",
    "multi_source",
)


def build_scenarios(*, as_of: date, run_id: str) -> dict[str, LiveFixture]:
    """Build one dataset per behaviour the Dashboard distinguishes.

    Every row is synthetic and every ID carries the run id, so a stray row is
    recognisable. The expectations are computed by the independent oracle, not by
    the formula builder.
    """

    from datetime import timedelta

    def iso(days: int) -> str:
        return (as_of + timedelta(days=days)).isoformat()

    def day_first(days: int) -> str:
        value = as_of + timedelta(days=days)
        return f"{value.day:02d}/{value.month:02d}/{value.year}"

    def seen(days: int, hour_utc: int = 3) -> str:
        value = as_of + timedelta(days=days)
        return f"{value.isoformat()}T{hour_utc:02d}:00:00.000000Z"

    def opp(identifier: str, **fields: str) -> dict[str, str]:
        record = {"opportunity_id": f"{run_id}-{identifier}", "location_status": "eligible"}
        record.update(fields)
        return record

    def src(identifier: str, source_type: str) -> dict[str, str]:
        # ``source_id`` is the identity column the metric row bound reads, so it
        # must never be blank: a blank one pins every range to row 2.
        return {
            "source_id": f"{run_id}-src-{identifier}-{source_type or 'blank'}",
            "opportunity_id": f"{run_id}-{identifier}",
            "source_type": source_type,
        }

    scenarios: dict[str, LiveFixture] = {}

    scenarios["empty"] = LiveFixture(run_id=run_id, opportunities=(), sources=())

    scenarios["single"] = LiveFixture(
        run_id=run_id,
        opportunities=(opp("single", first_seen_at_utc=seen(-40)),),
        sources=(src("single", "greenhouse"),),
    )

    scenarios["excluded"] = LiveFixture(
        run_id=run_id,
        opportunities=(
            opp("excluded", location_status="excluded", application_status="Rejected"),
            opp("eligible", application_status="Applied"),
        ),
        sources=(src("excluded", "greenhouse"), src("eligible", "lever")),
    )

    scenarios["duplicate_ids"] = LiveFixture(
        run_id=run_id,
        opportunities=(
            opp("dup", application_status="Applied", first_seen_at_utc=seen(-1)),
            opp("dup", application_status="Applied", first_seen_at_utc=seen(-1)),
        ),
        sources=(src("dup", "greenhouse"), src("dup", "greenhouse")),
    )

    statuses = oracle.APPLICATION_STATUS_VALUES
    scenarios["all_statuses"] = LiveFixture(
        run_id=run_id,
        opportunities=(
            *(
                opp(f"status-{index}", application_status=label)
                for index, label in enumerate(statuses)
            ),
            opp("status-blank", application_status=""),
            opp("status-unknown", application_status="Ghosted"),
        ),
        sources=tuple(src(f"status-{index}", "ashby") for index in range(len(statuses)))
        + (src("status-blank", "ashby"), src("status-unknown", "ashby")),
    )

    scenarios["source_types"] = LiveFixture(
        run_id=run_id,
        opportunities=tuple(opp(f"type-{index}") for index in range(len(oracle.SOURCE_TYPES))),
        sources=tuple(
            src(f"type-{index}", source_type)
            for index, source_type in enumerate(oracle.SOURCE_TYPES)
        ),
    )

    scenarios["sources_unknown"] = LiveFixture(
        run_id=run_id,
        opportunities=(opp("no-source"), opp("blank-type")),
        sources=(
            src("blank-type", ""),
            # Orphan: its opportunity does not exist.
            {
                "source_id": f"{run_id}-src-ghost",
                "opportunity_id": f"{run_id}-ghost",
                "source_type": "greenhouse",
            },
        ),
    )

    scenarios["tracker_dates"] = LiveFixture(
        run_id=run_id,
        opportunities=(
            opp("iso", date_applied=iso(-1), application_status="Applied"),
            opp("day-first", date_applied=day_first(-2), application_status="Applied"),
            opp("annotated", date_applied=f"{day_first(-3)} (pagi)", application_status="Applied"),
            opp("invalid", date_applied="31/02/2026", application_status="Applied"),
            opp("blank", date_applied="", application_status="Applied"),
        ),
        sources=tuple(
            src(name, "lever") for name in ("iso", "day-first", "annotated", "invalid", "blank")
        ),
    )

    scenarios["windows"] = LiveFixture(
        run_id=run_id,
        opportunities=(
            # Applications window is today-6 .. today inclusive.
            opp("app-start", date_applied=iso(-6), first_seen_at_utc=seen(-6)),
            opp("app-end", date_applied=iso(0)),
            opp("app-before", date_applied=iso(-7)),
            opp("app-after", date_applied=iso(1)),
            # Interviews window is today .. today+7 inclusive.
            opp("int-start", interview_date=iso(0)),
            opp("int-end", interview_date=iso(7)),
            opp("int-before", interview_date=iso(-1)),
            opp("int-after", interview_date=iso(8)),
            # New-opportunity window boundaries in UTC around Jakarta midnight.
            opp("new-first", first_seen_at_utc=f"{iso(-6)}T17:00:00.000000Z"),
            opp("new-last", first_seen_at_utc=f"{iso(0)}T16:59:59.999999Z"),
            opp("new-early", first_seen_at_utc=f"{iso(-7)}T16:59:59.999999Z"),
            opp("new-late", first_seen_at_utc=f"{iso(0)}T17:00:00.000000Z"),
        ),
        sources=tuple(
            src(name, "permitted_career_page")
            for name in (
                "app-start",
                "app-end",
                "app-before",
                "app-after",
                "int-start",
                "int-end",
                "int-before",
                "int-after",
                "new-first",
                "new-last",
                "new-early",
                "new-late",
            )
        ),
    )

    scenarios["multi_source"] = LiveFixture(
        run_id=run_id,
        opportunities=(opp("multi"), opp("single-source")),
        sources=(
            src("multi", "greenhouse"),
            src("multi", "lever"),
            src("multi", "greenhouse"),
            src("multi", "linkedin_post_manual"),
            src("single-source", "ashby"),
        ),
    )

    return scenarios


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """One scenario's live outcome, kept separate from the assertions."""

    name: str
    checks: tuple[verify.MetricCheck, ...]
    expected: oracle.ExpectedMetrics
    attempts: int
    stable: bool
    row_mismatch: str | None = None

    @property
    def failures(self) -> tuple[verify.MetricCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def ok(self) -> bool:
        return not self.failures and self.stable and self.row_mismatch is None

    def describe(self) -> str:
        if self.ok:
            return f"{self.name}: {len(self.checks)} metrics agree ({self.attempts} read(s))"
        details: list[str] = [f"{self.name}: FAILED after {self.attempts} read(s)"]
        if self.row_mismatch is not None:
            details.append(f"  rows: {self.row_mismatch}")
        for check in self.failures:
            suffix = f" error={check.error}" if check.error else ""
            details.append(
                f"  {check.label}: expected {check.expected!r}, "
                f"actual {check.actual!r} ({check.status}){suffix}"
            )
        return "\n".join(details)


def run_scenario(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    *,
    name: str,
    fixture: LiveFixture,
    as_of: date,
    metadata: verify.SpreadsheetMetadata | None = None,
    attempts: int = DEFAULT_POLL_ATTEMPTS,
    interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> ScenarioRun:
    """Write one scenario, wait for recalculation, and compare every metric.

    The written rows are read back first: if the sheet did not store what the
    scenario declared, the metric comparison would describe the wrong data.
    """

    clear_data_rows(client, spreadsheet_id)
    if fixture.opportunities:
        write_rows(client, spreadsheet_id, "Opportunities", fixture.opportunity_rows(), start_row=2)
    if fixture.sources:
        write_rows(client, spreadsheet_id, "Job_Sources", fixture.source_rows(), start_row=2)

    expected = expected_metrics(fixture, as_of=as_of)
    row_mismatch = _row_round_trip(client, spreadsheet_id, fixture)

    def read() -> tuple[verify.MetricCheck, ...]:
        return verify.verify_dashboard(
            client, spreadsheet_id, as_of=as_of, metadata=metadata
        ).checks

    checks, used, stable = wait_until_settled(
        read,
        is_settled=lambda current: all(check.ok for check in current),
        attempts=attempts,
        interval_seconds=interval_seconds,
        sleep=sleep,
    )
    return ScenarioRun(
        name=name,
        checks=checks,
        expected=expected,
        attempts=used,
        stable=stable,
        row_mismatch=row_mismatch,
    )


def _row_round_trip(
    client: GoogleSheetsClient,
    spreadsheet_id: str,
    fixture: LiveFixture,
) -> str | None:
    """Confirm the sheet stored exactly the scenario's rows, or describe why not."""

    values = with_retry(
        lambda: client.get_values(
            spreadsheet_id,
            ["Opportunities", "Job_Sources"],
            value_render_option=UNFORMATTED_RENDER_OPTION,
        )
    )
    for title, headers, records in (
        ("Opportunities", OPPORTUNITY_HEADERS, fixture.opportunities),
        ("Job_Sources", JOB_SOURCE_HEADERS, fixture.sources),
    ):
        stored = [row for row in values.get(title, [])[1:] if any(cell != "" for cell in row)]
        wanted = [[record.get(column, "") for column in headers] for record in records]
        wanted = [row for row in wanted if any(cell != "" for cell in row)]
        if len(stored) != len(wanted):
            return f"{title} holds {len(stored)} row(s), the scenario wrote {len(wanted)}"
        for index, (actual, expected_row) in enumerate(zip(stored, wanted, strict=True), start=2):
            trimmed = (list(actual) + [""] * len(headers))[: len(headers)]
            if trimmed != expected_row:
                differing = [
                    f"{headers[column]}: {expected_row[column]!r} != {trimmed[column]!r}"
                    for column in range(len(headers))
                    if trimmed[column] != expected_row[column]
                ]
                return f"{title} row {index}: " + ", ".join(differing[:5])
    return None


# ---------------------------------------------------------------- comparison


@dataclass(frozen=True, slots=True)
class LiveResult:
    """The outcome of one live comparison."""

    report: verify.VerifyReport
    attempts: int
    stable: bool

    @property
    def ok(self) -> bool:
        return self.report.ok and self.stable


def wait_until_settled(
    read: Callable[[], tuple[verify.MetricCheck, ...]],
    *,
    is_settled: Callable[[tuple[verify.MetricCheck, ...]], bool] | None = None,
    attempts: int = DEFAULT_POLL_ATTEMPTS,
    interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> tuple[tuple[verify.MetricCheck, ...], int, bool]:
    """Poll until the read is correct or two consecutive reads agree.

    A spreadsheet recalculates asynchronously, so one read can show a stale value.
    Two agreeing reads prove the value stopped changing, but *not* that it is
    correct: a stale value can also be stable. ``is_settled`` therefore lets the
    caller stop as soon as all checks pass, while a mismatch keeps polling until
    the deadline. The wait is always bounded.
    """

    if attempts < 1:
        raise LiveCheckError("At least one poll attempt is required.")
    wait = sleep or time.sleep

    previous: tuple[verify.MetricCheck, ...] | None = None
    for attempt in range(1, attempts + 1):
        current = read()
        if is_settled is not None:
            # A stale value can be stable, so a predicate means "keep polling
            # until correct or out of time", never "stop at the first plateau".
            if is_settled(current):
                return current, attempt, True
        elif previous is not None and current == previous:
            return current, attempt, True
        previous = current
        if attempt < attempts:
            wait(interval_seconds)
    return previous or (), attempts, False
