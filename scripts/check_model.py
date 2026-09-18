"""Checks the configured model provider(s): key accepted, model id available,
and one real interpretation call with its latency. Never prints the key.

  python scripts/check_model.py
"""
import asyncio
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.interpret.intent_parser import parse_intents  # noqa: E402
from app.interpret.llm_client import LLMClient, LLMError  # noqa: E402
from app.interpret.prompt import build_messages  # noqa: E402


async def check(http: httpx.AsyncClient, provider) -> bool:
    print(f"[{provider.name}] {provider.base_url}  model={provider.model}")
    if not provider.enabled:
        print("  not configured")
        return False
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    try:
        r = await http.get(f"{provider.base_url}/models", headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        print(f"  cannot reach provider: {type(exc).__name__}")
        return False
    if r.status_code == 401:
        print("  key rejected (401) - check LLM_API_KEY / LLM_BACKUP_API_KEY")
        return False
    if r.status_code == 200:
        ids = sorted(m.get("id", "") for m in r.json().get("data", []))
        if provider.model not in ids:
            print(f"  model '{provider.model}' not offered. Available: {', '.join(ids[:25])}")
            return False
        print("  key ok, model available")

    note = "Do not charge the battery between 2 PM and 4 PM."
    t0 = time.perf_counter()
    try:
        text = await LLMClient(http).chat(provider, build_messages({0: note}), 15)
    except LLMError as exc:
        print(f"  test call failed: {exc}")
        return False
    intents, errors = parse_intents(text, [0])
    print(f"  test call {time.perf_counter() - t0:.2f}s -> {intents.get(0)} {errors or ''}")
    return not errors


async def main() -> int:
    s = load_settings()
    async with httpx.AsyncClient() as http:
        ok = await check(http, s.primary)
        if s.backup.enabled:
            await check(http, s.backup)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
