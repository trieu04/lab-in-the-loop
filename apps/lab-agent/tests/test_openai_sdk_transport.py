"""OpenAI SDK endpoint construction regressions."""

from __future__ import annotations

import httpx

from lab_agent.config import Settings


async def test_canonical_openai_endpoint_requests_v1_chat_completions() -> None:
    """The real SDK must preserve OpenAI's versioned API path."""
    from openai import AsyncOpenAI

    captured_urls: list[str] = []

    async def capture_request(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            },
            request=request,
        )

    settings = Settings(openai_api_key="test-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(capture_request)) as transport:
        client = AsyncOpenAI(
            api_key="test-key",
            base_url=settings.provider_endpoints["openai"],
            http_client=transport,
        )
        await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "ping"}]
        )

    assert captured_urls == ["https://api.openai.com/v1/chat/completions"]
