"""Small load test against a running service: sends the public sample inputs
repeatedly with some concurrency and reports status codes and latency.

  python scripts/load_test.py --url https://your-app.example.com --requests 30 --concurrency 5
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--requests", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--file", default=str(ROOT / "samples" / "public_cases.json"))
    args = ap.parse_args()

    inputs = [c["input"] for c in json.loads(Path(args.file).read_text(encoding="utf-8"))["cases"]]
    url = args.url.rstrip("/") + "/optimize-energy"

    def one(i: int):
        t0 = time.perf_counter()
        try:
            r = httpx.post(url, json=inputs[i % len(inputs)], timeout=35)
            code = r.status_code
            if code == 200:
                r.json()
        except Exception as exc:  # noqa: BLE001
            code = type(exc).__name__
        return code, time.perf_counter() - t0

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(one, range(args.requests)))
    wall = time.perf_counter() - t_start

    codes = Counter(c for c, _ in results)
    lat = sorted(t for _, t in results)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * len(lat))) - 1)]
    print(f"requests={len(results)} concurrency={args.concurrency} wall={wall:.1f}s")
    print(f"status codes: {dict(codes)}")
    print(f"latency p50={statistics.median(lat):.2f}s p95={p95:.2f}s max={lat[-1]:.2f}s")
    return 0 if codes.get(200, 0) == len(results) and p95 <= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
