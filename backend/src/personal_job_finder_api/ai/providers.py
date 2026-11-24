"""Preset OpenAI-compatible providers and the validation applied to user input.

Presets only pre-fill the Settings form. The stored configuration is always the
user's own values, so a provider can be edited afterwards without the preset
silently overwriting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from personal_job_finder_api.ai.errors import AIServiceError


@dataclass(frozen=True, slots=True)
class AIProviderPreset:
    provider: str
    label: str
    base_url: str
    default_model: str
    requires_api_key: bool


PROVIDER_PRESETS: dict[str, AIProviderPreset] = {
    "groq": AIProviderPreset(
        provider="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        requires_api_key=True,
    ),
    "openrouter": AIProviderPreset(
        provider="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="google/gemini-2.5-flash",
        requires_api_key=True,
    ),
    "deepseek": AIProviderPreset(
        provider="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        requires_api_key=True,
    ),
    "gemini": AIProviderPreset(
        provider="gemini",
        label="Google Gemini (OpenAI-compatible)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-2.5-flash",
        requires_api_key=True,
    ),
    "ollama": AIProviderPreset(
        provider="ollama",
        label="Ollama (local)",
        base_url="http://localhost:11434/v1",
        default_model="llama3.2",
        requires_api_key=False,
    ),
    "custom": AIProviderPreset(
        provider="custom",
        label="Custom (OpenAI-compatible)",
        base_url="",
        default_model="",
        requires_api_key=False,
    ),
}

MAX_BASE_URL_LENGTH = 2048
MAX_MODEL_LENGTH = 200


def validate_provider(value: str) -> str:
    """Return a known provider identifier or raise a user-facing error."""

    provider = value.strip().casefold()
    if provider not in PROVIDER_PRESETS:
        allowed = ", ".join(sorted(PROVIDER_PRESETS))
        raise AIServiceError(
            "invalid_provider",
            f"Choose one of the supported providers: {allowed}.",
        )
    return provider


def validate_base_url(value: str) -> str:
    """Accept only an absolute http(s) endpoint with no embedded credentials.

    Local endpoints are expected (Ollama, LM Studio), so loopback hosts are
    allowed. Embedded credentials are rejected because the key belongs in
    ``api_key`` and would otherwise be copied into logs and error messages.
    """

    base_url = value.strip()
    if not base_url:
        raise AIServiceError("invalid_base_url", "Enter the Base URL of the provider.")
    if len(base_url) > MAX_BASE_URL_LENGTH:
        raise AIServiceError(
            "invalid_base_url",
            f"The Base URL must be at most {MAX_BASE_URL_LENGTH} characters.",
        )
    try:
        parsed = urlsplit(base_url)
    except ValueError as error:
        raise AIServiceError("invalid_base_url", "The Base URL is not a valid URL.") from error
    if parsed.scheme not in {"http", "https"}:
        raise AIServiceError(
            "invalid_base_url",
            "The Base URL must start with http:// or https://.",
        )
    if not parsed.hostname:
        raise AIServiceError("invalid_base_url", "The Base URL must include a host name.")
    if parsed.username is not None or parsed.password is not None:
        raise AIServiceError(
            "invalid_base_url",
            "Remove the credentials from the Base URL and put the key in the API Key field.",
        )
    if parsed.query or parsed.fragment:
        raise AIServiceError(
            "invalid_base_url",
            "The Base URL must not include a query string or fragment.",
        )
    return base_url


def validate_model(value: str) -> str:
    """Return a trimmed model identifier or raise a user-facing error."""

    model = value.strip()
    if not model:
        raise AIServiceError("invalid_model", "Enter the model name to use.")
    if len(model) > MAX_MODEL_LENGTH:
        raise AIServiceError(
            "invalid_model",
            f"The model name must be at most {MAX_MODEL_LENGTH} characters.",
        )
    return model
