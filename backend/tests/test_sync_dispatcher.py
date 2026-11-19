"""Tests for the durable Google Sheets outbox dispatcher.

Every test uses a fake transport; no socket is opened and no real credential is
read. The fake behaves like the Sheets API for the operations the dispatcher
uses: metadata, range reads, value batch updates, and structural batch updates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import (
    job_sources,
    job_sources_read_model,
    opportunities,
    opportunities_read_model,
    sync_outbox,
)
from personal_job_finder_api.domain.location import classify_location
from personal_job_finder_api.domain.opportunities import (
    JobSourceRef,
    OpportunityRecord,
    TargetRoleSpec,
    match_target_role,
)
from personal_job_finder_api.integrations.google_sheets.config import (
    JOB_SOURCE_HEADERS,
    OPPORTUNITY_HEADERS,
    REQUIRED_TABS,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.repositories.read_model import ReadModelSettlementError
from personal_job_finder_api.search.staging import OpportunityStagingService
from personal_job_finder_api.sync.dispatcher import SyncDispatcher, SyncOutcome
from personal_job_finder_api.sync.projections import project_opportunity

NOW = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
ROLE = TargetRoleSpec(
    role_id="role_backend",
    canonical_name="Backend Engineer",
    aliases=("Backend Developer",),
)


def _row_window(address: str) -> tuple[int | None, int | None]:
    """Return the row bounds of an A1 address such as ``A5:A5``.

    Column-only ranges (``A:AG``) have no row bounds and return ``(None, None)``.
    """

    start, _, end = address.partition(":")
    digits = "".join(ch for ch in start if ch.isdigit())
    if not digits:
        return None, None
    first = int(digits)
    end_digits = "".join(ch for ch in end if ch.isdigit())
    last = int(end_digits) if end_digits else first
    return first, last


class FakeSheet:
    """In-memory spreadsheet with the parts of the Sheets API used here."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[str]]] = {
            "Opportunities": [list(OPPORTUNITY_HEADERS)],
            "Job_Sources": [list(JOB_SOURCE_HEADERS)],
        }
        self.calls: list[tuple[str, Any]] = []

    def rows(self, tab: str) -> list[list[str]]:
        return self.tabs[tab]

    def identity_column(self, tab: str) -> int:
        return 0

    def identity_map(self, tab: str) -> dict[str, int]:
        header = self.tabs[tab][0]
        key_index = 0
        column = REQUIRED_TABS[tab][key_index]
        assert header[key_index] == column
        mapping: dict[str, int] = {}
        for index, row in enumerate(self.tabs[tab][1:], start=2):
            value = row[key_index] if row else ""
            mapping[value] = index
        return mapping


