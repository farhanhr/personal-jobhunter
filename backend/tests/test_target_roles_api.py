"""API contract and security tests for target-role settings."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.main import create_app

JAKARTA = ZoneInfo("Asia/Jakarta")
LOCAL_ORIGIN = "http://127.0.0.1:5173"
NOW = datetime(2026, 11, 13, 10, 0, tzinfo=JAKARTA)


def _client(engine: Engine) -> TestClient:
    app = create_app(engine=engine, clock=lambda: NOW)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _put_roles(
    client: TestClient,
    roles: list[dict[str, object]],
    *,
    headers: dict[str, str] | None = None,
):
    return client.put(
        "/api/v1/settings/search",
        json={"target_roles": roles},
        headers={"Origin": LOCAL_ORIGIN, **(headers or {})},
    )


def test_get_empty_configuration(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/settings/search")

    assert response.status_code == 200
    assert response.json() == {"target_roles": [], "max_target_roles": 3}
    assert response.headers["cache-control"] == "no-store"


def test_put_creates_roles_and_get_returns_them(engine: Engine) -> None:
    with _client(engine) as client:
        created = _put_roles(
            client,
            [
                {
                    "canonical_name": "Backend Engineer",
                    "aliases": ["Backend Developer"],
                }
            ],
        )
        fetched = client.get("/api/v1/settings/search")

    assert created.status_code == 200
    assert created.headers["cache-control"] == "no-store"
    payload = created.json()
    assert payload["max_target_roles"] == 3
    assert len(payload["target_roles"]) == 1
    role = payload["target_roles"][0]
    assert role["role_id"].startswith("role_")
    assert role["canonical_name"] == "Backend Engineer"
    assert role["aliases"] == ["Backend Developer"]
    assert fetched.json() == payload


def test_put_preserves_existing_role_id(engine: Engine) -> None:
    with _client(engine) as client:
        created = _put_roles(
            client,
            [{"canonical_name": "Backend Engineer", "aliases": []}],
        )
        role_id = created.json()["target_roles"][0]["role_id"]
        updated = _put_roles(
            client,
            [
                {
                    "role_id": role_id,
                    "canonical_name": "Senior Backend Engineer",
                    "aliases": ["Backend Developer"],
                }
            ],
        )

    role = updated.json()["target_roles"][0]
    assert role["role_id"] == role_id
    assert role["canonical_name"] == "Senior Backend Engineer"


def test_put_accepts_three_roles(engine: Engine) -> None:
    with _client(engine) as client:
        response = _put_roles(
            client,
            [
                {"canonical_name": "Backend Engineer", "aliases": []},
                {"canonical_name": "Data Analyst", "aliases": []},
                {"canonical_name": "Product Manager", "aliases": []},
            ],
        )

    assert response.status_code == 200
    assert len(response.json()["target_roles"]) == 3


def test_put_rejects_more_than_three_roles_with_error_envelope(engine: Engine) -> None:
    with _client(engine) as client:
        response = _put_roles(
            client,
            [
                {"canonical_name": "Role One", "aliases": []},
                {"canonical_name": "Role Two", "aliases": []},
                {"canonical_name": "Role Three", "aliases": []},
                {"canonical_name": "Role Four", "aliases": []},
            ],
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "too_many_target_roles"
    assert error["details"] == {"max_target_roles": 3, "actual_target_roles": 4}
    assert error["request_id"].startswith("req_")
    assert "three" in error["message"].lower()


def test_invalid_update_does_not_change_saved_configuration(engine: Engine) -> None:
    with _client(engine) as client:
        original = _put_roles(
            client,
            [{"canonical_name": "Backend Engineer", "aliases": []}],
        ).json()
        rejected = _put_roles(
            client,
            [
                {"canonical_name": "Role One", "aliases": []},
                {"canonical_name": "Role Two", "aliases": []},
                {"canonical_name": "Role Three", "aliases": []},
                {"canonical_name": "Role Four", "aliases": []},
            ],
        )
        fetched = client.get("/api/v1/settings/search")

    assert rejected.status_code == 422
    assert fetched.json() == original


@pytest.mark.parametrize(
    ("roles", "code"),
    [
        (
            [
                {"canonical_name": "Backend Engineer", "aliases": []},
                {"canonical_name": "backend engineer", "aliases": []},
            ],
            "duplicate_target_role",
        ),
        (
            [{"canonical_name": "   ", "aliases": []}],
            "invalid_target_role_name",
        ),
        (
            [
                {
                    "canonical_name": "Backend Engineer",
                    "aliases": ["Backend Dev", "backend dev"],
                }
            ],
            "duplicate_role_alias",
        ),
        (
            [
                {
                    "role_id": "role_missing",
                    "canonical_name": "Data Analyst",
                    "aliases": ["Reporting Analyst"],
                }
            ],
            "unknown_target_role",
        ),
    ],
)
def test_put_rejects_invalid_role_configuration(
    engine: Engine,
    roles: list[dict[str, object]],
    code: str,
) -> None:
    with _client(engine) as client:
        response = _put_roles(client, roles)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_put_requires_origin(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.put(
            "/api/v1/settings/search",
            json={"target_roles": []},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


def test_put_rejects_untrusted_origin(engine: Engine) -> None:
    with _client(engine) as client:
        response = _put_roles(
            client,
            [],
            headers={"Origin": "https://evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


def test_put_rejects_untrusted_host(engine: Engine) -> None:
    with _client(engine) as client:
        response = _put_roles(
            client,
            [],
            headers={"Host": "evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"


def test_settings_rejects_untrusted_host(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get(
            "/api/v1/settings/search",
            headers={"Host": "evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"


def test_schema_error_uses_validation_error_envelope(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.put(
            "/api/v1/settings/search",
            json={"target_roles": "not-a-list"},
            headers={"Origin": LOCAL_ORIGIN},
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert isinstance(error["details"]["errors"], list)
    assert error["details"]["errors"][0]["field"].startswith("body.target_roles")


def test_syntactically_invalid_json_uses_validation_error_envelope(
    engine: Engine,
) -> None:
    with _client(engine) as client:
        response = client.put(
            "/api/v1/settings/search",
            content=b'{"target_roles": [',
            headers={
                "Origin": LOCAL_ORIGIN,
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert isinstance(error["details"]["errors"], list)


def test_repeating_identical_create_request_preserves_role_id(engine: Engine) -> None:
    payload = {
        "target_roles": [
            {"canonical_name": "Backend Engineer", "aliases": []},
        ]
    }

    with _client(engine) as client:
        first = client.put(
            "/api/v1/settings/search",
            json=payload,
            headers={"Origin": LOCAL_ORIGIN},
        )
        second = client.put(
            "/api/v1/settings/search",
            json=payload,
            headers={"Origin": LOCAL_ORIGIN},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["target_roles"][0]["role_id"] == first.json()["target_roles"][0]["role_id"]


def test_put_can_clear_configuration(engine: Engine) -> None:
    with _client(engine) as client:
        _put_roles(
            client,
            [{"canonical_name": "Backend Engineer", "aliases": []}],
        )
        cleared = _put_roles(client, [])

    assert cleared.status_code == 200
    assert cleared.json() == {"target_roles": [], "max_target_roles": 3}


def test_saved_roles_survive_application_restart(tmp_path) -> None:
    data_dir = tmp_path / "private-data"
    settings = Settings(data_dir=data_dir)

    first_app = create_app(app_settings=settings)
    with TestClient(first_app, base_url="http://127.0.0.1:8000") as client:
        saved = _put_roles(
            client,
            [
                {
                    "canonical_name": "Backend Engineer",
                    "aliases": ["Backend Developer"],
                }
            ],
        )
    assert saved.status_code == 200

    second_app = create_app(app_settings=settings)
    with TestClient(second_app, base_url="http://127.0.0.1:8000") as client:
        fetched = client.get("/api/v1/settings/search")

    assert fetched.json() == saved.json()


def test_search_policy_reflects_saved_target_roles(engine: Engine, seed_config) -> None:
    seed_config(roles=0)

    with _client(engine) as client:
        saved = _put_roles(
            client,
            [{"canonical_name": "Backend Engineer", "aliases": []}],
        )
        allowed = client.get("/api/v1/search-policy").json()
        _put_roles(client, [])
        blocked = client.get("/api/v1/search-policy").json()

    assert saved.status_code == 200
    assert allowed["can_start"] is True
    assert allowed["blocking_reasons"] == []
    assert blocked["can_start"] is False
    assert blocked["block_reason"] == "no_target_roles"
