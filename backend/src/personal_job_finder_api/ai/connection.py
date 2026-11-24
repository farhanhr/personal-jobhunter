"""Minimal connectivity probe for a configured OpenAI-compatible endpoint.

The probe spends as few tokens as possible: one short prompt, a tiny completion
budget, and no retries. It reports a structured result instead of raising, so a
bad URL or a rejected key can never surface as an unhandled exception in the
Settings UI.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import openai
from openai import AsyncOpenAI

from personal_job_finder_api.ai.client import build_async_client
from personal_job_finder_api.ai.errors import AIServiceError
from personal_job_finder_api.ai.providers import validate_base_url, validate_model

DEFAULT_TIMEOUT_SECONDS = 10.0
PING_MAX_TOKENS = 16
PING_MESSAGES: tuple[dict[str, str], ...] = (
    {
        "role": "system",
        "content": (
            'You are a connectivity probe. Reply with exactly {"status":"ok"} and nothing else.'
        ),
    },
    {"role": "user", "content": "ping"},
)

ClientFactory = Callable[..., AsyncOpenAI]


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    success: bool
    latency_ms: int | None = None
    model: str | None = None
    error: str | None = None


def _build_client(*, base_url: str, api_key: str | None, timeout: float) -> AsyncOpenAI:
    """Build the client for one probe. Tests patch this seam, never the network."""

    return build_async_client(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
    )


def describe_connection_error(error: BaseException, timeout: float) -> str:
    """Map a provider failure to one actionable sentence.

    Provider error bodies are deliberately not echoed: they can be long, and
    some providers quote the submitted key back in their message.
    """

    if isinstance(error, openai.AuthenticationError):
        return (
            "The provider rejected the API key (HTTP 401). "
            "Check the key for this provider and save it again."
        )
    if isinstance(error, openai.PermissionDeniedError):
        return (
            "The provider denied access (HTTP 403). Check that this key may use the selected model."
        )
    if isinstance(error, openai.NotFoundError):
        return (
            "The endpoint or model was not found (HTTP 404). Check the Base URL and the model name."
        )
    if isinstance(error, openai.RateLimitError):
        return (
            "The provider is rate limiting this key (HTTP 429). "
            "Wait a moment and try the connection again."
        )
    if isinstance(error, openai.APITimeoutError):
        return f"The provider did not respond within {timeout:g} seconds. Try again."
    if isinstance(error, openai.APIConnectionError):
        return (
            "Could not reach the endpoint. Check the Base URL, and make sure a local "
            "server such as Ollama is running."
        )
    if isinstance(error, openai.APIStatusError):
        return (
            f"The provider returned HTTP {error.status_code}. "
            "Check the Base URL, the model name, and the API key."
        )
    return (
        "The connection test failed before the provider answered. "
        "Check the Base URL and the model name."
    )


async def test_llm_connection(
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client_factory: ClientFactory | None = None,
) -> ConnectionTestResult:
    """Ping the endpoint and report latency, never raising for provider errors."""

    try:
        resolved_url = validate_base_url(base_url)
        resolved_model = validate_model(model)
    except AIServiceError as error:
        return ConnectionTestResult(success=False, error=error.message)

    factory: ClientFactory = client_factory or _build_client
    started = time.perf_counter()
    try:
        client = factory(base_url=resolved_url, api_key=api_key, timeout=timeout)
        await client.chat.completions.create(
            model=resolved_model,
            messages=list(PING_MESSAGES),
            max_tokens=PING_MAX_TOKENS,
            temperature=0,
        )
    except Exception as error:  # The probe must never raise for a provider failure.
        return ConnectionTestResult(
            success=False,
            model=resolved_model,
            error=describe_connection_error(error, timeout),
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ConnectionTestResult(success=True, latency_ms=latency_ms, model=resolved_model)