class FakeTransport:
    def __init__(self, sheet: FakeSheet) -> None:
        self.sheet = sheet
        self.failures: list[Exception] = []
        self.after_write_error: Exception | None = None
        self.read_error: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.failures:
            raise self.failures.pop(0)

    def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        self.sheet.calls.append(("get_spreadsheet", spreadsheet_id))
        return {"sheets": [{"properties": {"title": title}} for title in self.sheet.tabs]}

    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: list[str],
    ) -> dict[str, Any]:
        self.sheet.calls.append(("batch_get_values", (spreadsheet_id, tuple(ranges))))
        if self.read_error is not None:
            raise self.read_error
        value_ranges: list[dict[str, Any]] = []
        for requested in ranges:
            tab, address = requested.split("!", 1)
            rows = self.sheet.tabs.get(tab, [])
            first, last = _row_window(address)
            if first is None:
                value_ranges.append({"values": [list(row) for row in rows]} if rows else {})
                continue
            # Honour the requested row window like the real API does; rows past the
            # end of the tab come back as an absent entry, not as blank rows.
            selected = rows[first - 1 : last] if first <= len(rows) else []
            value_ranges.append({"values": [list(row) for row in selected]} if selected else {})
        return {"valueRanges": value_ranges}

    def batch_update_values(
        self,
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        self.sheet.calls.append(("batch_update_values", (spreadsheet_id, data, value_input_option)))
        assert value_input_option == "RAW"
        self._maybe_fail()
        for item in data:
            range_name = str(item["range"])
            tab = range_name.split("!", 1)[0]
            address = range_name.split("!", 1)[1]
            start_cell = address.split(":", 1)[0]
            start_row = int("".join(ch for ch in start_cell if ch.isdigit()))
            for offset, values in enumerate(item["values"]):
                row_number = start_row + offset
                rows = self.sheet.tabs[tab]
                while len(rows) < row_number:
                    rows.append([""] * len(rows[0]))
                rows[row_number - 1] = [str(value) for value in values]
        if self.after_write_error is not None:
            raise self.after_write_error
        return {}

    def batch_update_spreadsheet(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.sheet.calls.append(("batch_update_spreadsheet", (spreadsheet_id, requests)))
        for request in requests:
            title = request["addSheet"]["properties"]["title"]
            self.sheet.tabs.setdefault(title, [list(REQUIRED_TABS[title])])
        return {}


def record(
    *,
    external_id: str = "1001",
    location: str | None = "Jakarta, Indonesia",
    title: str = "Backend Engineer",
    source_type: str = "greenhouse",
) -> OpportunityRecord:
    return OpportunityRecord(
        company_id="company_acme",
        company_name="Acme Example",
        job_title=title,
        location=classify_location(location),
        description="<p>Build services.</p>",
        posted_at=None,
        source_updated_at=None,
        source=JobSourceRef(
            source_type=source_type,
            source_url=f"https://boards.greenhouse.io/acme/jobs/{external_id}",
            external_source_id=external_id,
            captured_at=NOW,
            provenance_note="Captured from the public Greenhouse job board API.",
        ),
        role_match=match_target_role(title, None, (ROLE,)),
    )


def stage(engine: Engine, *, run_id: str, records: list[OpportunityRecord]) -> None:
    OpportunityStagingService(engine, clock=lambda: NOW).stage(
        run_id=run_id,
        records=records,
    )


def outbox(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(sa.select(sync_outbox)).mappings().all()]


def dispatcher(
    engine: Engine,
    sheet: FakeSheet,
    *,
    clock: Any = None,
) -> tuple[SyncDispatcher, FakeTransport]:
    transport = FakeTransport(sheet)
    return (
        SyncDispatcher(
            engine,
            transport=transport,
            spreadsheet_id="test_spreadsheet_id_1234567890",
            clock=clock or (lambda: NOW),
            max_batch_ranges=3,
        ),
        transport,
    )


def test_pending_work_is_written_to_both_tabs(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)

    outcome = sync.dispatch_due()

    assert outcome.synced == 2
    assert outcome.failed == 0
    assert len(sheet.rows("Opportunities")) == 2
    assert len(sheet.rows("Job_Sources")) == 2
    assert sheet.rows("Opportunities")[1][0].startswith("opp_")
    assert sheet.rows("Job_Sources")[1][0].startswith("src_")


def test_successful_write_marks_the_projection_synced(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sync, _ = dispatcher(engine, FakeSheet())

    sync.dispatch_due()

    assert {row["status"] for row in outbox(engine)} == {"synced"}
    assert all(row["synced_at_utc"] for row in outbox(engine))


def test_successful_write_updates_the_read_model(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sync, _ = dispatcher(engine, FakeSheet())

    sync.dispatch_due()

    with engine.connect() as connection:
        opportunity = (
            connection.execute(
                sa.select(
                    opportunities_read_model.c.opportunity_id,
                    opportunities_read_model.c.company_name,
                    opportunities_read_model.c.job_title_original,
                    opportunities_read_model.c.user_status,
                    opportunities_read_model.c.notes,
                )
            )
            .mappings()
            .one()
        )
        source = (
            connection.execute(
                sa.select(
                    job_sources_read_model.c.opportunity_id,
                    job_sources_read_model.c.source_type,
                )
            )
            .mappings()
            .one()
        )

    assert opportunity["opportunity_id"] == source["opportunity_id"]
    assert opportunity["company_name"] == "Acme Example"
    assert opportunity["job_title_original"] == "Backend Engineer"
    assert opportunity["user_status"] == "new"
    assert opportunity["notes"] is None
    assert source["source_type"] == "greenhouse"


def test_retry_after_lost_response_does_not_duplicate_a_row(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, transport = dispatcher(engine, sheet)
    transport.after_write_error = GoogleSheetsError("timeout", "timed out")

    first = sync.dispatch_due()

    assert first.failed == 2
    assert {row["status"] for row in outbox(engine)} == {"retry_wait"}
    rows_after_first = len(sheet.rows("Opportunities"))

    transport.after_write_error = None
    later = NOW + timedelta(minutes=5)
    sync_later = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    )
    second = sync_later.dispatch_due()

    assert second.synced == 2
    assert len(sheet.rows("Opportunities")) == rows_after_first
    assert len(sheet.rows("Opportunities")) == 2


def test_existing_row_is_updated_in_place(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)
    sync.dispatch_due()

    stage(engine, run_id="run_2", records=[record(title="Senior Backend Engineer")])
    later = NOW + timedelta(minutes=10)
    SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()

    assert len(sheet.rows("Opportunities")) == 2
    assert sheet.rows("Opportunities")[1][2] == "Senior Backend Engineer"


def test_user_status_edited_in_the_sheet_survives_a_resync(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)
    sync.dispatch_due()

    user_status_index = OPPORTUNITY_HEADERS.index("user_status")
    sheet.rows("Opportunities")[1][user_status_index] = "saved"

    stage(engine, run_id="run_2", records=[record(title="Backend Engineer II")])
    later = NOW + timedelta(minutes=10)
    SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()

    row = sheet.rows("Opportunities")[1]
    assert row[user_status_index] == "saved"
    assert row[2] == "Backend Engineer II"


def test_new_row_initializes_user_status_to_the_contract_default(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()

    dispatcher(engine, sheet)[0].dispatch_due()

    user_status_index = OPPORTUNITY_HEADERS.index("user_status")
    assert sheet.rows("Opportunities")[1][user_status_index] == "new"


def test_duplicate_stable_ids_in_the_sheet_block_the_projection(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)
    sync.dispatch_due()

    duplicate = list(sheet.rows("Opportunities")[1])
    sheet.rows("Opportunities").append(duplicate)

    stage(engine, run_id="run_2", records=[record(title="Renamed")])
    later = NOW + timedelta(minutes=10)
    SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()

    opportunity_outbox = [row for row in outbox(engine) if row["entity_type"] == "opportunity"]
    assert opportunity_outbox[0]["status"] == "blocked"
    assert opportunity_outbox[0]["last_error_code"] == "duplicate_identity"


def test_a_job_source_is_blocked_when_its_parent_opportunity_is_absent(
    engine: Engine,
) -> None:
    stage(engine, run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(sync_outbox.delete().where(sync_outbox.c.entity_type == "opportunity"))
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)

    outcome = sync.dispatch_due()

    assert outcome.blocked == 1
    source_outbox = next(row for row in outbox(engine) if row["entity_type"] == "job_source")
    assert source_outbox["status"] == "blocked"
    assert source_outbox["last_error_code"] == "missing_parent_opportunity"
    # Only the header row exists; no orphan source row reached canonical Sheets.
    assert len(sheet.rows("Job_Sources")) == 1


def test_a_read_model_settlement_failure_is_retried_without_a_partial_write(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sync, _ = dispatcher(engine, FakeSheet())

    def fail_settlement(claim, *, synced_at, read_model, parents=()):
        raise ReadModelSettlementError("the read-model update was rolled back")

    monkeypatch.setattr(sync._repository, "settle_synced", fail_settlement)

    outcome = sync.dispatch_due()

    assert outcome.synced == 0
    assert outcome.failed == 2
    assert {row["status"] for row in outbox(engine)} == {"retry_wait"}
    assert {row["last_error_code"] for row in outbox(engine)} == {"read_model_settlement_failed"}
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(opportunities_read_model)
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(job_sources_read_model)
            ).scalar_one()
            == 0
        )


def test_transient_error_schedules_a_retry_with_backoff(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sync, transport = dispatcher(engine, FakeSheet())
    transport.failures = [
        GoogleSheetsError("server_error", "unavailable"),
        GoogleSheetsError("server_error", "unavailable"),
    ]

    outcome = sync.dispatch_due()

    assert outcome.failed == 2
    for row in outbox(engine):
        assert row["status"] == "retry_wait"
        assert row["last_error_code"] == "server_error"
        assert row["next_attempt_at_utc"] is not None
        assert row["attempt_count"] == 1


def test_retry_wait_delay_grows_with_attempts(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sync, transport = dispatcher(engine, FakeSheet())

    delays: list[int] = []
    current = NOW
    for _ in range(3):
        transport.failures = [
            GoogleSheetsError("timeout", "timed out"),
            GoogleSheetsError("timeout", "timed out"),
        ]
        sync_at = SyncDispatcher(
            engine,
            transport=transport,
            spreadsheet_id="test_spreadsheet_id_1234567890",
            clock=lambda moment=current: moment,
            max_batch_ranges=3,
        )
        sync_at.dispatch_due()
        for row in outbox(engine):
            if row["status"] == "retry_wait":
                next_at = datetime.fromisoformat(
                    str(row["next_attempt_at_utc"]).replace("Z", "+00:00")
                )
                delays.append(int((next_at - current).total_seconds()))
                current = next_at
                break

    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_retry_after_is_respected_when_it_is_bounded(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    transport = FakeTransport(sheet)
    limited = GoogleSheetsError("rate_limited", "slow down")
    limited.retry_after_seconds = 12
    transport.failures = [limited, limited]
    sync = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW,
    )

    sync.dispatch_due()

    for row in outbox(engine):
        next_at = datetime.fromisoformat(str(row["next_attempt_at_utc"]).replace("Z", "+00:00"))
        assert int((next_at - NOW).total_seconds()) == 12


def test_an_unreasonable_retry_after_is_capped(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    transport = FakeTransport(FakeSheet())
    limited = GoogleSheetsError("rate_limited", "slow down")
    limited.retry_after_seconds = 86_400
    transport.failures = [limited, limited]
    sync = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW,
    )

    sync.dispatch_due()

    for row in outbox(engine):
        next_at = datetime.fromisoformat(str(row["next_attempt_at_utc"]).replace("Z", "+00:00"))
        assert int((next_at - NOW).total_seconds()) <= 300


@pytest.mark.parametrize(
    ("code", "reconnect"),
    [
        ("reconnect_required", True),
        ("permission_denied", True),
        ("spreadsheet_not_found", False),
        ("schema_mismatch", False),
    ],
)
def test_permanent_errors_block_the_projection(
    engine: Engine,
    code: str,
    reconnect: bool,
) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sync, transport = dispatcher(engine, FakeSheet())
    transport.failures = [
        GoogleSheetsError(code, "permanent", reconnect_required=reconnect),
        GoogleSheetsError(code, "permanent", reconnect_required=reconnect),
    ]

    sync.dispatch_due()

    for row in outbox(engine):
        assert row["status"] == "blocked"
        assert row["last_error_code"] == code


def test_max_attempts_stops_retrying(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(attempt_count=4, max_attempts=5))
    sync, transport = dispatcher(engine, FakeSheet())
    transport.failures = [
        GoogleSheetsError("server_error", "unavailable"),
        GoogleSheetsError("server_error", "unavailable"),
    ]

    sync.dispatch_due()

    for row in outbox(engine):
        assert row["status"] == "blocked"
        assert row["last_error_code"] == "server_error"
        assert row["attempt_count"] == 5


def test_payload_change_during_send_keeps_the_newer_work_pending(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, transport = dispatcher(engine, sheet)

    original = transport.batch_update_values
    mutated: list[bool] = []

    def mutate_then_write(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        if not mutated:
            mutated.append(True)
            with engine.begin() as connection:
                connection.execute(
                    sync_outbox.update().values(payload_hash="newer-hash", status="pending")
                )
        return original(
            spreadsheet_id,
            data,
            value_input_option=value_input_option,
        )

    transport.batch_update_values = mutate_then_write  # type: ignore[method-assign]

    sync.dispatch_due()

    for row in outbox(engine):
        assert row["status"] == "pending"
        assert row["payload_hash"] == "newer-hash"
        assert row["synced_at_utc"] is None


def test_stale_worker_cannot_mark_a_newer_payload_synced(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    repository_claims: list[Any] = []
    sync, transport = dispatcher(engine, sheet)

    class CapturingDispatcher(SyncDispatcher):
        def _after_claim(self, claims: list[Any]) -> None:
            repository_claims.extend(claims)

    capture = CapturingDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW,
    )
    original = transport.batch_update_values

    def mutate_after_claim(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        with engine.begin() as connection:
            connection.execute(
                sync_outbox.update()
                .where(sync_outbox.c.outbox_id == repository_claims[0].outbox_id)
                .values(payload_hash="brand-new", status="pending")
            )
        return original(
            spreadsheet_id,
            data,
            value_input_option=value_input_option,
        )

    transport.batch_update_values = mutate_after_claim  # type: ignore[method-assign]

    capture.dispatch_due()

    changed = [row for row in outbox(engine) if row["outbox_id"] == repository_claims[0].outbox_id][
        0
    ]
    assert changed["status"] == "pending"
    assert changed["payload_hash"] == "brand-new"


def test_dispatch_does_not_run_connectors_or_consume_quota(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage(engine, run_id="run_1", records=[record()])

    from personal_job_finder_api.search import connectors as connectors_module

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dispatch must never execute a connector")

    monkeypatch.setattr(connectors_module, "connector_snapshot", explode)

    outcome = dispatcher(engine, FakeSheet())[0].dispatch_due()

    assert outcome.synced == 2


def test_reordered_header_blocks_instead_of_writing_to_wrong_columns(
    engine: Engine,
) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    reordered = list(sheet.rows("Opportunities")[0])
    reordered[1], reordered[2] = reordered[2], reordered[1]
    sheet.rows("Opportunities")[0] = reordered
    sync, _ = dispatcher(engine, sheet)

    outcome = sync.dispatch_due()

    # The source cannot be confirmed against an unreadable parent tab, so both
    # claims block instead of publishing an orphan source row.
    assert outcome.blocked == 2
    opportunity_row = next(row for row in outbox(engine) if row["entity_type"] == "opportunity")
    assert opportunity_row["status"] == "blocked"
    assert opportunity_row["last_error_code"] == "schema_mismatch"
    source_row = next(row for row in outbox(engine) if row["entity_type"] == "job_source")
    assert source_row["status"] == "blocked"
    assert source_row["last_error_code"] == "schema_mismatch"
    assert len(sheet.rows("Opportunities")) == 1
    assert len(sheet.rows("Job_Sources")) == 1


def test_a_second_overlapping_dispatch_is_refused(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, transport = dispatcher(engine, sheet)
    original = transport.batch_update_values
    errors: list[Exception] = []

    def reenter(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        if not errors:
            try:
                sync.dispatch_due()
            except RuntimeError as error:
                errors.append(error)
        return original(
            spreadsheet_id,
            data,
            value_input_option=value_input_option,
        )

    transport.batch_update_values = reenter  # type: ignore[method-assign]

    sync.dispatch_due()

    assert errors
    assert "already running" in str(errors[0])


def test_missing_tab_blocks_work_with_a_schema_error(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    del sheet.tabs["Job_Sources"]
    sync, _ = dispatcher(engine, sheet)

    outcome = sync.dispatch_due()

    assert outcome.blocked >= 1
    source_rows = [row for row in outbox(engine) if row["entity_type"] == "job_source"]
    assert source_rows[0]["status"] == "blocked"
    assert source_rows[0]["last_error_code"] == "schema_mismatch"


def test_batch_size_is_enforced(engine: Engine) -> None:
    stage(
        engine,
        run_id="run_1",
        records=[record(external_id=str(index)) for index in range(10)],
    )
    sheet = FakeSheet()
    sync, transport = dispatcher(engine, sheet)

    sync.dispatch_due()

    writes = [call for call in sheet.calls if call[0] == "batch_update_values"]
    assert writes
    for _name, payload in writes:
        assert len(payload[1]) <= 3


def test_dispatch_reports_an_invalid_spreadsheet_id_without_leaking_it(
    engine: Engine,
) -> None:
    stage(engine, run_id="run_1", records=[record()])
    transport = FakeTransport(FakeSheet())
    sync = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="not a valid/id",
        clock=lambda: NOW,
    )

    outcome = sync.dispatch_due()

    assert outcome.blocked == 2
    assert "not a valid/id" not in json.dumps(outcome.as_dict())


def test_unknown_entity_type_is_blocked_rather_than_guessed(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    with engine.begin() as connection:
        connection.execute(sync_outbox.update().values(entity_type="contact"))
    sync, _ = dispatcher(engine, FakeSheet())

    outcome = sync.dispatch_due()

    assert outcome.blocked == 2
    assert all(row["status"] == "blocked" for row in outbox(engine))


def test_excluded_opportunity_is_written_as_a_non_destructive_withdrawal(
    engine: Engine,
) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    opportunities_row = len(sheet.rows("Opportunities"))
    job_sources_row = len(sheet.rows("Job_Sources"))
    user_status_index = OPPORTUNITY_HEADERS.index("user_status")
    sheet.rows("Opportunities")[1][user_status_index] = "saved"

    stage(engine, run_id="run_2", records=[record(location="Singapore")])
    later = NOW + timedelta(minutes=10)
    SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()

    assert len(sheet.rows("Opportunities")) == opportunities_row
    assert len(sheet.rows("Job_Sources")) == job_sources_row
    status_index = OPPORTUNITY_HEADERS.index("location_status")
    assert sheet.rows("Opportunities")[1][status_index] == "excluded"
    assert sheet.rows("Opportunities")[1][user_status_index] == "saved"


def test_a_withdrawal_of_a_never_written_source_still_settles(
    engine: Engine,
) -> None:
    """Nothing reached the sheet, so there is no orphan risk and nothing to write."""

    with engine.begin() as connection:
        connection.execute(
            sync_outbox.insert().values(
                outbox_id="outbox_ghost",
                run_id="run_1",
                destination="google_sheets",
                entity_type="job_source",
                entity_id="src_ghost",
                operation="withdraw",
                payload_json=json.dumps(
                    {
                        "source_id": "src_ghost",
                        "opportunity_id": "opp_ghost",
                        "source_type": "greenhouse",
                        "observation_status": "seen",
                    }
                ),
                payload_hash="hash_ghost",
                status="pending",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at_utc=None,
                last_error_code=None,
                last_error_at_utc=None,
                created_at_utc="2026-11-15T04:00:00.000000Z",
                updated_at_utc="2026-11-15T04:00:00.000000Z",
                synced_at_utc=None,
            )
        )
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)

    outcome = sync.dispatch_due()

    assert outcome.synced == 1
    assert outcome.blocked == 0
    assert outbox(engine)[0]["status"] == "synced"
    assert len(sheet.rows("Job_Sources")) == 1


def test_a_source_settles_when_its_parent_exists_only_in_the_sheet(
    engine: Engine,
) -> None:
    """Cold start: the parent is canonical in Sheets but absent from SQLite.

    The read model is a rebuildable cache and can be empty before the first
    refresh, while the spreadsheet already holds earlier rows. A verified source
    write must then still settle instead of failing the foreign key on every
    retry.
    """

    parent = project_opportunity(
        {
            "opportunity_id": "opp_cold",
            "company_name": "Acme Example",
            "job_title": "Backend Engineer",
            "role_match_type": "canonical_title",
            "location_status": "eligible",
            "work_mode": "onsite",
            "remote_scope": "not_applicable",
            "availability_status": "unknown",
            "user_status": "new",
        }
    )
    sheet = FakeSheet()
    sheet.rows("Opportunities").append(list(parent.values))
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.insert().values(
                outbox_id="outbox_cold_source",
                run_id="run_1",
                destination="google_sheets",
                entity_type="job_source",
                entity_id="src_cold",
                operation="upsert",
                payload_json=json.dumps(
                    {
                        "source_id": "src_cold",
                        "opportunity_id": "opp_cold",
                        "source_type": "greenhouse",
                        "safe_source_url": "https://boards.greenhouse.io/acme/jobs/1001",
                        "external_source_id": "1001",
                        "captured_at_utc": "2026-11-15T05:00:00.000000Z",
                        "observation_status": "seen",
                    }
                ),
                payload_hash="hash_cold",
                status="pending",
                attempt_count=0,
                max_attempts=5,
                next_attempt_at_utc=None,
                last_error_code=None,
                last_error_at_utc=None,
                created_at_utc="2026-11-15T04:00:00.000000Z",
                updated_at_utc="2026-11-15T04:00:00.000000Z",
                synced_at_utc=None,
            )
        )
    sync, _ = dispatcher(engine, sheet)

    outcome = sync.dispatch_due()

    assert outcome.synced == 1
    assert outcome.blocked == 0
    assert outbox(engine)[0]["status"] == "synced"
    with engine.connect() as connection:
        assert (
            connection.execute(sa.select(opportunities_read_model.c.opportunity_id)).scalar_one()
            == "opp_cold"
        )
        assert (
            connection.execute(sa.select(job_sources_read_model.c.opportunity_id)).scalar_one()
            == "opp_cold"
        )


def test_recovery_then_dispatch_reconciles_instead_of_duplicating(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()

    # A title that still matches the configured role, so this is an update and not
    # a withdrawal.
    stage(engine, run_id="run_2", records=[record(title="Senior Backend Engineer")])
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update().values(
                status="in_progress",
                updated_at_utc="2026-11-15T04:00:00.000000Z",
            )
        )
    rows_before = len(sheet.rows("Opportunities"))

    later = NOW + timedelta(minutes=10)
    sync_later = SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    )
    recovered = sync_later.recover()
    outcome = sync_later.dispatch_due()

    assert recovered == 2
    # The opportunity changed, so it is rewritten. The source row's payload is
    # unchanged, so after recovery it is reconciled as already current.
    assert outcome.synced >= 1
    assert outcome.blocked == 0
    assert len(sheet.rows("Opportunities")) == rows_before
    assert sheet.rows("Opportunities")[1][2] == "Senior Backend Engineer"


def test_dispatch_returns_an_empty_outcome_when_nothing_is_due(engine: Engine) -> None:
    sync, _ = dispatcher(engine, FakeSheet())

    outcome = sync.dispatch_due()

    assert outcome == SyncOutcome(synced=0, failed=0, blocked=0, remaining=0)


def test_synced_rows_are_not_claimed_again(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, _ = dispatcher(engine, sheet)
    sync.dispatch_due()

    second = sync.dispatch_due()

    assert second.synced == 0
    assert second.remaining == 0


def test_dispatcher_keeps_local_rows_intact_when_blocked(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    with engine.connect() as connection:
        before = connection.execute(
            sa.select(sa.func.count()).select_from(opportunities)
        ).scalar_one()
        sources_before = connection.execute(
            sa.select(sa.func.count()).select_from(job_sources)
        ).scalar_one()
    sync, transport = dispatcher(engine, FakeSheet())
    transport.failures = [
        GoogleSheetsError("reconnect_required", "reconnect", reconnect_required=True),
        GoogleSheetsError("reconnect_required", "reconnect", reconnect_required=True),
    ]

    sync.dispatch_due()

    with engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(opportunities)).scalar_one()
            == before
        )
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(job_sources)).scalar_one()
            == sources_before
        )


def test_a_tracker_patch_writes_only_its_columns_and_keeps_discovery_values(
    engine: Engine,
) -> None:
    """A tracker-only payload must not blank the canonical discovery columns."""

    from personal_job_finder_api.search.tracker import OpportunityTrackerService

    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    opportunity_row = next(row for row in outbox(engine) if row["entity_type"] == "opportunity")
    opportunity_id = str(opportunity_row["entity_id"])
    assert opportunity_row["status"] == "synced"

    later = NOW + timedelta(minutes=10)
    OpportunityTrackerService(engine, clock=lambda: later).update(
        opportunity_id,
        {"application_status": "Applied", "notes": "Recruiter replied"},
    )
    SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()

    written = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))
    assert written["application_status"] == "Applied"
    assert written["notes"] == "Recruiter replied"
    # Everything the patch did not supply is preserved from the canonical row.
    assert written["company_name"] == "Acme Example"
    assert written["job_title_original"] == "Backend Engineer"
    assert written["job_title_normalized"] == "backend engineer"
    assert written["user_status"] == "new"
    assert len(sheet.rows("Opportunities")) == 2


# ---------------------------------------------------------------------------
# Tracker-only payloads must never recreate a deleted canonical row (M4C3A hardening)
# ---------------------------------------------------------------------------


def _synced_tracker_only_claim(engine: Engine, sheet: FakeSheet) -> str:
    """Stage, sync, then queue a tracker-only edit; return the opportunity id."""

    from personal_job_finder_api.search.tracker import OpportunityTrackerService

    stage(engine, run_id="run_1", records=[record()])
    dispatcher(engine, sheet)[0].dispatch_due()
    row = next(entry for entry in outbox(engine) if entry["entity_type"] == "opportunity")
    opportunity_id = str(row["entity_id"])
    assert row["status"] == "synced"

    later = NOW + timedelta(minutes=10)
    OpportunityTrackerService(engine, clock=lambda: later).update(
        opportunity_id,
        {"application_status": "Applied", "notes": "Waiting for reply"},
    )
    return opportunity_id


def test_a_tracker_only_payload_updates_the_existing_canonical_row(engine: Engine) -> None:
    sheet = FakeSheet()
    opportunity_id = _synced_tracker_only_claim(engine, sheet)
    rows_before = len(sheet.rows("Opportunities"))
    discovery_before = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    assert outcome.synced == 1
    assert len(sheet.rows("Opportunities")) == rows_before
    written = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))
    assert written["opportunity_id"] == opportunity_id
    assert written["application_status"] == "Applied"
    assert written["notes"] == "Waiting for reply"
    for column in ("company_name", "job_title_original", "job_title_normalized", "location_raw"):
        assert written[column] == discovery_before[column]


def test_a_tracker_only_payload_never_recreates_a_deleted_canonical_row(
    engine: Engine,
) -> None:
    sheet = FakeSheet()
    opportunity_id = _synced_tracker_only_claim(engine, sheet)

    # The user deletes the canonical row after the local refresh and the edit.
    del sheet.rows("Opportunities")[1]
    rows_before = len(sheet.rows("Opportunities"))
    sources_before = [list(row) for row in sheet.rows("Job_Sources")]
    outbox_before = next(entry for entry in outbox(engine) if entry["entity_type"] == "opportunity")

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    # Nothing was appended or written.
    assert len(sheet.rows("Opportunities")) == rows_before
    assert sheet.rows("Job_Sources") == sources_before
    assert outcome.synced == 0

    # The work is blocked, not silently dropped and not marked synced.
    stored = next(entry for entry in outbox(engine) if entry["entity_type"] == "opportunity")
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_row_missing"
    assert stored["entity_id"] == opportunity_id
    assert stored["synced_at_utc"] is None
    assert stored["outbox_id"] == outbox_before["outbox_id"]

    # The pending tracker edit is still recoverable from the stored payload.
    payload = json.loads(str(stored["payload_json"]))
    assert payload["application_status"] == "Applied"
    assert payload["notes"] == "Waiting for reply"
    assert sorted(payload["_pending_user_fields"]) == ["application_status", "notes"]

    # The read model keeps the previously synchronized values and is not updated
    # as though the blocked tracker edit had been published.
    with engine.connect() as connection:
        stored_row = (
            connection.execute(
                sa.select(
                    opportunities_read_model.c.application_status,
                    opportunities_read_model.c.notes,
                )
            )
            .mappings()
            .one()
        )
    assert stored_row["application_status"] is None
    assert stored_row["notes"] is None


def test_a_blocked_missing_canonical_row_recovers_after_the_row_is_restored(
    engine: Engine,
) -> None:
    from personal_job_finder_api.repositories.sync_outbox import OutboxRepository

    sheet = FakeSheet()
    opportunity_id = _synced_tracker_only_claim(engine, sheet)
    canonical_row = list(sheet.rows("Opportunities")[1])
    del sheet.rows("Opportunities")[1]

    dispatcher(engine, sheet)[0].dispatch_due()
    blocked = next(entry for entry in outbox(engine) if entry["entity_type"] == "opportunity")
    assert blocked["status"] == "blocked"
    assert blocked["last_error_code"] == "canonical_row_missing"

    # The user restores the canonical row, then uses the existing requeue flow.
    sheet.rows("Opportunities").append(canonical_row)
    later = NOW + timedelta(minutes=30)
    assert OutboxRepository(engine).requeue_blocked(
        outbox_id=str(blocked["outbox_id"]),
        now=later,
    )

    outcome = SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()

    assert outcome.synced == 1
    assert len(sheet.rows("Opportunities")) == 2
    written = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))
    assert written["opportunity_id"] == opportunity_id
    assert written["application_status"] == "Applied"
    assert written["company_name"] == "Acme Example"


def test_a_complete_discovery_payload_still_inserts_a_new_row(engine: Engine) -> None:
    sheet = FakeSheet()

    stage(engine, run_id="run_1", records=[record()])
    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    assert outcome.synced == 2
    assert len(sheet.rows("Opportunities")) == 2
    written = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))
    assert written["company_name"] == "Acme Example"
    assert written["job_title_original"] == "Backend Engineer"


