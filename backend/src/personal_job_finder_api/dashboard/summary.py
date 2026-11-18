"""Dashboard summary metrics computed from the local read model.

The dashboard is a local view: it reads the SQLite read model only. It never
contacts Google Sheets and never introduces another synchronization path
(ADR-015, ADR-024, P0/12 §"dashboard").

Source of truth for totals and groupings is the canonical read model
(``opportunities_read_model`` and ``job_sources_read_model``), not the staging
projection, so every number describes what the user can actually see in the
canonical sheet (ADR-024).

Window definitions are fixed by ADR-021 and P0/12:

- Applications in the last 7 calendar days: ``[today_jkt - 6 days, today_jkt]``
- Upcoming interviews: ``[today_jkt, today_jkt + 7 days]``
- New opportunities: first seen in ``[today_jkt - 6 days, today_jkt]``

All boundaries are inclusive and every date is a Jakarta calendar date, so the
backend supplies ``as_of_date_jkt`` and the exact boundaries and the browser
never computes "today" on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Engine, func, select

from personal_job_finder_api.core.time import parse_utc, utc_now
from personal_job_finder_api.db.tables import (
    job_sources_read_model,
    opportunities_read_model,
)
from personal_job_finder_api.domain.search_policy import JAKARTA_TIMEZONE
from personal_job_finder_api.domain.tracker_dates import parse_tracker_date
from personal_job_finder_api.repositories.read_model import ReadModelRepository
from personal_job_finder_api.repositories.target_roles import TargetRoleRepository

#: Number of calendar days in the applications window (inclusive on both ends).
APPLICATION_WINDOW_DAYS = 7

#: Number of calendar days after today in the interviews window (inclusive).
INTERVIEW_WINDOW_DAYS = 7

#: Number of calendar days in the new-opportunity window (inclusive on both ends).
NEW_OPPORTUNITY_WINDOW_DAYS = 7

DATE_APPLIED_FIELD = "date_applied"
INTERVIEW_DATE_FIELD = "interview_date"

#: Label for opportunities whose target role is missing or no longer configured.
UNMATCHED_ROLE_LABEL = "Unmatched"

#: Label for source and location values that the canonical row does not state.
UNKNOWN_LABEL = "Unknown"

#: Upper bound of reported invalid values, so one bad column cannot blow up the
#: response. The total count and the truncation flag stay in the response, so a
#: truncated list is never mistaken for a complete one.
MAX_INVALID_DATE_VALUES = 50


@dataclass(frozen=True, slots=True)
class DateWindow:
    """An inclusive calendar-date window."""

    start: date
    end: date

    def contains(self, value: date) -> bool:
        """True when the date falls inside the inclusive window."""

        return self.start <= value <= self.end


@dataclass(frozen=True, slots=True)
class InvalidTrackerDate:
    """One non-empty tracker date value that could not be read."""

    opportunity_id: str
    field: str
    raw_value: str
    reason: str


@dataclass(frozen=True, slots=True)
class RoleGroup:
    """Opportunities grouped by target role."""

    role_id: str | None
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class SourceGroup:
    """Opportunities grouped by source type.

    One opportunity with several source types appears in several groups, so the
    group counts may add up to more than the overall opportunity total.
    """

    source_type: str
    count: int


@dataclass(frozen=True, slots=True)
class LocationGroup:
    """Opportunities grouped by the ``(province, city)`` pair."""

    province: str
    city: str
    count: int


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    """The dashboard metrics and their exact window boundaries."""

    as_of_date_jkt: date
    applications_window: DateWindow
    applications_last_7_days: int
    interviews_window: DateWindow
    upcoming_interviews: int
    total_opportunities: int
    excluded_opportunities: int
    new_opportunities: int
    new_opportunities_window: DateWindow
    opportunities_by_target_role: tuple[RoleGroup, ...]
    opportunities_by_source: tuple[SourceGroup, ...]
    opportunities_by_location: tuple[LocationGroup, ...]
    latest_read_model_refresh_at_utc: str | None
    invalid_values: tuple[InvalidTrackerDate, ...]
    invalid_value_count: int
    invalid_values_truncated: bool


def _not_excluded() -> sa.ColumnElement[bool]:
    """Match every opportunity row that is not an excluded record.

    A missing ``location_status`` is not an exclusion, so such a record still
    belongs to the visible population.
    """

    return sa.or_(
        opportunities_read_model.c.location_status.is_(None),
        opportunities_read_model.c.location_status != "excluded",
    )


class DashboardSummaryService:
    """Count recent applications and upcoming interviews from the read model."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._read_model = ReadModelRepository(engine)
        self._roles = TargetRoleRepository()

    def summary(self) -> DashboardSummary:
        """Return every dashboard metric for the current Jakarta calendar day."""

        today = self._today()
        applications_window = DateWindow(
            start=today - timedelta(days=APPLICATION_WINDOW_DAYS - 1),
            end=today,
        )
        interviews_window = DateWindow(
            start=today,
            end=today + timedelta(days=INTERVIEW_WINDOW_DAYS),
        )
        new_window = DateWindow(
            start=today - timedelta(days=NEW_OPPORTUNITY_WINDOW_DAYS - 1),
            end=today,
        )

        with self._engine.connect() as connection:
            applied_ids, interview_ids, invalid = self._tracker_metrics(
                connection,
                applications_window=applications_window,
                interviews_window=interviews_window,
            )
            total = self._count(connection, _not_excluded())
            excluded = self._count(
                connection,
                opportunities_read_model.c.location_status == "excluded",
            )
            new_opportunities = self._count_new(connection, new_window)
            role_groups = self._group_by_role(connection)
            source_groups = self._group_by_source(connection)
            location_groups = self._group_by_location(connection)

        invalid.sort(key=lambda entry: (entry.opportunity_id, entry.field))
        return DashboardSummary(
            as_of_date_jkt=today,
            applications_window=applications_window,
            applications_last_7_days=len(applied_ids),
            interviews_window=interviews_window,
            upcoming_interviews=len(interview_ids),
            total_opportunities=total,
            excluded_opportunities=excluded,
            new_opportunities=new_opportunities,
            new_opportunities_window=new_window,
            opportunities_by_target_role=role_groups,
            opportunities_by_source=source_groups,
            opportunities_by_location=location_groups,
            latest_read_model_refresh_at_utc=self._latest_refresh(),
            invalid_values=tuple(invalid[:MAX_INVALID_DATE_VALUES]),
            invalid_value_count=len(invalid),
            invalid_values_truncated=len(invalid) > MAX_INVALID_DATE_VALUES,
        )

    # ----------------------------------------------------------------- helpers

    def _today(self) -> date:
        """Return the current calendar date in Jakarta."""

        return self._clock().astimezone(ZoneInfo(JAKARTA_TIMEZONE)).date()

    def _latest_refresh(self) -> str | None:
        """Return the last successful read-model refresh timestamp.

        This is the read-model refresh state, not the last successful Sheets
        write: the two answer different questions.
        """

        state = self._read_model.get_sync_state()
        if state is None:
            return None
        value = state.get("last_full_refresh_at_utc")
        return str(value) if value else None

    def _tracker_metrics(
        self,
        connection: sa.Connection,
        *,
        applications_window: DateWindow,
        interviews_window: DateWindow,
    ) -> tuple[set[str], set[str], list[InvalidTrackerDate]]:
        """Count applications and interviews from the two tracker columns."""

        applied_ids: set[str] = set()
        interview_ids: set[str] = set()
        invalid: list[InvalidTrackerDate] = []

        rows = connection.execute(
            select(
                opportunities_read_model.c.opportunity_id,
                opportunities_read_model.c.date_applied,
                opportunities_read_model.c.interview_date,
            )
        ).all()

        for opportunity_id, date_applied, interview_date in rows:
            applied = parse_tracker_date(date_applied)
            if applied.is_invalid:
                invalid.append(
                    InvalidTrackerDate(
                        opportunity_id=str(opportunity_id),
                        field=DATE_APPLIED_FIELD,
                        raw_value=applied.raw,
                        reason=str(applied.reason),
                    )
                )
            elif applied.value is not None and applications_window.contains(applied.value):
                applied_ids.add(str(opportunity_id))

            interview = parse_tracker_date(interview_date)
            if interview.is_invalid:
                invalid.append(
                    InvalidTrackerDate(
                        opportunity_id=str(opportunity_id),
                        field=INTERVIEW_DATE_FIELD,
                        raw_value=interview.raw,
                        reason=str(interview.reason),
                    )
                )
            elif interview.value is not None and interviews_window.contains(interview.value):
                interview_ids.add(str(opportunity_id))

        return applied_ids, interview_ids, invalid

    def _count(
        self,
        connection: sa.Connection,
        condition: sa.ColumnElement[bool],
    ) -> int:
        """Count distinct opportunity rows that satisfy ``condition``."""

        return int(
            connection.execute(
                select(func.count(sa.distinct(opportunities_read_model.c.opportunity_id))).where(
                    condition
                )
            ).scalar_one()
        )

    def _count_new(
        self,
        connection: sa.Connection,
        window: DateWindow,
    ) -> int:
        """Count opportunities first seen inside the inclusive window.

        ``first_seen_at_utc`` is canonical text, so every value is parsed
        before it is compared. A value that cannot be parsed is not counted:
        it cannot be proven to be inside the window.
        """

        start = self._window_start_utc(window)
        end = self._window_end_utc(window)

        rows = connection.execute(
            select(
                opportunities_read_model.c.opportunity_id,
                opportunities_read_model.c.first_seen_at_utc,
            ).where(_not_excluded())
        ).all()

        count = 0
        for _, raw in rows:
            moment = _parse_timestamp(raw)
            if moment is None:
                continue
            if start <= moment < end:
                count += 1
        return count

    def _window_start_utc(self, window: DateWindow) -> datetime:
        """Return the inclusive start instant of a Jakarta calendar window."""

        local = datetime.combine(
            window.start,
            time.min,
            tzinfo=ZoneInfo(JAKARTA_TIMEZONE),
        )
        return local.astimezone(UTC)

    def _window_end_utc(self, window: DateWindow) -> datetime:
        """Return the exclusive end instant of a Jakarta calendar window."""

        local = datetime.combine(
            window.end + timedelta(days=1),
            time.min,
            tzinfo=ZoneInfo(JAKARTA_TIMEZONE),
        )
        return local.astimezone(UTC)

    def _group_by_role(self, connection: sa.Connection) -> tuple[RoleGroup, ...]:
        """Group opportunities by target role.

        Every opportunity lands in exactly one group. A missing target role, or
        a role that is no longer configured, lands in the explicit ``Unmatched``
        group. Roles that are configured but disabled are still resolved, so a
        historical opportunity keeps its original label.
        """

        rows = connection.execute(
            select(
                opportunities_read_model.c.target_role_id,
                func.count(sa.distinct(opportunities_read_model.c.opportunity_id)),
            )
            .where(_not_excluded())
            .group_by(opportunities_read_model.c.target_role_id)
        ).all()

        roles = {
            role.role_id: role.canonical_name
            for role in self._roles.list_all(connection, include_disabled=True)
        }

        grouped: dict[str, int] = {}
        unmatched = 0
        for role_id, count in rows:
            key = str(role_id) if role_id is not None else None
            if key is None or key not in roles:
                unmatched += int(count)
                continue
            grouped[key] = grouped.get(key, 0) + int(count)

        groups = [
            RoleGroup(role_id=role_id, label=roles[role_id], count=count)
            for role_id, count in grouped.items()
        ]
        if unmatched:
            groups.append(RoleGroup(role_id=None, label=UNMATCHED_ROLE_LABEL, count=unmatched))
        return tuple(sorted(groups, key=lambda group: (-group.count, group.label)))

    def _group_by_source(self, connection: sa.Connection) -> tuple[SourceGroup, ...]:
        """Group opportunities by source type.

        An opportunity with several source types appears in several groups, so
        the counts may add up to more than the overall total. An opportunity
        without any source association lands in the explicit ``Unknown`` group.
        """

        rows = connection.execute(
            select(
                job_sources_read_model.c.source_type,
                func.count(sa.distinct(job_sources_read_model.c.opportunity_id)),
            )
            .join(
                opportunities_read_model,
                opportunities_read_model.c.opportunity_id
                == job_sources_read_model.c.opportunity_id,
            )
            .where(_not_excluded())
            .group_by(job_sources_read_model.c.source_type)
        ).all()

        grouped: dict[str, int] = {}
        for source_type, count in rows:
            key = str(source_type).strip() if source_type else ""
            label = key or UNKNOWN_LABEL
            grouped[label] = grouped.get(label, 0) + int(count)

        without_source = connection.execute(
            select(func.count(sa.distinct(opportunities_read_model.c.opportunity_id)))
            .where(_not_excluded())
            .where(
                ~sa.select(job_sources_read_model.c.opportunity_id)
                .where(
                    job_sources_read_model.c.opportunity_id
                    == opportunities_read_model.c.opportunity_id
                )
                .exists()
            )
        ).scalar_one()
        if int(without_source):
            grouped[UNKNOWN_LABEL] = grouped.get(UNKNOWN_LABEL, 0) + int(without_source)

        return tuple(
            sorted(
                (
                    SourceGroup(source_type=source_type, count=count)
                    for source_type, count in grouped.items()
                ),
                key=lambda group: (-group.count, group.source_type),
            )
        )

    def _group_by_location(self, connection: sa.Connection) -> tuple[LocationGroup, ...]:
        """Group opportunities by the ``(province, city)`` pair.

        A missing province or city becomes an explicit ``Unknown`` value, and a
        partly known location is kept instead of being dropped.
        """

        rows = connection.execute(
            select(
                opportunities_read_model.c.province,
                opportunities_read_model.c.city,
                func.count(sa.distinct(opportunities_read_model.c.opportunity_id)),
            )
            .where(_not_excluded())
            .group_by(
                opportunities_read_model.c.province,
                opportunities_read_model.c.city,
            )
        ).all()

        groups = [
            LocationGroup(
                province=_known_value(province),
                city=_known_value(city),
                count=int(count),
            )
            for province, city, count in rows
        ]
        return tuple(
            sorted(
                groups,
                key=lambda group: (-group.count, group.province, group.city),
            )
        )


def _known_value(value: object) -> str:
    """Normalize a canonical text value to a label, keeping partial knowledge."""

    text = str(value).strip() if value else ""
    return text or UNKNOWN_LABEL


def _parse_timestamp(value: object) -> datetime | None:
    """Parse a canonical UTC timestamp, returning ``None`` when unreadable."""

    if not value:
        return None
    try:
        return parse_utc(str(value))
    except ValueError:
        return None
