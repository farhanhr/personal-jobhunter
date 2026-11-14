"""Contract tests for the health endpoint."""

from fastapi.testclient import TestClient

from personal_job_finder_api.main import app

client = TestClient(app)


def test_health_returns_expected_payload() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "personal-job-finder-api",
        "version": "0.1.0",
    }


def test_health_does_not_expose_extra_fields() -> None:
    payload = client.get("/api/v1/health").json()

    assert set(payload) == {"status", "service", "version"}