def test_a_transient_read_failure_is_not_reported_as_a_missing_canonical_row(
    engine: Engine,
) -> None:
    sheet = FakeSheet()
    _synced_tracker_only_claim(engine, sheet)
    sync, transport = dispatcher(engine, sheet)
    transport.read_error = GoogleSheetsError("server_error", "unavailable")

    outcome = sync.dispatch_due()

    assert outcome.synced == 0
    stored = next(entry for entry in outbox(engine) if entry["entity_type"] == "opportunity")
    assert stored["status"] == "retry_wait"
    assert stored["last_error_code"] == "server_error"
    assert stored["next_attempt_at_utc"] is not None
    assert len(sheet.rows("Opportunities")) == 2


def test_a_blocked_tracker_only_payload_does_not_write_unrelated_sources(
    engine: Engine,
) -> None:
    from personal_job_finder_api.db.tables import sync_outbox

    sheet = FakeSheet()
    _synced_tracker_only_claim(engine, sheet)
    sources_before = [list(row) for row in sheet.rows("Job_Sources")]

    # The provenance row has queued work of its own when the parent disappears.
    with engine.begin() as connection:
        connection.execute(
            sync_outbox.update()
            .where(sync_outbox.c.entity_type == "job_source")
            .values(status="pending", attempt_count=0)
        )
    del sheet.rows("Opportunities")[1]

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    assert sheet.rows("Job_Sources") == sources_before
    assert outcome.synced == 0
    stored = {str(row["entity_type"]): str(row["last_error_code"]) for row in outbox(engine)}
    assert stored["opportunity"] == "canonical_row_missing"
    assert stored["job_source"] == "missing_parent_opportunity"


