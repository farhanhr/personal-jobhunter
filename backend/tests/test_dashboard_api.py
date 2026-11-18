"""Dashboard summary API tests.

The metrics read the local read model only. Every test uses a fixed clock and
deterministic SQLite rows, so no Google request and no wall-clock value is
involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import opportunities_read_model
from personal_job_finder_api.main import create_app

JAKARTA = ZoneInfo("Asia/Jakarta")

# A fixed Jakarta midday, so the calendar day is 2026-11-15.
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=JAKARTA)

APPLICATION_START = "2026-11-09"
APPLICATION_END = "2026-11-15"
INTERVIEW_START = "2026-11-15"
INTERVIEW_END = "2026-11-22"


def _client(engine: Engine, *, now: datetime = NOW) -> TestClient:
    app = create_app(engine=engine, clock=lambda: now)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _publish(
    engine: Engine,
    opportunity_id: str,
    *,
    date_applied: str | None = None,
    interview_date: str | None = None,
) -> None:
    """Insert one read-model row with only the tracker dates set."""

    with engine.begin() as connection:
        connection.execute(
            sa.insert(opportunities_read_model).values(
                opportunity_id=opportunity_id,
                date_applied=date_applied,
                interview_date=interview_date,
            )
        )


def _summary(engine: Engine, *, now: datetime = NOW) -> dict[str, object]:
    # The context manager runs the app lifespan, which publishes the engine
    # and the clock on ``app.state``.
    with _client(engine, now=now) as client:
        response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    return dict(response.json())


def test_applications_on_the_first_inclusive_boundary_are_counted(engine: Engine) -> None:
    _publish(engine, "opp_a", date_applied="09/11/2026")

    body = _summary(engine)

    assert body["applications_last_7_days"] == 1
    assert body["applications_window"] == {
        "start": APPLICATION_START,
        "end": APPLICATION_END,
    }


def test_applications_on_the_last_inclusive_boundary_are_counted(engine: Engine) -> None:
    _publish(engine, "opp_a", date_applied="15/11/2026")

    assert _summary(engine)["applications_last_7_days"] == 1


def test_applications_just_outside_the_window_are_not_counted(engine: Engine) -> None:
    _publish(engine, "opp_before", date_applied="08/11/2026")
    _publish(engine, "opp_after", date_applied="16/11/2026")

    assert _summary(engine)["applications_last_7_days"] == 0


def test_interviews_on_today_are_counted(engine: Engine) -> None:
    _publish(engine, "opp_a", interview_date="15/11/2026")

    assert _summary(engine)["upcoming_interviews"] == 1


def test_interviews_on_the_seventh_future_day_are_counted(engine: Engine) -> None:
    _publish(engine, "opp_a", interview_date="22/11/2026")

    body = _summary(engine)

    assert body["upcoming_interviews"] == 1
    assert body["interviews_window"] == {"start": INTERVIEW_START, "end": INTERVIEW_END}


def test_interviews_outside_the_upcoming_window_are_not_counted(engine: Engine) -> None:
    _publish(engine, "opp_before", interview_date="14/11/2026")
    _publish(engine, "opp_after", interview_date="23/11/2026")

    assert _summary(engine)["upcoming_interviews"] == 0


def test_the_window_uses_the_jakarta_calendar_date(engine: Engine) -> None:
    # 2026-11-14 20:00 UTC is already 2026-11-15 03:00 in Jakarta.
    now = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)
    _publish(engine, "opp_a", date_applied="15/11/2026")

    body = _summary(engine, now=now)

    assert body["as_of_date_jkt"] == "2026-11-15"
    assert body["applications_window"]["start"] == APPLICATION_START
    assert body["applications_last_7_days"] == 1


def test_midnight_rollover_moves_the_window(engine: Engine) -> None:
    _publish(engine, "opp_a", date_applied="15/11/2026")

    before = _summary(engine, now=datetime(2026, 11, 14, 23, 59, 59, tzinfo=JAKARTA))
    after = _summary(engine, now=datetime(2026, 11, 15, 0, 0, 0, tzinfo=JAKARTA))

    assert before["as_of_date_jkt"] == "2026-11-14"
    assert before["applications_window"] == {"start": "2026-11-08", "end": "2026-11-14"}
    assert before["applications_last_7_days"] == 0

    assert after["as_of_date_jkt"] == "2026-11-15"
    assert after["applications_window"] == {
        "start": APPLICATION_START,
        "end": APPLICATION_END,
    }
    assert after["applications_last_7_days"] == 1


def test_blank_dates_are_neither_counted_nor_reported(engine: Engine) -> None:
    _publish(engine, "opp_empty", date_applied="", interview_date="")
    _publish(engine, "opp_spaces", date_applied="   ", interview_date="  ")
    _publish(engine, "opp_none")

    body = _summary(engine)

    assert body["applications_last_7_days"] == 0
    assert body["upcoming_interviews"] == 0
    assert body["data_quality"]["invalid_date_value_count"] == 0
    assert body["data_quality"]["invalid_date_values"] == []


def test_invalid_date_values_are_excluded_and_reported(engine: Engine) -> None:
    _publish(engine, "opp_a", date_applied="31/02/2026", interview_date="besok")

    body = _summary(engine)

    assert body["applications_last_7_days"] == 0
    assert body["upcoming_interviews"] == 0
    quality = body["data_quality"]
    assert quality["invalid_date_value_count"] == 2
    entries = {
        (entry["field"], entry["raw_value"], entry["reason"])
        for entry in quality["invalid_date_values"]
    }
    assert entries == {
        ("date_applied", "31/02/2026", "invalid_calendar_date"),
        ("interview_date", "besok", "unrecognized_format"),
    }


def test_raw_tracker_date_values_are_not_rewritten(engine: Engine) -> None:
    raw = "15/11/2026 (pagi)"
    _publish(engine, "opp_a", date_applied=raw)

    body = _summary(engine)

    # The value is interpreted as 15 November 2026 without being changed.
    assert body["applications_last_7_days"] == 1
    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(opportunities_read_model.c.date_applied).where(
                opportunities_read_model.c.opportunity_id == "opp_a"
            )
        ).scalar_one()
    assert stored == raw


def test_the_response_reports_stable_window_boundaries(engine: Engine) -> None:
    body = _summary(engine)

    assert body["as_of_date_jkt"] == "2026-11-15"
    assert body["applications_window"] == {
        "start": APPLICATION_START,
        "end": APPLICATION_END,
    }
    assert body["interviews_window"] == {
        "start": INTERVIEW_START,
        "end": INTERVIEW_END,
    }
    assert body["data_quality"]["invalid_date_values_truncated"] is False


def test_an_empty_read_model_reports_zero_metrics(engine: Engine) -> None:
    body = _summary(engine)

    assert body["applications_last_7_days"] == 0
    assert body["upcoming_interviews"] == 0
    assert body["data_quality"]["invalid_date_value_count"] == 0
    assert body["data_quality"]["invalid_date_values"] == []


def test_the_summary_never_calls_google_sheets(engine: Engine) -> None:
    class _ForbiddenTransport:
        def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, object]:
            raise AssertionError("the dashboard must not call Google Sheets")

        def batch_get_values(
            self,
            spreadsheet_id: str,
            ranges: list[str],
        ) -> dict[str, object]:
            raise AssertionError("the dashboard must not call Google Sheets")

        def batch_update_values(
            self,
            spreadsheet_id: str,
            data: list[dict[str, object]],
            *,
            value_input_option: str,
        ) -> dict[str, object]:
            raise AssertionError("the dashboard must not call Google Sheets")

        def batch_update_spreadsheet(
            self,
            spreadsheet_id: str,
            requests: list[dict[str, object]],
        ) -> dict[str, object]:
            raise AssertionError("the dashboard must not call Google Sheets")

    app = create_app(
        engine=engine,
        clock=lambda: NOW,
        sync_transport=_ForbiddenTransport(),
    )
    _publish(engine, "opp_a", date_applied="15/11/2026")

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["applications_last_7_days"] == 1


def test_each_opportunity_counts_once_per_metric(engine: Engine) -> None:
    _publish(engine, "opp_a", date_applied="12/11/2026", interview_date="18/11/2026")
    _publish(engine, "opp_b", date_applied="12/11/2026", interview_date="18/11/2026")

    body = _summary(engine)

    assert body["applications_last_7_days"] == 2
    assert body["upcoming_interviews"] == 2


def test_the_summary_response_is_not_cached(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/dashboard/summary")

    assert response.headers["cache-control"] == "no-store"
