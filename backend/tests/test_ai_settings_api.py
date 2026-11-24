"""API tests for AI provider settings and the connection probe (ADR-030).

Every provider call is faked: no test opens a socket or spends a token. The
fakes are injected through the client seam in ``ai.connection``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.ai import connection
from personal_job_finder_api.ai.settings_store import (
    is_masked_api_key,
    mask_api_key,
    resolve_api_key_update,
)
from personal_job_finder_api.db.migrate import downgrade_database, run_migrations
from personal_job_finder_api.db.tables import ai_settings, companies, target_roles
from personal_job_finder_api.main import create_app

NOW = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)
LOCAL_ORIGIN = "http://127.0.0.1:5173"
API_KEY = "gsk_test_0123456789abcdef1234"
MASKED_KEY = "gsk...1234"


def _client(engine: Engine) -> TestClient:
    app = create_app(engine=engine, clock=lambda: NOW)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _settings_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": API_KEY,
        "model": "llama-3.3-70b-versatile",
    }
    payload.update(overrides)
    return payload


def _put(client: TestClient, **overrides: object):
    return client.put(
        "/api/v1/ai/settings",
        json=_settings_payload(**overrides),
        headers={"Origin": LOCAL_ORIGIN},
    )


def _post_connection(client: TestClient, payload: dict[str, object] | None = None):
    return client.post(
        "/api/v1/ai/test-connection",
        json={} if payload is None else payload,
        headers={"Origin": LOCAL_ORIGIN},
    )


class _FakeCompletions:
    def __init__(self, behaviour) -> None:
        self._behaviour = behaviour
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object):
        self.calls.append(kwargs)
        return self._behaviour(**kwargs)


class _FakeClient:
    def __init__(self, behaviour) -> None:
        self.completions = _FakeCompletions(behaviour)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the SDK client with a deterministic fake.

    A test may replace ``state["client"]`` to make the provider fail.
    """

    state: dict[str, Any] = {
        "client": _FakeClient(lambda **_kwargs: SimpleNamespace()),
        "builds": [],
    }

    def _fake_build_client(*, base_url: str, api_key: str | None, timeout: float):
        state["builds"].append({"base_url": base_url, "api_key": api_key, "timeout": timeout})
        return state["client"]

    monkeypatch.setattr(connection, "_build_client", _fake_build_client)
    return state


def _provider_failure(error: BaseException):
    def _raise(**_kwargs: object):
        raise error

    return _raise


def _status_error(error_type: type[openai.APIStatusError], status: int) -> BaseException:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return error_type("provider rejected the request", response=response, body=None)


def test_get_settings_is_unconfigured_before_any_save(engine: Engine) -> None:
    with _client(engine) as client:
        response = client.get("/api/v1/ai/settings")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["provider"] == "custom"
    assert body["base_url"] == ""
    assert body["api_key_masked"] is None
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["is_configured"] is False
    assert body["updated_at_utc"] is None


def test_put_persists_settings_and_get_returns_only_a_mask(engine: Engine) -> None:
    with _client(engine) as client:
        saved = _put(client)
        loaded = client.get("/api/v1/ai/settings")

    assert saved.status_code == 200
    assert saved.json()["api_key_masked"] == MASKED_KEY
    assert saved.json()["is_configured"] is True
    assert saved.json()["updated_at_utc"] is not None
    assert API_KEY not in saved.text

    assert loaded.status_code == 200
    assert loaded.json()["api_key_masked"] == MASKED_KEY
    assert loaded.json()["provider"] == "groq"
    assert loaded.json()["base_url"] == "https://api.groq.com/openai/v1"
    assert loaded.json()["model"] == "llama-3.3-70b-versatile"
    assert API_KEY not in loaded.text

    with engine.connect() as db:
        stored = db.execute(sa.select(ai_settings.c.api_key)).scalar_one()
    assert stored == API_KEY


def test_put_with_the_displayed_mask_keeps_the_stored_key(engine: Engine) -> None:
    with _client(engine) as client:
        _put(client)
        updated = _put(client, api_key=MASKED_KEY, model="llama-3.1-8b-instant")

    assert updated.status_code == 200
    assert updated.json()["model"] == "llama-3.1-8b-instant"
    assert updated.json()["api_key_masked"] == MASKED_KEY

    with engine.connect() as db:
        stored = db.execute(sa.select(ai_settings.c.api_key)).scalar_one()
    assert stored == API_KEY


def test_put_with_a_blank_key_keeps_the_stored_key(engine: Engine) -> None:
    with _client(engine) as client:
        _put(client)
        updated = _put(client, api_key="")

    assert updated.status_code == 200
    with engine.connect() as db:
        stored = db.execute(sa.select(ai_settings.c.api_key)).scalar_one()
    assert stored == API_KEY


def test_put_with_a_new_key_replaces_the_stored_key(engine: Engine) -> None:
    new_key = "gsk_replacement_0000000000wxyz"
    with _client(engine) as client:
        _put(client)
        updated = _put(client, api_key=new_key)

    assert updated.status_code == 200
    assert updated.json()["api_key_masked"] == "gsk...wxyz"
    with engine.connect() as db:
        stored = db.execute(sa.select(ai_settings.c.api_key)).scalar_one()
    assert stored == new_key


def test_put_requires_an_api_key_for_a_hosted_provider(engine: Engine) -> None:
    with _client(engine) as client:
        saved = _put(client, api_key="")

    assert saved.status_code == 200
    assert saved.json()["is_configured"] is False


def test_ollama_is_configured_without_an_api_key(engine: Engine) -> None:
    with _client(engine) as client:
        saved = _put(
            client,
            provider="ollama",
            base_url="http://localhost:11434/v1",
            api_key="",
            model="llama3.2",
        )

    assert saved.status_code == 200
    assert saved.json()["is_configured"] is True
    assert saved.json()["api_key_masked"] is None


