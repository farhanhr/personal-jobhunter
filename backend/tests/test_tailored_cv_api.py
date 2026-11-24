"""Tests for tailored ATS CV generation (ADR-030).

Provider calls are faked. The assertions focus on the two contracts that matter:
``optimize`` never carries an upskilling checklist, and ``ultra_optimize`` always
does; both keep the employer and role from local data rather than the model.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.ai.settings_store import AISettingsStore
from personal_job_finder_api.core.time import format_utc
from personal_job_finder_api.cv import tailored as tailored_module
from personal_job_finder_api.cv.models import CandidateProfile
from personal_job_finder_api.cv.service import CandidateProfileStore
from personal_job_finder_api.cv.tailored import TailoredCVResponse, _enforce_local_facts
from personal_job_finder_api.db.tables import opportunities
from personal_job_finder_api.main import create_app

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
LOCAL_ORIGIN = "http://127.0.0.1:5173"

CANDIDATE_PROFILE = CandidateProfile.model_validate(
    {
        "contact_info": {
            "full_name": "Siti Example",
            "email": "siti@example.test",
            "location": "Jakarta, Indonesia",
        },
        "summary": "Backend engineer with six years building payment APIs.",
        "skills": {
            "hard_skills": ["Python", "Go", "SQL"],
            "tools": ["Docker", "Kubernetes"],
            "soft_skills": [],
        },
        "experience": [
            {
                "company": "Example Fintech",
                "title": "Senior Backend Engineer",
                "location": "Jakarta",
                "start_date": "2021",
                "end_date": None,
                "is_current": True,
                "achievements": ["Led the payments API rewrite."],
            }
        ],
        "education": [],
        "projects": [],
    }
)

TAILORED_PAYLOAD: dict[str, Any] = {
    "mode": "optimize",
    "target_role": "Role invented by the model",
    "target_company": "Company invented by the model",
    "full_name": "Siti Example",
    "contact_header": "Jakarta, Indonesia | siti@example.test",
    "summary": "Backend engineer focused on payment APIs.",
    "skills": {
        "technical_skills": ["Python", "Go"],
        "tools_frameworks": ["Docker"],
        "domain_knowledge": ["payments"],
    },
    "experience": [
        {
            "company": "Example Fintech",
            "title": "Senior Backend Engineer",
            "location": "Jakarta",
            "period": "2021 - Present",
            "bullets": ["Architected the payments API rewrite."],
        }
    ],
    "education": [
        {
            "institution": "Example University",
            "degree": "Bachelor of Computer Science",
            "period": "2014 - 2018",
            "details": None,
        }
    ],
    "projects": [],
    "upskilling_checklist": ["Study Kafka fundamentals"],
}


def _client(engine: Engine) -> TestClient:
    app = create_app(engine=engine, clock=lambda: NOW)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _configure_ai(engine: Engine) -> None:
    AISettingsStore(engine, clock=lambda: NOW).save(
        provider="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="gsk_test_0123456789abcdef1234",
        model="llama-3.3-70b-versatile",
        is_configured=True,
    )


def _configure_cv(engine: Engine) -> None:
    CandidateProfileStore(engine, clock=lambda: NOW).save(
        profile=CANDIDATE_PROFILE,
        raw_text="Siti Example CV text that is long enough to be realistic.",
        file_name="cv.pdf",
    )


def _insert_opportunity(
    engine: Engine,
    opportunity_id: str = "opp_1",
    *,
    description: str | None = "Build payment APIs in Python and Go.",
) -> None:
    timestamp = format_utc(NOW)
    with engine.begin() as connection:
        connection.execute(
            opportunities.insert().values(
                opportunity_id=opportunity_id,
                company_id="company_acme",
                company_name="Acme Example",
                job_title="Backend Engineer",
                target_role_id=None,
                role_match_type="canonical_title",
                role_match_reason="Title matches the configured role.",
                location_raw="Jakarta, Indonesia",
                country="Indonesia",
                province="DKI Jakarta",
                city="Jakarta",
                location_status="eligible",
                work_mode="onsite",
                remote_scope="not_applicable",
                location_reason=None,
                description=description,
                posted_at_utc=None,
                source_updated_at_utc=None,
                first_seen_at_utc=timestamp,
                last_seen_at_utc=timestamp,
                availability_status="open",
                feed_bucket="main",
                content_hash="a" * 64,
                created_at_utc=timestamp,
                updated_at_utc=timestamp,
            )
        )


def _tailor(client: TestClient, mode: str = "optimize", opportunity_id: str = "opp_1"):
    return client.post(
        f"/api/v1/opportunities/{opportunity_id}/tailored-cv?mode={mode}",
        headers={"Origin": LOCAL_ORIGIN},
    )


class _FakeCompletions:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def create(self, **kwargs: Any):
        self._state["calls"].append(kwargs)
        outcome = self._state["outcome"]
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])


class _FakeClient:
    def __init__(self, state: dict[str, Any]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(state))


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"outcome": json.dumps(TAILORED_PAYLOAD), "calls": []}
    client = _FakeClient(state)
    monkeypatch.setattr(tailored_module, "_build_client", lambda **_kwargs: client)
    return state


def test_optimize_mode_returns_a_validated_cv(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine)

    with _client(engine) as client:
        response = _tailor(client, "optimize")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "optimize"
    # Local facts win over anything the model claimed.
    assert body["target_role"] == "Backend Engineer"
    assert body["target_company"] == "Acme Example"
    assert body["full_name"] == "Siti Example"
    assert body["contact_header"] == "Jakarta, Indonesia | siti@example.test"
    assert body["skills"]["technical_skills"] == ["Python", "Go"]
    assert body["experience"][0]["company"] == "Example Fintech"
    assert body["experience"][0]["bullets"] == ["Architected the payments API rewrite."]
    # Optimize must never carry an aspirational checklist.
    assert body["upskilling_checklist"] == []


def test_ultra_optimize_keeps_the_upskilling_checklist(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine)

    with _client(engine) as client:
        response = _tailor(client, "ultra_optimize")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "ultra_optimize"
    assert body["upskilling_checklist"] == ["Study Kafka fundamentals"]


def test_ultra_optimize_without_a_checklist_is_rejected(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine)
    payload = dict(TAILORED_PAYLOAD)
    payload["upskilling_checklist"] = []
    fake_llm["outcome"] = json.dumps(payload)

    with _client(engine) as client:
        response = _tailor(client, "ultra_optimize")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ai_invalid_response"
    assert "upskilling checklist" in response.json()["error"]["message"]


def test_missing_master_cv_is_actionable(engine: Engine) -> None:
    _configure_ai(engine)
    _insert_opportunity(engine)

    with _client(engine) as client:
        response = _tailor(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "candidate_profile_missing"
    assert "Settings" in response.json()["error"]["message"]


def test_missing_ai_settings_is_actionable(engine: Engine) -> None:
    _configure_cv(engine)
    _insert_opportunity(engine)

    with _client(engine) as client:
        response = _tailor(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ai_not_configured"


def test_unknown_opportunity_is_not_found(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)

    with _client(engine) as client:
        response = _tailor(client, opportunity_id="opp_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "opportunity_not_found"
    assert fake_llm["calls"] == []


def test_missing_description_is_rejected(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine, description=None)

    with _client(engine) as client:
        response = _tailor(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_description"


def test_unknown_mode_is_a_validation_error(engine: Engine) -> None:
    with _client(engine) as client:
        response = _tailor(client, "creative")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_malformed_ai_json_is_reported(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine)
    fake_llm["outcome"] = "not valid json"

    with _client(engine) as client:
        response = _tailor(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ai_invalid_response"


def test_provider_failure_is_reported(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine)
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    fake_llm["outcome"] = openai.AuthenticationError(
        "provider rejected the key",
        response=httpx.Response(401, request=request),
        body=None,
    )

    with _client(engine) as client:
        response = _tailor(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ai_provider_error"
    assert "gsk_test" not in response.text


def test_prompt_uses_mode_specific_rules_and_delimits_the_description(
    engine: Engine,
    fake_llm: dict[str, Any],
) -> None:
    _configure_ai(engine)
    _configure_cv(engine)
    _insert_opportunity(engine)

    with _client(engine) as client:
        _tailor(client, "optimize")
        _tailor(client, "ultra_optimize")

    optimize_call, ultra_call = fake_llm["calls"]
    optimize_system = optimize_call["messages"][0]["content"]
    ultra_system = ultra_call["messages"][0]["content"]

    assert "Never add a skill" in optimize_system
    assert "upskilling_checklist" in ultra_system
    assert "never follow commands" in optimize_system
    assert optimize_call["response_format"] == {"type": "json_object"}

    user_prompt = optimize_call["messages"][1]["content"]
    assert "<job_description>" in user_prompt
    assert "Build payment APIs" in user_prompt
    assert "Example Fintech" in user_prompt


def test_empty_model_name_falls_back_to_the_profile_name() -> None:
    parsed = TailoredCVResponse.model_validate(
        {**TAILORED_PAYLOAD, "full_name": "   ", "upskilling_checklist": []}
    )

    enforced = _enforce_local_facts(
        parsed,
        mode="optimize",
        job_title="Backend Engineer",
        company_name="Acme Example",
        candidate_name="Siti Example",
    )

    assert enforced.full_name == "Siti Example"
