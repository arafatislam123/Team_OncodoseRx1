"""Measures interpretation accuracy on reworded notes (tests/data/paraphrases.json).

Calls the interpreter directly (no server needed) with the model configured in the
environment / .env. Cache is disabled so every note really goes to the model.

  python scripts/run_paraphrases.py                 # configured model
  python scripts/run_paraphrases.py --fallback-only # only the degraded extractor (no model)
"""
import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ProviderConfig, load_settings  # noqa: E402
from app.interpret.cache import IntentCache  # noqa: E402
from app.interpret.interpreter import Interpreter  # noqa: E402
from app.interpret.llm_client import LLMClient  # noqa: E402

TOL = 0.01


def _match(got: dict, item: dict) -> bool:
    if got["directive_type"] != item["type"]:
        return False
    ga, ea = got["structured_adjustment"], item["adj"]
    if ea is None or ga is None:
        return ga == ea
    if set(ga) != set(ea) or ga["hours"] != ea["hours"]:
        return False
    return all(abs(float(ga[k]) - float(v)) <= TOL for k, v in ea.items() if k != "hours")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "tests" / "data" / "paraphrases.json"))
    ap.add_argument("--fallback-only", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    if args.fallback_only:
        off = ProviderConfig("off", "", "", "")
        settings = replace(settings, primary=off, backup=off, enable_degraded_fallback=True)
    elif not settings.primary.enabled:
        print("No model configured: set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL (or use --fallback-only).")
        return 2
    else:
        settings = replace(settings, enable_degraded_fallback=False)  # measure the model alone

    items = json.loads(Path(args.file).read_text(encoding="utf-8"))["items"]
    ok, times = 0, []
    async with httpx.AsyncClient() as http:
        interp = Interpreter(LLMClient(http), settings, IntentCache(0))
        for item in items:
            t0 = time.perf_counter()
            [d] = await interp.interpret([item["note"]], item["capacity"],
                                         deadline=time.monotonic() + settings.request_deadline_s)
            times.append(time.perf_counter() - t0)
            got = d.to_response()
            good = _match(got, item)
            ok += good
            if not good:
                print(f"MISS  {item['note']}\n      expected {item['type']} {item['adj']}\n"
                      f"      got      {got['directive_type']} {got['structured_adjustment']} ({d.source})")
    times.sort()
    p95 = times[min(len(times) - 1, int(round(0.95 * len(times))) - 1)]
    print(f"\n{ok}/{len(items)} correct ({100 * ok / len(items):.0f}%) | "
          f"latency p50={times[len(times) // 2]:.2f}s p95={p95:.2f}s")
    return 0 if ok == len(items) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
