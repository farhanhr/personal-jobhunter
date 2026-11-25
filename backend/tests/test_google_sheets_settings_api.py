"""API tests for the Google Sheets setup endpoints.

Uses a temporary data directory and a temporary credential path. The real key under
``.local/credentials/`` is never read or written.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets import orchestration
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.main import create_app

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
ORIGIN = "http://127.0.0.1:5173"
ORIGIN_HEADERS = {"Origin": ORIGIN}
SYNTHETIC_URL = "https://docs.google.com/spreadsheets/d/1SyntheticSheetId0123456789abcdefXYZ/edit"
FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nfake-not-real\n-----END PRIVATE KEY-----\n"
EMAIL = "pjf-reader@pjf-test-project.iam.gserviceaccount.com"


def _key_bytes(email: str = EMAIL) -> bytes:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "pjf-test-project",
            "private_key_id": "k",
            "private_key": FAKE_KEY,
            "client_email": email,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ).encode()


OTHER_EMAIL = "pjf-writer@pjf-test-project.iam.gserviceaccount.com"


@pytest.fixture
def key_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "local" / "credentials" / "google-service-account.json"
    monkeypatch.setattr(Settings, "google_service_account_file", property(lambda self: path))
    return path


@pytest.fixture
def client(engine: Engine, tmp_path: Path, key_path: Path) -> TestClient:
    app = create_app(
        app_settings=Settings(data_dir=tmp_path),
        engine=engine,
        clock=lambda: NOW,
    )
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _bootstrap_headers() -> dict[str, str]:
    """State-changing requests only need the local browser Origin."""

    return dict(ORIGIN_HEADERS)


def test_status_surfaces_an_invalid_environment_spreadsheet_url(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed `.env` URL is reported as invalid configuration, not a 500."""

    def raise_invalid(_engine, _settings):
        raise GoogleSheetsError(
            "spreadsheet_url_invalid",
            "The configured spreadsheet URL is not a valid Google Sheets URL.",
        )

    monkeypatch.setattr(
        "personal_job_finder_api.api.v1.google_sheets_settings.effective_spreadsheet",
        raise_invalid,
    )

    response = client.get("/api/v1/integrations/google-sheets/status")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["configuration_error"] == "spreadsheet_url_invalid"
    assert payload["spreadsheet_configured"] is False
    assert payload["workspace_ready"] is False


def test_status_starts_not_configured_and_never_leaks_key_text(client: TestClient) -> None:
    response = client.get("/api/v1/integrations/google-sheets/status")

    assert response.status_code == 200
    assert response.json()["state"] == "not_configured"
    assert "PRIVATE KEY" not in response.text


def test_direct_upload_without_a_setup_token_is_accepted(
    client: TestClient, key_path: Path
) -> None:
    """Loopback Origin validation replaces the old setup-token exchange."""

    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**ORIGIN_HEADERS, "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["service_account_email"] == EMAIL
    assert key_path.exists()


def test_multipart_upload_accepts_the_downloaded_json_file(
    client: TestClient, key_path: Path
) -> None:
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        files={"file": ("example-project-key.json", _key_bytes(), "application/json")},
        headers=ORIGIN_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert response.json()["service_account_email"] == EMAIL
    assert [path.name for path in key_path.parent.iterdir()] == ["google-service-account.json"]
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_multipart_upload_without_a_file_is_rejected(client: TestClient) -> None:
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        files={"unrelated": (None, "value")},
        headers=ORIGIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "key_missing"


def test_mutation_with_wrong_origin_is_rejected(client: TestClient) -> None:
    headers = _bootstrap_headers()
    response = client.post(
        "/api/v1/integrations/google-sheets/setup",
        json={"spreadsheet_url": SYNTHETIC_URL},
        headers={**headers, "Origin": "https://evil.test"},
    )

    assert response.status_code == 403


def test_upload_rejects_non_json_content_type(client: TestClient) -> None:
    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "text/plain"},
    )

    assert response.status_code == 415


def test_upload_rejects_oversized_body(client: TestClient) -> None:
    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=b" " * (64 * 1024 + 1),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_upload_stores_key_and_response_has_only_public_fields(
    client: TestClient, key_path: Path
) -> None:
    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_account_email"] == EMAIL
    assert body["key_installed"] is True
    assert "PRIVATE KEY" not in response.text
    assert key_path.exists()


def test_oauth_client_upload_is_rejected_with_actionable_error(client: TestClient) -> None:
    headers = _bootstrap_headers()
    oauth = json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}).encode()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=oauth,
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "oauth_client_rejected"


def test_upload_ignores_the_uploaded_filename_and_stores_one_canonical_credential(
    client: TestClient, key_path: Path
) -> None:
    """The Google Cloud download name must never become a second credential file."""

    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={
            **headers,
            "Content-Type": "application/json",
            "Content-Disposition": (
                'attachment; filename="example-project-000000-abcdef0123.json"'
            ),
        },
    )

    assert response.status_code == 200
    assert [path.name for path in key_path.parent.iterdir()] == ["google-service-account.json"]
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert json.loads(key_path.read_text(encoding="utf-8"))["client_email"] == EMAIL


