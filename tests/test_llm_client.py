import asyncio
import json

import httpx
import pytest

from app.config import ProviderConfig
from app.interpret.llm_client import LLMClient, LLMError

PROVIDER = ProviderConfig("primary", "http://model.test/v1", "secret-key-123", "m")
OK = {"choices": [{"message": {"content": '{"notes": []}'}}]}


def _run(handler, timeout=5.0):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await LLMClient(http).chat(PROVIDER, [{"role": "user", "content": "x"}], timeout)
    return asyncio.run(go())


def test_success_sends_expected_request():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=OK)

    assert _run(handler) == '{"notes": []}'
    assert seen["url"] == "http://model.test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key-123"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_retries_once_on_429():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json=OK)

    assert _run(handler) == '{"notes": []}'
    assert len(calls) == 2


def test_gives_up_after_second_5xx():
    def handler(req):
        return httpx.Response(503, text="upstream secret-key-123 details")

    with pytest.raises(LLMError) as exc:
        _run(handler)
    assert "secret-key-123" not in str(exc.value)  # provider error bodies are never surfaced


def test_json_mode_rejection_falls_back_to_plain():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400)
        return httpx.Response(200, json=OK)

    assert _run(handler) == '{"notes": []}'
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]


def test_unexpected_payload_is_an_error():
    with pytest.raises(LLMError):
        _run(lambda req: httpx.Response(200, json={"weird": True}))


def test_unconfigured_provider_raises():
    async def go():
        async with httpx.AsyncClient() as http:
            await LLMClient(http).chat(ProviderConfig("backup", "", "", ""), [], 5)
    with pytest.raises(LLMError):
        asyncio.run(go())