def test_put_rejects_an_unknown_provider(engine: Engine) -> None:
    with _client(engine) as client:
        response = _put(client, provider="not-a-provider")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_provider"


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "api.groq.com/v1",
        "ftp://api.groq.com/v1",
        "https://user:secret@api.groq.com/v1",
        "https://api.groq.com/v1?key=secret",
    ],
)
def test_put_rejects_an_invalid_base_url(engine: Engine, base_url: str) -> None:
    with _client(engine) as client:
        response = _put(client, base_url=base_url)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_base_url"
    assert "secret" not in response.text


def test_put_rejects_a_blank_model(engine: Engine) -> None:
    with _client(engine) as client:
        response = _put(client, model="   ")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_model"


def test_connection_test_succeeds_and_reports_latency(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    with _client(engine) as client:
        response = _post_connection(
            client,
            {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": API_KEY,
                "model": "llama-3.3-70b-versatile",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["model"] == "llama-3.3-70b-versatile"
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0

    client_builds = fake_provider["builds"]
    assert client_builds[0]["base_url"] == "https://api.groq.com/openai/v1"
    assert client_builds[0]["api_key"] == API_KEY
    assert client_builds[0]["timeout"] == 10.0


def test_connection_test_uses_saved_settings_and_keeps_the_stored_key(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    with _client(engine) as client:
        _put(client)
        response = _post_connection(client, {"api_key": MASKED_KEY})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake_provider["builds"][0]["api_key"] == API_KEY


def test_connection_test_reports_a_rejected_key(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    fake_provider["client"] = _FakeClient(
        _provider_failure(_status_error(openai.AuthenticationError, 401))
    )

    with _client(engine) as client:
        response = _post_connection(
            client,
            {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": API_KEY,
                "model": "llama-3.3-70b-versatile",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["latency_ms"] is None
    assert "401" in body["error"]
    assert API_KEY not in response.text


def test_connection_test_reports_a_timeout(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    fake_provider["client"] = _FakeClient(
        _provider_failure(openai.APITimeoutError(request=request))
    )

    with _client(engine) as client:
        response = _post_connection(
            client,
            {
                "base_url": "http://localhost:11434/v1",
                "model": "llama3.2",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "did not respond within" in body["error"]


def test_connection_test_reports_an_unreachable_endpoint(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    fake_provider["client"] = _FakeClient(
        _provider_failure(openai.APIConnectionError(request=request))
    )

    with _client(engine) as client:
        response = _post_connection(
            client,
            {"base_url": "http://localhost:11434/v1", "model": "llama3.2"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "Could not reach the endpoint" in response.json()["error"]


def test_connection_test_answers_instead_of_raising_for_an_invalid_url(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    with _client(engine) as client:
        response = _post_connection(
            client,
            {"base_url": "not-a-url", "model": "llama3.2"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "http://" in response.json()["error"]
    assert fake_provider["builds"] == []


def test_connection_test_without_configuration_asks_for_settings(
    engine: Engine,
    fake_provider: dict[str, Any],
) -> None:
    with _client(engine) as client:
        response = _post_connection(client)

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "Base URL" in response.json()["error"]
    assert fake_provider["builds"] == []


def test_ai_settings_migration_round_trips_without_losing_rows(
    engine: Engine,
    database_url: str,
    seed_config,
) -> None:
    """The new table is additive: rolling it back must not touch existing rows."""

    seed_config(roles=1, companies_count=1)
    downgrade_database(database_url, "0006_google_sheets_workspace_state")

    tables_after_downgrade = set(sa.inspect(engine).get_table_names())
    assert "ai_settings" not in tables_after_downgrade
    assert {"target_roles", "companies"} <= tables_after_downgrade

    run_migrations(database_url)
    assert "ai_settings" in set(sa.inspect(engine).get_table_names())

    with engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(target_roles)).scalar_one() == 1
        assert db.execute(sa.select(sa.func.count()).select_from(companies)).scalar_one() == 1


def test_ai_settings_migration_keeps_existing_tables(engine: Engine) -> None:
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())

    assert "ai_settings" in tables
    assert {"target_roles", "companies", "search_runs", "google_sheets_settings"} <= tables

    columns = {column["name"] for column in inspector.get_columns("ai_settings")}
    assert {
        "id",
        "provider",
        "base_url",
        "api_key",
        "model",
        "is_configured",
        "updated_at_utc",
    } == columns

    # Existing configuration rows are untouched by the additive migration.
    with engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(ai_settings)).scalar_one() == 0


def test_mask_api_key_is_stable_and_hides_the_secret() -> None:
    assert mask_api_key(None) is None
    assert mask_api_key("") is None
    assert mask_api_key(API_KEY) == MASKED_KEY
    assert mask_api_key(MASKED_KEY) == MASKED_KEY
    assert mask_api_key("short") == "***"
    assert is_masked_api_key(MASKED_KEY) is True
    assert is_masked_api_key(API_KEY) is False
    assert is_masked_api_key("") is False


def test_resolve_api_key_update_only_replaces_a_real_new_key() -> None:
    assert resolve_api_key_update(None, API_KEY) == API_KEY
    assert resolve_api_key_update("", API_KEY) == API_KEY
    assert resolve_api_key_update("   ", API_KEY) == API_KEY
    assert resolve_api_key_update(MASKED_KEY, API_KEY) == API_KEY
    assert resolve_api_key_update("gsk_new_key_value", API_KEY) == "gsk_new_key_value"
    assert resolve_api_key_update(MASKED_KEY, None) is None