def test_upload_with_a_path_like_filename_cannot_escape_the_credentials_directory(
    client: TestClient, key_path: Path, tmp_path: Path
) -> None:
    """A user-controlled filename must never influence the destination path."""

    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={
            **headers,
            "Content-Type": "application/json",
            "Content-Disposition": ('attachment; filename="../../../../escaped-credentials.json"'),
        },
    )

    assert response.status_code == 200
    assert [path.name for path in key_path.parent.iterdir()] == ["google-service-account.json"]
    assert not (tmp_path / "escaped-credentials.json").exists()
    assert not (key_path.parent.parent / "escaped-credentials.json").exists()


def test_upload_keeps_the_credentials_directory_private(client: TestClient, key_path: Path) -> None:
    """The upload path must keep the directory at 0700 and the file at 0600."""

    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_replacing_the_key_overwrites_the_canonical_file_without_a_second_credential(
    client: TestClient, key_path: Path
) -> None:
    headers = _bootstrap_headers()
    first = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    assert first.status_code == 200

    second = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(OTHER_EMAIL),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert second.status_code == 200
    assert second.json()["service_account_email"] == OTHER_EMAIL
    assert [path.name for path in key_path.parent.iterdir()] == ["google-service-account.json"]
    assert json.loads(key_path.read_text(encoding="utf-8"))["client_email"] == OTHER_EMAIL


def test_invalid_json_leaves_the_installed_credential_untouched(
    client: TestClient, key_path: Path
) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    installed = key_path.read_bytes()

    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=b"{not json",
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "key_invalid_json"
    assert key_path.read_bytes() == installed


def test_oauth_client_upload_leaves_the_installed_credential_untouched(
    client: TestClient, key_path: Path
) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    installed = key_path.read_bytes()

    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}).encode(),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "oauth_client_rejected"
    assert key_path.read_bytes() == installed


def _ready_workspace(engine: Engine) -> None:
    from personal_job_finder_api.integrations.google_sheets.settings_store import (
        GoogleSheetsSettingsStore,
    )

    GoogleSheetsSettingsStore(engine).save_spreadsheet_id(
        "1SyntheticSheetId0123456789abcdefXYZ",
        workspace_ready_at=NOW,
        configured_via="ui",
    )


def test_replacement_revalidates_access_and_clears_readiness_when_the_sheet_is_not_shared(
    client: TestClient, engine: Engine, key_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new identity that cannot open the sheet must not inherit the old readiness."""

    _ready_workspace(engine)
    headers = _bootstrap_headers()

    def denied(request: object, spreadsheet_id: str) -> None:
        raise GoogleSheetsError(
            "permission_denied",
            "Google denied access to the spreadsheet.",
        )

    monkeypatch.setattr(
        "personal_job_finder_api.api.v1.google_sheets_settings._revalidate_access",
        denied,
    )
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(OTHER_EMAIL),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "permission_denied"
    assert OTHER_EMAIL in response.json()["error"]["message"]
    # The new key is installed even though it cannot reach the sheet yet.
    assert json.loads(key_path.read_text(encoding="utf-8"))["client_email"] == OTHER_EMAIL
    body = client.get("/api/v1/integrations/google-sheets/status").json()
    assert body["workspace_ready"] is False
    assert body["onboarding_required"] is True


def test_replacement_that_reaches_the_sheet_keeps_the_workspace_ready(
    client: TestClient, engine: Engine, key_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready_workspace(engine)
    headers = _bootstrap_headers()
    checked: list[str] = []

    def allowed(request: object, spreadsheet_id: str) -> None:
        checked.append(spreadsheet_id)

    monkeypatch.setattr(
        "personal_job_finder_api.api.v1.google_sheets_settings._revalidate_access",
        allowed,
    )
    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(OTHER_EMAIL),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert checked == ["1SyntheticSheetId0123456789abcdefXYZ"]
    body = client.get("/api/v1/integrations/google-sheets/status").json()
    assert body["workspace_ready"] is True
    assert body["state"] == "ready"
    assert body["service_account_email"] == OTHER_EMAIL


def test_revalidation_reads_the_configured_sheet_without_writing(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check is read-only and asks the sheet the application actually uses."""

    from personal_job_finder_api.integrations.google_sheets.config import REQUIRED_TABS

    class RecordingTransport:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def get_spreadsheet(self, spreadsheet_id: str) -> dict[str, object]:
            self.requested.append(spreadsheet_id)
            return {
                "sheets": [
                    {"properties": {"title": title, "sheetId": index}}
                    for index, title in enumerate(REQUIRED_TABS)
                ]
            }

        def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, object]:
            return {"valueRanges": [{"values": []} for _ in ranges]}

    _ready_workspace(engine)
    headers = _bootstrap_headers()
    transport = RecordingTransport()
    monkeypatch.setattr(client.app.state, "sync_transport_factory", lambda: transport)

    response = client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(OTHER_EMAIL),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert transport.requested == ["1SyntheticSheetId0123456789abcdefXYZ"]
    assert not hasattr(transport, "batch_update")


