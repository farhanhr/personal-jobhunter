"""API and policy tests for registering and approving company sources."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.db.tables import companies
from personal_job_finder_api.main import create_app

JAKARTA = ZoneInfo("Asia/Jakarta")
LOCAL_ORIGIN = "http://127.0.0.1:5173"
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=JAKARTA)


def _client(engine: Engine) -> TestClient:
    app = create_app(engine=engine, clock=lambda: NOW)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _create(
    client: TestClient,
    *,
    company_name: str = "Example Company",
    board_token: str = "example-board",
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/api/v1/companies",
        json={"company_name": company_name, "board_token": board_token},
        headers={"Origin": LOCAL_ORIGIN, **(headers or {})},
    )


def _patch(
    client: TestClient,
    company_id: str,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
):
    return client.patch(
        f"/api/v1/companies/{company_id}",
        json=payload,
        headers={"Origin": LOCAL_ORIGIN, **(headers or {})},
    )


def _company_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(companies)).scalar_one()


def test_company_list_starts_empty(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/companies")

    assert response.status_code == 200
    assert response.json() == {"companies": []}
    assert response.headers["cache-control"] == "no-store"


def test_registering_a_company_does_not_approve_it(engine: Engine) -> None:
    with _client(engine) as client:
        response = _create(client)

    assert response.status_code == 201
    payload = response.json()
    assert payload["company_id"].startswith("company_")
    assert payload["company_name"] == "Example Company"
    assert payload["ats_type"] == "greenhouse"
    assert payload["board_token"] == "example-board"
    assert payload["policy_status"] == "unreviewed"
    assert payload["enabled"] is True
    assert payload["can_fetch"] is False
    assert response.headers["cache-control"] == "no-store"
    assert _company_count(engine) == 1


def test_registered_company_appears_in_the_list(engine: Engine) -> None:
    with _client(engine) as client:
        _create(client)
        response = client.get("/api/v1/companies")

    assert response.status_code == 200
    listed = response.json()["companies"]
    assert len(listed) == 1
    assert listed[0]["board_token"] == "example-board"


@pytest.mark.parametrize(
    "board_token",
    [
        "../etc/passwd",
        "example/board",
        "example board",
        "example.board",
        "https://evil.test/board",
        "?x=1",
        "-leading",
        "trailing-",
        "",
    ],
)
def test_invalid_board_tokens_are_rejected_without_persisting(
    engine: Engine,
    board_token: str,
) -> None:
    with _client(engine) as client:
        response = _create(client, board_token=board_token)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_board_token"
    assert _company_count(engine) == 0


@pytest.mark.parametrize("company_name", ["", "   ", "bad\x00name", "x" * 201])
def test_invalid_company_names_are_rejected(
    engine: Engine,
    company_name: str,
) -> None:
    with _client(engine) as client:
        response = _create(client, company_name=company_name)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_company_name"
    assert _company_count(engine) == 0


def test_duplicate_board_token_is_rejected(engine: Engine) -> None:
    with _client(engine) as client:
        first = _create(client)
        second = _create(client, company_name="Another Company")

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_company_source"
    assert _company_count(engine) == 1


def test_duplicate_company_name_is_rejected(engine: Engine) -> None:
    with _client(engine) as client:
        _create(client, company_name="Example Company", board_token="board-one")
        response = _create(
            client,
            company_name="example company",
            board_token="board-two",
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_company_source"


def test_approving_a_company_allows_fetching(engine: Engine) -> None:
    with _client(engine) as client:
        created = _create(client).json()
        response = _patch(
            client,
            created["company_id"],
            {"policy_status": "permitted"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy_status"] == "permitted"
    assert payload["can_fetch"] is True


def test_blocking_a_company_prevents_fetching(engine: Engine) -> None:
    with _client(engine) as client:
        created = _create(client).json()
        _patch(client, created["company_id"], {"policy_status": "permitted"})
        response = _patch(
            client,
            created["company_id"],
            {"policy_status": "blocked"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy_status"] == "blocked"
    assert payload["can_fetch"] is False


def test_disabling_a_company_prevents_fetching(engine: Engine) -> None:
    with _client(engine) as client:
        created = _create(client).json()
        _patch(client, created["company_id"], {"policy_status": "permitted"})
        response = _patch(client, created["company_id"], {"enabled": False})

    assert response.status_code == 200
    assert response.json()["can_fetch"] is False


def test_unknown_policy_status_is_rejected(engine: Engine) -> None:
    with _client(engine) as client:
        created = _create(client).json()
        response = _patch(
            client,
            created["company_id"],
            {"policy_status": "approved-by-vibes"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_empty_update_is_rejected(engine: Engine) -> None:
    with _client(engine) as client:
        created = _create(client).json()
        response = _patch(client, created["company_id"], {})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_company_update"


def test_patching_an_unknown_company_returns_not_found(engine: Engine) -> None:
    with _client(engine) as client:
        response = _patch(client, "company_missing", {"policy_status": "permitted"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "company_source_not_found"


def test_company_endpoints_reject_untrusted_host(engine: Engine) -> None:
    with _client(engine) as client:
        response = _create(client, headers={"Host": "evil.test"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"
    assert _company_count(engine) == 0


def test_company_endpoints_reject_untrusted_origin(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.post(
            "/api/v1/companies",
            json={"company_name": "Example Company", "board_token": "example-board"},
            headers={"Origin": "https://evil.test"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"
    assert _company_count(engine) == 0


def test_policy_blocks_until_the_company_is_approved(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(roles=1, companies_count=0)

    with _client(engine) as client:
        created = _create(client).json()
        before = client.get("/api/v1/search-policy").json()

        _patch(client, created["company_id"], {"policy_status": "permitted"})
        after = client.get("/api/v1/search-policy").json()

    assert before["can_start"] is False
    assert before["blocking_reasons"] == ["no_usable_source"]
    assert after["can_start"] is True
    assert after["blocking_reasons"] == []


def test_blocked_company_keeps_search_unavailable(
    engine: Engine,
    seed_config,
) -> None:
    seed_config(roles=1, companies_count=0)

    with _client(engine) as client:
        created = _create(client).json()
        _patch(client, created["company_id"], {"policy_status": "blocked"})
        policy = client.get("/api/v1/search-policy").json()

    assert policy["can_start"] is False
    assert policy["blocking_reasons"] == ["no_usable_source"]
