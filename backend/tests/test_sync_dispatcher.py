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
            tab = requested.split("!", 1)[0]
            rows = self.sheet.tabs.get(tab, [])
            value_ranges.append({"values": [list(row) for row in rows]} if rows else {})
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
