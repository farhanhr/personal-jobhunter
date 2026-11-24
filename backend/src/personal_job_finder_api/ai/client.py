"""Shared async client construction for every AI call.

One place decides the SDK options so the connection probe and the real
extraction calls cannot drift apart. ``max_retries`` stays configurable because
a fast probe wants none while a longer generation may tolerate one.
"""

from __future__ import annotations

from openai import AsyncOpenAI


def build_async_client(
    *,
    base_url: str,
    api_key: str | None,
    timeout: float,
    max_retries: int = 0,
) -> AsyncOpenAI:
    """Build a client for any OpenAI-compatible endpoint.

    Local servers such as Ollama accept any key, but the SDK requires a
    non-empty value, so a missing key becomes the literal ``none``.
    """

    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or "none",
        timeout=timeout,
        max_retries=max_retries,
    )
