"""Thin client for any OpenAI-compatible /chat/completions endpoint.

Works with hosted providers that expose this API and with local servers
(Ollama, vLLM, LM Studio). Provider, key and model come from env vars.
"""
import logging
import time
from typing import List, Optional

import httpx

from app.config import ProviderConfig

log = logging.getLogger("gridwise.llm")


class LLMError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


RETRY_MIN_S = 2.0


class LLMClient:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self._json_mode_unsupported: set = set()

    async def chat(self, provider: ProviderConfig, messages: List[dict], timeout: float) -> str:
        """One call with a single retry for fast transient failures (429, 5xx, connection
        errors), as long as enough of the time budget is left."""
        end = time.monotonic() + timeout
        try:
            return await self._call(provider, messages, timeout)
        except LLMError as exc:
            left = end - time.monotonic()
            if not exc.retryable or left < RETRY_MIN_S:
                raise
            log.info("%s: retrying once after transient error", provider.name)
            return await self._call(provider, messages, left)

    async def _call(self, provider: ProviderConfig, messages: List[dict], timeout: float) -> str:
        if not provider.enabled:
            raise LLMError(f"{provider.name} provider not configured")
        if timeout <= 0.5:
            raise LLMError("no time left for model call")

        body = {
            "model": provider.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 900,
        }
        use_json_mode = provider.name not in self._json_mode_unsupported
        if use_json_mode:
            body["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        url = f"{provider.base_url}/chat/completions"

        try:
            resp = await self.http.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            raise LLMError(f"{provider.name} timed out")  # budget used up, not retried
        except httpx.HTTPError as exc:
            raise LLMError(f"{provider.name} connection error: {type(exc).__name__}", retryable=True)

        if resp.status_code == 400 and use_json_mode:
            # Some providers/models reject response_format; remember and retry once without it.
            self._json_mode_unsupported.add(provider.name)
            log.info("%s rejected JSON mode, retrying without it", provider.name)
            return await self._call(provider, messages, timeout - 0.5)
        if resp.status_code >= 400:
            # Status only; provider error bodies can echo request details.
            retryable = resp.status_code == 429 or resp.status_code >= 500
            raise LLMError(f"{provider.name} returned HTTP {resp.status_code}", retryable=retryable)

        try:
            content: Optional[str] = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError(f"{provider.name} returned an unexpected payload")
        if not content:
            raise LLMError(f"{provider.name} returned empty content")
        return content