# ---------------------------------------------------------------------------
# Row-shift protection (M4C3A-H). Each test moves rows between the dispatcher's
# read and its write, exactly where a manual spreadsheet edit could land.
# ---------------------------------------------------------------------------


def _shift_before_revalidation(
    engine: Engine,
    sheet: FakeSheet,
    transport: FakeTransport,
    mutate: Any,
) -> None:
    """Run ``mutate(sheet)`` once, after the initial table read and before revalidation.

    The first ``batch_get_values`` after the initial full-table read is the
    pre-write revalidation read. Mutating before it models a manual edit that lands
    between the planning read and the revalidation.
    """

    original = transport.batch_get_values
    reads: list[int] = []

    def shifting_read(spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
        reads.append(1)
        if len(reads) == 2:  # 1 = initial table read, 2 = revalidation read
            mutate(sheet)
        return original(spreadsheet_id, ranges)

    transport.batch_get_values = shifting_read  # type: ignore[method-assign]


def _insert_row_above_target(sheet: FakeSheet) -> None:
    """A user inserts an unrelated record above the target's physical row."""

    other = list(sheet.rows("Opportunities")[1])
    other[OPPORTUNITY_HEADERS.index("opportunity_id")] = "opp_intruder"
    other[OPPORTUNITY_HEADERS.index("company_name")] = "Intruder Corp"
    sheet.rows("Opportunities").insert(1, other)


def test_a_row_inserted_above_the_target_is_not_overwritten(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    # Existing target is now at row 2. Queue a tracker edit against it.
    _queue_tracker_edit(engine)

    sync, transport = dispatcher(engine, sheet)
    _shift_before_revalidation(engine, sheet, transport, _insert_row_above_target)

    sync.dispatch_due()
    intruder = [r for r in sheet.rows("Opportunities") if r[0] == "opp_intruder"]
    assert len(intruder) == 1
    assert intruder[0][OPPORTUNITY_HEADERS.index("company_name")] == "Intruder Corp"
    assert intruder[0][OPPORTUNITY_HEADERS.index("notes")] == ""
    target_rows = [r for r in sheet.rows("Opportunities") if r[0].startswith("opp_")]
    target_rows = [r for r in target_rows if r[0] != "opp_intruder"]
    assert len(target_rows) == 1
    stored = next(e for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert stored["status"] != "synced"


def _queue_tracker_edit(engine: Engine) -> None:
    from personal_job_finder_api.search.tracker import OpportunityTrackerService

    opportunity_id = str(
        next(e for e in outbox(engine) if e["entity_type"] == "opportunity")["entity_id"]
    )
    OpportunityTrackerService(engine, clock=lambda: NOW + timedelta(minutes=10)).update(
        opportunity_id,
        {"application_status": "Applied"},
    )


def _delete_row_above_target(sheet: FakeSheet) -> None:
    """A user deletes a record above the target, so the target moves up one row."""

    # Add an unrelated record above the target, then delete it. Row indices of the
    # target therefore shift by one relative to the plan, and a different record
    # (the filler) moves into the planned row.
    del sheet.rows("Opportunities")[1]


def test_a_row_deleted_above_the_target_is_not_overwritten(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    # Add a second, unrelated record below the target so a shift has something to hit.
    other_before = list(sheet.rows("Opportunities")[1])
    filler = list(other_before)
    filler[OPPORTUNITY_HEADERS.index("opportunity_id")] = "opp_filler"
    filler[OPPORTUNITY_HEADERS.index("company_name")] = "Filler Corp"
    sheet.rows("Opportunities").append(filler)
    _queue_tracker_edit(engine)

    sync, transport = dispatcher(engine, sheet)
    _shift_before_revalidation(engine, sheet, transport, _delete_row_above_target)
    sync.dispatch_due()

    filler_rows = [r for r in sheet.rows("Opportunities") if r[0] == "opp_filler"]
    assert len(filler_rows) == 1
    assert filler_rows[0][OPPORTUNITY_HEADERS.index("company_name")] == "Filler Corp"
    assert filler_rows[0][OPPORTUNITY_HEADERS.index("application_status")] == ""
    stored = next(e for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert stored["status"] == "retry_wait"
    assert stored["last_error_code"] == "row_moved"


def _delete_target_and_shift_replacement_in(sheet: FakeSheet) -> None:
    """The target is deleted; an unrelated record shifts into its old row."""

    assert sheet.rows("Opportunities")[1][0].startswith("opp_")
    del sheet.rows("Opportunities")[1]


def test_a_deleted_target_is_not_replaced_by_the_shifted_record(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    filler = list(sheet.rows("Opportunities")[1])
    filler[OPPORTUNITY_HEADERS.index("opportunity_id")] = "opp_filler"
    filler[OPPORTUNITY_HEADERS.index("company_name")] = "Filler Corp"
    sheet.rows("Opportunities").append(filler)
    _queue_tracker_edit(engine)

    sync, transport = dispatcher(engine, sheet)
    _shift_before_revalidation(engine, sheet, transport, _delete_target_and_shift_replacement_in)
    sync.dispatch_due()

    # The filler now sits in the target's old row. It must be untouched.
    replacement = sheet.rows("Opportunities")[1]
    assert replacement[0] == "opp_filler"
    assert replacement[OPPORTUNITY_HEADERS.index("company_name")] == "Filler Corp"
    assert replacement[OPPORTUNITY_HEADERS.index("application_status")] == ""
    # This dispatch detected the stale plan and retries it safely; nothing is synced.
    stored = next(e for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert stored["status"] == "retry_wait"
    assert stored["last_error_code"] == "row_moved"

    # A fresh dispatch reads the table again, finds the target absent, and the
    # existing guard blocks the tracker-only write instead of recreating the row.
    SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW + timedelta(hours=1),
    ).dispatch_due()
    stored = next(e for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_row_missing"
    assert sheet.rows("Opportunities")[1][0] == "opp_filler"
    assert len(sheet.rows("Opportunities")) == 2


def test_a_row_moved_before_a_new_dispatch_is_updated_at_its_new_position(
    engine: Engine,
) -> None:
    """Fresh planning must follow the target to its new physical row."""

    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    _queue_tracker_edit(engine)
    # Move the target from row 2 to row 3 before any dispatch runs.
    _insert_row_above_target(sheet)

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    assert outcome.synced == 1
    assert len(sheet.rows("Opportunities")) == 3
    target_index = next(
        i for i, r in enumerate(sheet.rows("Opportunities")) if r[0] == _target_id(sheet)
    )
    written = sheet.rows("Opportunities")[target_index]
    assert written[OPPORTUNITY_HEADERS.index("application_status")] == "Applied"
    intruder = next(r for r in sheet.rows("Opportunities") if r[0] == "opp_intruder")
    assert intruder[OPPORTUNITY_HEADERS.index("company_name")] == "Intruder Corp"
    assert intruder[OPPORTUNITY_HEADERS.index("application_status")] == ""


def _target_id(sheet: FakeSheet) -> str:
    return next(
        r[0]
        for r in sheet.rows("Opportunities")
        if r[0].startswith("opp_") and r[0] != "opp_intruder"
    )


def test_a_planned_append_that_became_occupied_is_not_overwritten(engine: Engine) -> None:
    """A new discovery row planned for the end must not land on a row filled meanwhile."""

    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()

    sync, transport = dispatcher(engine, sheet)

    def occupy_next_free_row(s: FakeSheet) -> None:
        # Someone types a record into the row the plan just chose for the append.
        squatter = [""] * len(OPPORTUNITY_HEADERS)
        squatter[0] = "opp_squatter"
        squatter[OPPORTUNITY_HEADERS.index("company_name")] = "Squatter Ltd"
        s.rows("Opportunities").append(squatter)

    _shift_before_revalidation(engine, sheet, transport, occupy_next_free_row)
    sync.dispatch_due()

    squatter = [r for r in sheet.rows("Opportunities") if r[0] == "opp_squatter"]
    assert len(squatter) == 1
    assert squatter[0][OPPORTUNITY_HEADERS.index("company_name")] == "Squatter Ltd"
    assert squatter[0][OPPORTUNITY_HEADERS.index("job_title_original")] == ""
    # The discovery claim was not written over the squatter; it is retryable.
    opp = next(e for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert opp["status"] == "retry_wait"
    assert opp["last_error_code"] == "row_moved"


def test_only_the_stale_plan_is_deferred_and_the_other_row_is_written(engine: Engine) -> None:
    """Within one chunk, a stale plan must not redirect or block its neighbours."""

    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )
    sheet = FakeSheet()

    sync, transport = dispatcher(engine, sheet)

    def insert_above_first_planned(s: FakeSheet) -> None:
        # Only the first planned append is invalidated: someone fills its row.
        squatter = [""] * len(OPPORTUNITY_HEADERS)
        squatter[0] = "opp_squatter"
        squatter[OPPORTUNITY_HEADERS.index("company_name")] = "Squatter Ltd"
        s.rows("Opportunities").append(squatter)

    _shift_before_revalidation(engine, sheet, transport, insert_above_first_planned)
    sync.dispatch_due()

    # Exactly one planned opportunity is deferred; the other goes to its own row.
    ids = [r[0] for r in sheet.rows("Opportunities")[1:]]
    assert ids.count("opp_squatter") == 1
    written = [
        r
        for r in sheet.rows("Opportunities")[1:]
        if r[0].startswith("opp_") and r[0] != "opp_squatter"
    ]
    assert len(written) == 1
    assert written[0][OPPORTUNITY_HEADERS.index("job_title_original")] in {
        "Backend Engineer",
        "Senior Backend Engineer",
    }
    statuses = sorted(str(e["status"]) for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert statuses == ["retry_wait", "synced"]


def _shifted_middle_row(engine, sheet, transport, mutate) -> None:
    pass


# ---------------------------------------------------------------------------
# M4C3A-H item 7 (from investigation): post-write verification improvements
# ---------------------------------------------------------------------------


def test_post_write_verification_detects_an_overwrite_between_revalidate_and_write(
    engine: Engine,
) -> None:
    """A write that lands on a stranger's row must not be settled as synced.

    Revalidation closes the read→revalidate window. This test targets the
    remaining revalidate→write window: the sheet changes after revalidation
    approves the plan, so the write overwrites an unrelated row.
    """

    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    _queue_tracker_edit(engine)

    sync, transport = dispatcher(engine, sheet)
    original = transport.batch_update_values

    def mutate_then_write(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        # A user inserts an unlrelated row above the target after revalidation
        # approved row 2 for the target. The write below now overwrites it.
        stranger = [""] * len(OPPORTUNITY_HEADERS)
        stranger[0] = "opp_mystery"
        stranger[OPPORTUNITY_HEADERS.index("company_name")] = "Mystery Co"
        sheet.rows("Opportunities").insert(1, stranger)
        return original(spreadsheet_id, data, value_input_option=value_input_option)

    transport.batch_update_values = mutate_then_write  # type: ignore[method-assign]

    sync.dispatch_due()

    # The stranger's row was overwritten by the target: the target now appears
    # twice, which the post-write verification must catch as an anomaly.
    identities = [r[0] for r in sheet.rows("Opportunities")[1:]]
    assert identities.count("opp_mystery") == 0  # overwritten — the damage happened
    stored = next(e for e in outbox(engine) if e["entity_type"] == "opportunity")
    assert stored["status"] == "blocked"  # never settled as success
    assert stored["last_error_code"] == "canonical_identity_map_changed"

    # Detection is not rollback: the overwritten row stays overwritten. The next
    # dispatch re-reads and finds the target at its (shifted) location.
    fresh = dispatcher(engine, sheet)[0]
    fresh.dispatch_due()
    rows = sheet.rows("Opportunities")
    assert sum(1 for r in rows if r[0].startswith("opp_")) >= 1


# ---------------------------------------------------------------------------
# M4C3A-H2: complete identity-map verification and optional write counts.
# ---------------------------------------------------------------------------


def _two_synced_opportunities(engine: Engine, sheet: FakeSheet) -> tuple[str, str]:
    """Stage and sync two matching opportunities, returning their row identities."""

    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )
    outcome = dispatcher(engine, sheet)[0].dispatch_due()
    assert outcome.synced == 4
    identities = [row[0] for row in sheet.rows("Opportunities")[1:]]
    assert len(identities) == 2
    return identities[0], identities[1]


def _queue_tracker_edit_for(engine: Engine, opportunity_id: str) -> None:
    from personal_job_finder_api.search.tracker import OpportunityTrackerService

    OpportunityTrackerService(engine, clock=lambda: NOW + timedelta(minutes=10)).update(
        opportunity_id,
        {"application_status": "Applied"},
    )


def _mutate_during_write(transport: FakeTransport, mutate: Any) -> None:
    """Run ``mutate()`` after revalidation approved the plan and before the write."""

    original = transport.batch_update_values

    def mutating_write(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        mutate()
        return original(spreadsheet_id, data, value_input_option=value_input_option)

    transport.batch_update_values = mutating_write  # type: ignore[method-assign]


def _claim(engine: Engine, opportunity_id: str) -> dict[str, Any]:
    return next(
        entry
        for entry in outbox(engine)
        if entry["entity_type"] == "opportunity" and entry["entity_id"] == opportunity_id
    )


def _read_model_status(engine: Engine, opportunity_id: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(opportunities_read_model.c.application_status).where(
                opportunities_read_model.c.opportunity_id == opportunity_id
            )
        ).scalar_one()


def test_deleted_target_shifted_replacement_is_detected_without_a_duplicate(
    engine: Engine,
) -> None:
    """The audit scenario: opp_B shifts into opp_A's row and is silently overwritten."""

    sheet = FakeSheet()
    target_id, victim_id = _two_synced_opportunities(engine, sheet)
    victim_row = list(sheet.rows("Opportunities")[2])
    _queue_tracker_edit_for(engine, target_id)

    def delete_target_only() -> None:
        rows = sheet.rows("Opportunities")
        if rows[1][0] == target_id:
            del rows[1]  # opp_B shifts from row 3 into row 2

    sync, transport = dispatcher(engine, sheet)
    _mutate_during_write(transport, delete_target_only)
    outcome = sync.dispatch_due()

    # The write committed over opp_B. Detection is evidence, not rollback.
    identities = [row[0] for row in sheet.rows("Opportunities")[1:]]
    assert identities == [target_id]  # no duplicate identity exists to give it away
    assert outcome.synced == 0
    assert outcome.blocked == 1
    stored = _claim(engine, target_id)
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_identity_map_changed"
    assert stored["synced_at_utc"] is None
    assert _read_model_status(engine, target_id) is None
    assert json.loads(str(stored["payload_json"]))["application_status"] == "Applied"

    # Operator recovery stays available after the lost row is restored.
    sheet.rows("Opportunities").append(victim_row)
    from personal_job_finder_api.repositories.sync_outbox import OutboxRepository

    later = NOW + timedelta(minutes=30)
    assert OutboxRepository(engine).requeue_blocked(
        outbox_id=str(stored["outbox_id"]),
        now=later,
    )
    recovered = SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: later,
    ).dispatch_due()
    assert recovered.synced == 1
    assert _read_model_status(engine, target_id) == "Applied"


def test_an_existing_identity_that_disappears_after_the_write_is_detected(
    engine: Engine,
) -> None:
    sheet = FakeSheet()
    target_id, victim_id = _two_synced_opportunities(engine, sheet)
    _queue_tracker_edit_for(engine, target_id)

    def delete_victim_only() -> None:
        del sheet.rows("Opportunities")[2]  # the unrelated row at row 3 disappears

    sync, transport = dispatcher(engine, sheet)
    _mutate_during_write(transport, delete_victim_only)
    outcome = sync.dispatch_due()

    assert all(row[0] != victim_id for row in sheet.rows("Opportunities")[1:])
    stored = _claim(engine, target_id)
    assert outcome.synced == 0
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_identity_map_changed"


def test_an_unrelated_identity_moving_row_after_the_write_is_detected(
    engine: Engine,
) -> None:
    sheet = FakeSheet()
    target_id, victim_id = _two_synced_opportunities(engine, sheet)
    _queue_tracker_edit_for(engine, target_id)

    def insert_blank_row_below_target() -> None:
        blank = [""] * len(OPPORTUNITY_HEADERS)
        sheet.rows("Opportunities").insert(2, blank)  # victim moves from row 3 to row 4

    sync, transport = dispatcher(engine, sheet)
    _mutate_during_write(transport, insert_blank_row_below_target)
    outcome = sync.dispatch_due()

    rows = sheet.rows("Opportunities")
    assert rows[1][0] == target_id
    assert rows[3][0] == victim_id  # the unrelated row survived, but not where planned
    stored = _claim(engine, target_id)
    assert outcome.synced == 0
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_identity_map_changed"


def test_a_duplicate_identity_created_after_the_write_is_detected(engine: Engine) -> None:
    sheet = FakeSheet()
    target_id, _victim_id = _two_synced_opportunities(engine, sheet)
    _queue_tracker_edit_for(engine, target_id)

    def duplicate_target_row() -> None:
        rows = sheet.rows("Opportunities")
        rows.append(list(rows[1]))

    sync, transport = dispatcher(engine, sheet)
    _mutate_during_write(transport, duplicate_target_row)
    outcome = sync.dispatch_due()

    identities = [row[0] for row in sheet.rows("Opportunities")[1:]]
    assert identities.count(target_id) == 2
    stored = _claim(engine, target_id)
    assert outcome.synced == 0
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_identity_map_changed"


def test_a_normal_update_without_a_write_count_still_settles(engine: Engine) -> None:
    """The fake transport omits ``totalUpdatedRows``; the identity map must suffice."""

    sheet = FakeSheet()
    stage(engine, run_id="run_1", records=[record()])
    dispatcher(engine, sheet)[0].dispatch_due()
    target_id = sheet.rows("Opportunities")[1][0]
    _queue_tracker_edit_for(engine, target_id)
    before = {i: row[0] for i, row in enumerate(sheet.rows("Opportunities")[1:], 2)}

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    after = {i: row[0] for i, row in enumerate(sheet.rows("Opportunities")[1:], 2)}
    assert after == before
    assert outcome.synced == 1
    written = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))
    assert written["application_status"] == "Applied"
    assert _claim(engine, target_id)["status"] == "synced"


def test_a_normal_append_verifies_the_planned_new_identities(engine: Engine) -> None:
    sheet = FakeSheet()
    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    assert outcome.synced == 4
    identities = [row[0] for row in sheet.rows("Opportunities")[1:]]
    assert len(identities) == 2
    assert len(set(identities)) == 2


def test_multiple_updates_in_one_chunk_keep_the_identity_map(engine: Engine) -> None:
    sheet = FakeSheet()
    first_id, second_id = _two_synced_opportunities(engine, sheet)
    _queue_tracker_edit_for(engine, first_id)
    _queue_tracker_edit_for(engine, second_id)
    before = {i: row[0] for i, row in enumerate(sheet.rows("Opportunities")[1:], 2)}

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    after = {i: row[0] for i, row in enumerate(sheet.rows("Opportunities")[1:], 2)}
    assert after == before
    assert outcome.synced == 2
    assert _claim(engine, first_id)["status"] == "synced"
    assert _claim(engine, second_id)["status"] == "synced"


def test_a_mixed_update_and_insert_chunk_matches_the_expected_map(
    engine: Engine,
) -> None:
    stage(engine, run_id="run_1", records=[record(external_id="1001")])
    sheet = FakeSheet()
    dispatcher(engine, sheet)[0].dispatch_due()
    existing_id = sheet.rows("Opportunities")[1][0]
    _queue_tracker_edit_for(engine, existing_id)
    stage(
        engine,
        run_id="run_2",
        records=[record(external_id="1002", title="Senior Backend Engineer")],
    )

    outcome = dispatcher(engine, sheet)[0].dispatch_due()

    rows = sheet.rows("Opportunities")
    identities = [row[0] for row in rows[1:]]
    assert len(identities) == 2
    assert len(set(identities)) == 2
    assert existing_id in identities
    assert outcome.synced == 3  # tracker update + discovery insert + its source
    assert outcome.blocked == 0
    written = dict(zip(OPPORTUNITY_HEADERS, rows[1], strict=True))
    assert written["application_status"] == "Applied"


def test_later_chunks_include_earlier_verified_writes_in_the_baseline(
    engine: Engine,
) -> None:
    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )
    sheet = FakeSheet()
    sync = SyncDispatcher(
        engine,
        transport=FakeTransport(sheet),
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW,
        batch_size=1,
    )

    outcome = sync.dispatch_due()

    # If a later chunk reused the initial baseline, the earlier write would look
    # like an unexpected identity and this would be blocked instead.
    assert outcome.synced == 4
    assert outcome.blocked == 0
    identities = [row[0] for row in sheet.rows("Opportunities")[1:]]
    assert len(identities) == 2
    assert len(set(identities)) == 2


def test_a_matching_write_count_is_accepted(engine: Engine) -> None:
    sheet = FakeSheet()
    stage(engine, run_id="run_1", records=[record()])
    dispatcher(engine, sheet)[0].dispatch_due()
    target_id = sheet.rows("Opportunities")[1][0]
    _queue_tracker_edit_for(engine, target_id)

    sync, transport = dispatcher(engine, sheet)
    original = transport.batch_update_values
    seen_counts: list[int] = []

    def counted_write(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        response = original(spreadsheet_id, data, value_input_option=value_input_option)
        if any(str(item["range"]).startswith("Opportunities!") for item in data):
            seen_counts.append(len(data))
            return {"totalUpdatedRows": len(data)}
        return response

    transport.batch_update_values = counted_write  # type: ignore[method-assign]
    outcome = sync.dispatch_due()

    assert seen_counts == [1]
    assert outcome.synced == 1
    assert _claim(engine, target_id)["status"] == "synced"


def test_a_write_count_mismatch_blocks_the_ambiguous_claim(engine: Engine) -> None:
    sheet = FakeSheet()
    stage(engine, run_id="run_1", records=[record()])
    dispatcher(engine, sheet)[0].dispatch_due()
    target_id = sheet.rows("Opportunities")[1][0]
    _queue_tracker_edit_for(engine, target_id)

    sync, transport = dispatcher(engine, sheet)
    original = transport.batch_update_values

    def wrong_count(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        response = original(spreadsheet_id, data, value_input_option=value_input_option)
        if any(str(item["range"]).startswith("Opportunities!") for item in data):
            return {"totalUpdatedRows": 0}
        return response

    transport.batch_update_values = wrong_count  # type: ignore[method-assign]
    outcome = sync.dispatch_due()

    stored = _claim(engine, target_id)
    assert outcome.synced == 0
    assert outcome.blocked == 1
    assert stored["status"] == "blocked"
    assert stored["last_error_code"] == "canonical_write_count_mismatch"
    assert stored["synced_at_utc"] is None
    assert _read_model_status(engine, target_id) is None
    # The values were already written; the blocked state records ambiguity only.
    written = dict(zip(OPPORTUNITY_HEADERS, sheet.rows("Opportunities")[1], strict=True))
    assert written["application_status"] == "Applied"


def test_a_later_chunk_is_not_synced_after_an_earlier_ambiguous_write(
    engine: Engine,
) -> None:
    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )
    sheet = FakeSheet()
    transport = FakeTransport(sheet)
    sync = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW,
        batch_size=1,
    )
    original = transport.batch_update_values
    opportunity_writes = 0

    def mismatch_first_chunk(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        nonlocal opportunity_writes
        response = original(spreadsheet_id, data, value_input_option=value_input_option)
        if str(data[0]["range"]).startswith("Opportunities!"):
            opportunity_writes += 1
            if opportunity_writes == 1:
                return {"totalUpdatedRows": 0}
        return response

    transport.batch_update_values = mismatch_first_chunk  # type: ignore[method-assign]

    sync.dispatch_due()

    rows = sheet.rows("Opportunities")
    identities = [row[0] for row in rows[1:]]
    assert len(identities) == 1  # the second chunk was never written
    opportunity_claims = [
        entry for entry in outbox(engine) if entry["entity_type"] == "opportunity"
    ]
    assert len(opportunity_claims) == 2
    assert all(entry["status"] == "blocked" for entry in opportunity_claims)
    assert all(
        entry["last_error_code"] == "canonical_write_count_mismatch" for entry in opportunity_claims
    )


def test_an_earlier_verified_chunk_stays_synced_when_a_later_chunk_is_ambiguous(
    engine: Engine,
) -> None:
    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )
    sheet = FakeSheet()
    transport = FakeTransport(sheet)
    sync = SyncDispatcher(
        engine,
        transport=transport,
        spreadsheet_id="test_spreadsheet_id_1234567890",
        clock=lambda: NOW,
        batch_size=1,
    )
    original = transport.batch_update_values
    written_ids: list[str] = []

    def mismatch_second_chunk(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        response = original(spreadsheet_id, data, value_input_option=value_input_option)
        if str(data[0]["range"]).startswith("Opportunities!"):
            address = str(data[0]["range"]).split("!", 1)[1].split(":", 1)[0]
            row_number = int("".join(ch for ch in address if ch.isdigit()))
            written_ids.append(sheet.rows("Opportunities")[row_number - 1][0])
            if len(written_ids) == 2:
                return {"totalUpdatedRows": 0}
        return response

    transport.batch_update_values = mismatch_second_chunk  # type: ignore[method-assign]
    sync.dispatch_due()

    assert len(written_ids) == 2
    assert _claim(engine, written_ids[0])["status"] == "synced"
    blocked = _claim(engine, written_ids[1])
    assert blocked["status"] == "blocked"
    assert blocked["last_error_code"] == "canonical_write_count_mismatch"
    assert blocked["synced_at_utc"] is None


def test_an_ambiguous_batch_blocks_every_claim_in_the_chunk(engine: Engine) -> None:
    stage(
        engine,
        run_id="run_1",
        records=[
            record(external_id="1001"),
            record(external_id="1002", title="Senior Backend Engineer"),
        ],
    )
    sheet = FakeSheet()
    sync, transport = dispatcher(engine, sheet)
    original = transport.batch_update_values

    def wrong_count(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        response = original(spreadsheet_id, data, value_input_option=value_input_option)
        if any(str(item["range"]).startswith("Opportunities!") for item in data):
            return {"totalUpdatedRows": 0}
        return response

    transport.batch_update_values = wrong_count  # type: ignore[method-assign]
    sync.dispatch_due()

    opportunity_claims = [
        entry for entry in outbox(engine) if entry["entity_type"] == "opportunity"
    ]
    assert len(opportunity_claims) == 2
    assert all(entry["status"] == "blocked" for entry in opportunity_claims)
    assert all(
        entry["last_error_code"] == "canonical_write_count_mismatch" for entry in opportunity_claims
    )
    assert len(sheet.rows("Opportunities")) == 3  # both rows committed; the response was ambiguous


def test_a_planned_insert_that_never_lands_is_detected(engine: Engine) -> None:
    stage(engine, run_id="run_1", records=[record()])
    sheet = FakeSheet()
    sync, transport = dispatcher(engine, sheet)
    original = transport.batch_update_values

    def skip_opportunity_write(
        spreadsheet_id: str,
        data: list[dict[str, Any]],
        *,
        value_input_option: str,
    ) -> dict[str, Any]:
        if any(str(item["range"]).startswith("Opportunities!") for item in data):
            return {}  # pretend success without writing anything
        return original(spreadsheet_id, data, value_input_option=value_input_option)

    transport.batch_update_values = skip_opportunity_write  # type: ignore[method-assign]

    outcome = sync.dispatch_due()

    assert outcome.synced == 0
    assert len(sheet.rows("Opportunities")) == 1  # header only; the insert never landed
    opportunity_claim = next(
        entry for entry in outbox(engine) if entry["entity_type"] == "opportunity"
    )
    assert opportunity_claim["status"] == "blocked"
    assert opportunity_claim["last_error_code"] == "canonical_identity_map_changed"
