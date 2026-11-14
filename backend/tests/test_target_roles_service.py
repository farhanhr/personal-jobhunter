"""Integration tests for persisted target-role management."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.db.engine import create_engine_for_url
from personal_job_finder_api.db.tables import target_roles
from personal_job_finder_api.domain.search_policy import BlockingReason
from personal_job_finder_api.domain.target_roles import (
    TargetRoleDraft,
    TargetRoleValidationError,
)
from personal_job_finder_api.search.service import SearchPolicyService
from personal_job_finder_api.settings.service import TargetRoleService

FIXED_NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


class _AvailableConnector:
    source_id = "company_1"


def _available_connectors(_snapshot):
    return [_AvailableConnector()]


def _service(engine: Engine, now: datetime = FIXED_NOW) -> TargetRoleService:
    return TargetRoleService(engine, clock=lambda: now)


def _stored_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(target_roles).order_by(target_roles.c.canonical_name)
            ).mappings()
        ]


def test_empty_configuration_returns_no_roles(engine: Engine) -> None:
    assert _service(engine).list_roles() == []


def test_role_is_persisted_with_generated_id_and_explicit_aliases(engine: Engine) -> None:
    service = _service(engine)

    saved = service.replace_roles(
        [TargetRoleDraft("Backend Engineer", aliases=("Backend Developer",))]
    )

    assert len(saved) == 1
    assert saved[0].role_id.startswith("role_")
    assert saved[0].canonical_name == "Backend Engineer"
    assert saved[0].aliases == ("Backend Developer",)
    assert service.list_roles() == saved

    rows = _stored_rows(engine)
    assert rows[0]["aliases_json"] == '["Backend Developer"]'
    assert rows[0]["enabled"] == 1


def test_role_without_aliases_does_not_gain_any(engine: Engine) -> None:
    saved = _service(engine).replace_roles([TargetRoleDraft("Data Analyst")])

    assert saved[0].aliases == ()
    assert _stored_rows(engine)[0]["aliases_json"] == "[]"


def test_three_roles_are_persisted(engine: Engine) -> None:
    saved = _service(engine).replace_roles(
        [
            TargetRoleDraft("Backend Engineer"),
            TargetRoleDraft("Data Analyst"),
            TargetRoleDraft("Product Manager"),
        ]
    )

    assert {role.canonical_name for role in saved} == {
        "Backend Engineer",
        "Data Analyst",
        "Product Manager",
    }
    assert len(_stored_rows(engine)) == 3


def test_repeating_a_create_without_role_id_preserves_identity(engine: Engine) -> None:
    service = _service(engine)

    first = service.replace_roles([TargetRoleDraft("Backend Engineer")])
    first_row = _stored_rows(engine)[0]
    second = service.replace_roles([TargetRoleDraft("Backend Engineer")])

    assert second[0].role_id == first[0].role_id
    assert _stored_rows(engine)[0]["created_at_utc"] == first_row["created_at_utc"]


def test_name_match_does_not_reuse_an_id_claimed_by_another_role(engine: Engine) -> None:
    service = _service(engine)
    original = service.replace_roles(
        [
            TargetRoleDraft("Backend Engineer"),
            TargetRoleDraft("Data Analyst"),
        ]
    )
    ids = {role.canonical_name: role.role_id for role in original}

    saved = service.replace_roles(
        [
            TargetRoleDraft(
                "Data Analyst",
                role_id=ids["Backend Engineer"],
            ),
            TargetRoleDraft("Backend Engineer"),
        ]
    )

    roles = {role.canonical_name: role for role in saved}
    assert roles["Data Analyst"].role_id == ids["Backend Engineer"]
    assert roles["Backend Engineer"].role_id != ids["Backend Engineer"]
    assert roles["Backend Engineer"].role_id != ids["Data Analyst"]


def test_full_replacement_removes_omitted_roles_and_keeps_retained_id(
    engine: Engine,
) -> None:
    service = _service(engine)
    original = service.replace_roles(
        [
            TargetRoleDraft("Backend Engineer"),
            TargetRoleDraft("Data Analyst"),
            TargetRoleDraft("Product Manager"),
        ]
    )
    retained = next(role for role in original if role.canonical_name == "Data Analyst")

    saved = service.replace_roles([TargetRoleDraft("Data Analyst", role_id=retained.role_id)])

    assert len(saved) == 1
    assert saved[0].role_id == retained.role_id
    assert saved[0].canonical_name == "Data Analyst"
    stored = _stored_rows(engine)
    assert len(stored) == 1
    assert stored[0]["role_id"] == retained.role_id


def test_full_replacement_preserves_disabled_roles_outside_management_scope(
    engine: Engine,
) -> None:
    now = format_utc(FIXED_NOW)
    with engine.begin() as connection:
        connection.execute(
            target_roles.insert().values(
                role_id="role_disabled",
                canonical_name="Legacy Data Analyst",
                aliases_json='["Legacy Analyst"]',
                excluded_terms_json="[]",
                seniority_json=None,
                enabled=False,
                created_at_utc=now,
                updated_at_utc=now,
            )
        )

    _service(engine).replace_roles([TargetRoleDraft("Backend Engineer")])

    rows = {row["canonical_name"]: row for row in _stored_rows(engine)}
    assert rows["Legacy Data Analyst"]["role_id"] == "role_disabled"
    assert rows["Legacy Data Analyst"]["enabled"] == 0
    assert rows["Backend Engineer"]["enabled"] == 1
    assert len(rows) == 2


def test_more_than_three_roles_is_rejected_without_changing_saved_state(
    engine: Engine,
) -> None:
    service = _service(engine)
    original = service.replace_roles([TargetRoleDraft("Backend Engineer")])

    with pytest.raises(TargetRoleValidationError) as raised:
        service.replace_roles(
            [
                TargetRoleDraft("Backend Engineer", role_id=original[0].role_id),
                TargetRoleDraft("Data Analyst"),
                TargetRoleDraft("Product Manager"),
                TargetRoleDraft("Site Reliability Engineer"),
            ]
        )

    assert raised.value.code == "too_many_target_roles"
    assert service.list_roles() == original


def test_existing_role_id_is_preserved_and_created_at_is_unchanged(
    engine: Engine,
    clock_factory,
) -> None:
    clock = clock_factory(datetime(2026, 11, 13, 10, 0, tzinfo=UTC))
    service = TargetRoleService(engine, clock=clock)
    original = service.replace_roles([TargetRoleDraft("Backend Engineer")])
    original_row = _stored_rows(engine)[0]

    clock.now = datetime(2026, 11, 13, 11, 0, tzinfo=UTC)
    updated = service.replace_roles(
        [
            TargetRoleDraft(
                "Senior Backend Engineer",
                aliases=("Backend Developer",),
                role_id=original[0].role_id,
            )
        ]
    )

    assert updated[0].role_id == original[0].role_id
    assert updated[0].canonical_name == "Senior Backend Engineer"
    assert updated[0].aliases == ("Backend Developer",)

    updated_row = _stored_rows(engine)[0]
    assert updated_row["created_at_utc"] == original_row["created_at_utc"]
    assert updated_row["updated_at_utc"] == format_utc(datetime(2026, 11, 13, 11, 0, tzinfo=UTC))


def test_unmanaged_role_fields_are_preserved_on_update(engine: Engine) -> None:
    now = format_utc(FIXED_NOW)
    with engine.begin() as connection:
        connection.execute(
            target_roles.insert().values(
                role_id="role_existing",
                canonical_name="Backend Engineer",
                aliases_json='["Backend Dev"]',
                excluded_terms_json='["intern"]',
                seniority_json='["senior"]',
                enabled=True,
                created_at_utc=now,
                updated_at_utc=now,
            )
        )

    _service(engine).replace_roles(
        [
            TargetRoleDraft(
                "Senior Backend Engineer",
                aliases=("Backend Developer",),
                role_id="role_existing",
            )
        ]
    )

    row = _stored_rows(engine)[0]
    assert row["role_id"] == "role_existing"
    assert row["excluded_terms_json"] == '["intern"]'
    assert row["seniority_json"] == '["senior"]'


def test_unknown_role_id_is_rejected_without_changing_saved_state(engine: Engine) -> None:
    service = _service(engine)
    original = service.replace_roles([TargetRoleDraft("Backend Engineer")])

    with pytest.raises(TargetRoleValidationError) as raised:
        service.replace_roles(
            [
                TargetRoleDraft(
                    "Data Analyst",
                    aliases=("Reporting Analyst",),
                    role_id="role_missing",
                )
            ]
        )

    assert raised.value.code == "unknown_target_role"
    assert service.list_roles() == original


@pytest.mark.parametrize(
    "drafts",
    [
        [TargetRoleDraft(""), TargetRoleDraft("Data Analyst")],
        [TargetRoleDraft("Backend Engineer"), TargetRoleDraft("backend engineer")],
        [
            TargetRoleDraft("Backend Engineer", aliases=("Backend Dev",)),
            TargetRoleDraft("Data Analyst", aliases=("backend dev",)),
        ],
    ],
)
def test_invalid_payload_leaves_previous_configuration_unchanged(
    engine: Engine,
    drafts: list[TargetRoleDraft],
) -> None:
    service = _service(engine)
    original = service.replace_roles([TargetRoleDraft("Product Manager")])

    with pytest.raises(TargetRoleValidationError):
        service.replace_roles(drafts)

    assert service.list_roles() == original


def test_empty_replacement_clears_configuration(engine: Engine) -> None:
    service = _service(engine)
    service.replace_roles([TargetRoleDraft("Backend Engineer")])

    assert service.replace_roles([]) == []
    assert service.list_roles() == []
    assert _stored_rows(engine) == []


def test_database_failure_rolls_back_the_entire_replacement(engine: Engine) -> None:
    service = _service(engine)
    original = service.replace_roles([TargetRoleDraft("Backend Engineer")])

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_target_role_insert "
            "BEFORE INSERT ON target_roles "
            "WHEN NEW.canonical_name = 'Forced Failure' "
            "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
        )

    with pytest.raises(sa.exc.DBAPIError):
        service.replace_roles(
            [
                TargetRoleDraft(
                    "Backend Engineer",
                    role_id=original[0].role_id,
                ),
                TargetRoleDraft("Forced Failure"),
            ]
        )

    assert service.list_roles() == original


def test_policy_uses_persisted_role_configuration(
    engine: Engine,
    seed_config,
    clock_factory,
) -> None:
    seed_config(roles=0)
    now = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    roles = _service(engine, now)

    roles.replace_roles([TargetRoleDraft("Backend Engineer")])
    allowed = SearchPolicyService(
        engine,
        connector_factory=_available_connectors,
        clock=lambda: now,
    ).evaluate()

    assert allowed.allowed is True
    assert allowed.blocking_reasons == ()

    roles.replace_roles([])
    blocked = SearchPolicyService(
        engine,
        connector_factory=_available_connectors,
        clock=lambda: now,
    ).evaluate()

    assert blocked.allowed is False
    assert blocked.blocking_reasons == (BlockingReason.NO_TARGET_ROLES,)


def test_saved_roles_survive_a_new_engine(engine: Engine, database_url: str) -> None:
    _service(engine).replace_roles(
        [TargetRoleDraft("Backend Engineer", aliases=("Backend Developer",))]
    )

    second_engine = create_engine_for_url(database_url)
    try:
        roles = _service(second_engine).list_roles()
    finally:
        second_engine.dispose()

    assert len(roles) == 1
    assert roles[0].canonical_name == "Backend Engineer"
    assert roles[0].aliases == ("Backend Developer",)


def test_concurrent_replacements_leave_one_complete_configuration(
    engine: Engine,
    database_url: str,
) -> None:
    second_engine = create_engine_for_url(database_url)
    services = [_service(engine), _service(second_engine)]
    barrier = threading.Barrier(2)
    replacements = [
        [TargetRoleDraft("Backend Engineer")],
        [TargetRoleDraft("Data Analyst")],
    ]

    def replace(item):
        service, drafts = item
        barrier.wait(timeout=5)
        return service.replace_roles(drafts)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(replace, zip(services, replacements, strict=True)))
    finally:
        second_engine.dispose()

    final_names = {role.canonical_name for role in _service(engine).list_roles()}
    assert final_names in ({"Backend Engineer"}, {"Data Analyst"})
    assert len(_stored_rows(engine)) == 1