def test_spreadsheet_url_is_validated_and_not_echoed(client: TestClient) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    response = client.post(
        "/api/v1/integrations/google-sheets/setup",
        json={"spreadsheet_url": "https://evil.test/x"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "spreadsheet_url_invalid"
    assert "evil.test" not in response.text


def test_removed_spreadsheet_put_endpoint_is_gone(client: TestClient) -> None:
    headers = _bootstrap_headers()
    response = client.put(
        "/api/v1/integrations/google-sheets/spreadsheet",
        json={"spreadsheet_url": SYNTHETIC_URL},
        headers=headers,
    )

    assert response.status_code in {404, 405}


def test_status_reports_onboarding_until_setup_completes(
    client: TestClient, key_path: Path
) -> None:
    headers = _bootstrap_headers()
    before = client.get("/api/v1/integrations/google-sheets/status").json()
    assert before["workspace_ready"] is False
    assert before["onboarding_required"] is True

    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    after_key = client.get("/api/v1/integrations/google-sheets/status").json()
    assert after_key["workspace_ready"] is False
    assert after_key["onboarding_required"] is True


def test_status_stays_ready_when_the_key_is_removed(
    client: TestClient, engine: Engine, key_path: Path
) -> None:
    from personal_job_finder_api.integrations.google_sheets.settings_store import (
        GoogleSheetsSettingsStore,
    )

    GoogleSheetsSettingsStore(engine).save_spreadsheet_id(
        "1SyntheticSheetId0123456789abcdefXYZ",
        workspace_ready_at=NOW,
        configured_via="ui",
    )

    body = client.get("/api/v1/integrations/google-sheets/status").json()

    assert body["state"] == "ready"
    assert body["workspace_ready"] is True
    assert body["onboarding_required"] is False
    assert body["key_installed"] is False
    assert body["configured_via"] == "ui"
    assert body["setup_completed_at"] is not None


def test_save_and_setup_reports_missing_key_without_traceback(client: TestClient) -> None:
    headers = _bootstrap_headers()
    response = client.post("/api/v1/integrations/google-sheets/setup", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["stage"] == "credentials"
    assert "Traceback" not in response.text


def test_save_and_setup_calls_shared_orchestration(
    client: TestClient, key_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    seen: list[str | None] = []

    def fake_setup(settings, engine, **kwargs):
        seen.append(kwargs.get("spreadsheet_input"))
        return orchestration.SetupResult(
            ready=False,
            stage="access",
            code="permission_denied",
            message="Share with the Service Account.",
            service_account_email=EMAIL,
        )

    monkeypatch.setattr(
        "personal_job_finder_api.api.v1.google_sheets_settings.run_setup", fake_setup
    )
    response = client.post(
        "/api/v1/integrations/google-sheets/setup",
        json={"spreadsheet_url": SYNTHETIC_URL},
        headers=headers,
    )

    assert seen == [SYNTHETIC_URL]
    assert response.json()["stage"] == "access"
    assert response.json()["service_account_email"] == EMAIL


def test_save_and_setup_accepts_no_body(
    client: TestClient, key_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )
    seen: list[str | None] = []

    def fake_setup(settings, engine, **kwargs):
        seen.append(kwargs.get("spreadsheet_input"))
        return orchestration.SetupResult(
            ready=True,
            stage=None,
            code="ready",
            message="Google Sheets is connected and ready.",
            workspace_ready=True,
        )

    monkeypatch.setattr(
        "personal_job_finder_api.api.v1.google_sheets_settings.run_setup", fake_setup
    )
    response = client.post("/api/v1/integrations/google-sheets/setup", headers=headers)

    assert seen == [None]
    assert response.json()["workspace_ready"] is True


def test_incompatible_workspace_returns_conflict(
    client: TestClient, key_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )

    def fake_setup(settings, engine, **kwargs):
        return orchestration.SetupResult(
            ready=False,
            stage="workspace",
            code="workspace_incompatible",
            message="This spreadsheet is not a workspace.",
            preserved="Nothing was changed in the spreadsheet or the active configuration.",
        )

    monkeypatch.setattr(
        "personal_job_finder_api.api.v1.google_sheets_settings.run_setup", fake_setup
    )
    response = client.post(
        "/api/v1/integrations/google-sheets/setup",
        json={"spreadsheet_url": SYNTHETIC_URL},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_incompatible"


def test_disconnect_removes_the_key(client: TestClient, key_path: Path) -> None:
    headers = _bootstrap_headers()
    client.put(
        "/api/v1/integrations/google-sheets/service-account",
        content=_key_bytes(),
        headers={**headers, "Content-Type": "application/json"},
    )

    response = client.delete("/api/v1/integrations/google-sheets/service-account", headers=headers)

    assert response.status_code == 200
    assert response.json()["key_installed"] is False
    assert not key_path.exists()


def test_sanitized_error_type_is_exported() -> None:
    assert issubclass(GoogleSheetsError, Exception)
