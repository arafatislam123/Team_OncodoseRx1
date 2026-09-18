"""Runs the public sample cases against a running service, judge-style.

For each case it checks:
  - interpretation: applies, directive_type, hours and numeric value vs expected (0.01 tolerance)
  - plan validity: replays the returned hourly_plan against the EXPECTED directives
  - cost: returned cost vs the reference optimum
and prints latency (p50 / p95).

Usage:
  python scripts/run_samples.py                              # http://localhost:8000, bundled samples
  python scripts/run_samples.py --url https://your-app.example.com
  python scripts/run_samples.py --file path/to/official_public_cases.json
Exit code is non-zero if any case fails.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.optimize.constraints import build_limits  # noqa: E402
from app.schemas.directives import Directive  # noqa: E402
from app.schemas.request import OptimizeRequest  # noqa: E402
from app.validate.replay import replay  # noqa: E402

TOL = 0.01


def _truth(expected):
    out = []
    for e in expected:
        adj = e["structured_adjustment"] or {}
        value = next((v for k, v in adj.items() if k != "hours"), None)
        out.append(Directive(e["note_index"], e["directive_type"], adj.get("hours", []), value))
    return out


def _same_interp(got, exp):
    if len(got) != len(exp):
        return False, f"{len(got)} entries, expected {len(exp)}"
    for g, e in zip(got, exp):
        if g.get("note_index") != e["note_index"]:
            return False, f"note_index order wrong at {e['note_index']}"
        if g.get("applies") != e["applies"] or g.get("directive_type") != e["directive_type"]:
            return False, f"note {e['note_index']}: got {g.get('directive_type')}, expected {e['directive_type']}"
        ga, ea = g.get("structured_adjustment"), e["structured_adjustment"]
        if ea is None or ga is None:
            if ga != ea:
                return False, f"note {e['note_index']}: adjustment should be {ea}"
            continue
        if set(ga) != set(ea) or ga.get("hours") != ea.get("hours"):
            return False, f"note {e['note_index']}: got {ga}, expected {ea}"
        for k, v in ea.items():
            if k != "hours" and abs(float(ga[k]) - float(v)) > TOL:
                return False, f"note {e['note_index']}: {k}={ga[k]}, expected {v}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--file", default=str(ROOT / "samples" / "public_cases.json"))
    ap.add_argument("--timeout", type=float, default=30)
    args = ap.parse_args()

    cases = json.loads(Path(args.file).read_text(encoding="utf-8"))["cases"]
    base = args.url.rstrip("/")
    latencies, failures = [], 0

    with httpx.Client(timeout=args.timeout) as http:
        h = http.get(f"{base}/health")
        print(f"GET /health -> {h.status_code} {h.text}")
        print(f"{'case':<11}{'interp':<8}{'plan':<7}{'cost':>11}{'ref':>11}{'secs':>7}  note")
        for case in cases:
            t0 = time.perf_counter()
            r = http.post(f"{base}/optimize-energy", json=case["input"])
            dt = time.perf_counter() - t0
            latencies.append(dt)
            exp = case["expected_output"]
            if r.status_code != 200:
                failures += 1
                print(f"{case['id']:<11}HTTP {r.status_code}: {r.text[:120]}")
                continue
            body = r.json()
            ok_i, why = _same_interp(body.get("directive_interpretation", []), exp["directive_interpretation"])
            req = OptimizeRequest.model_validate(case["input"])
            lim = build_limits(req, _truth(exp["directive_interpretation"]))
            problems = replay(lim, body["hourly_plan"], body)
            if body.get("scenario_id") != case["input"]["scenario_id"]:
                problems.append("scenario_id not echoed")
            ok_p = not problems
            ok_c = ok_p and body["total_cost_bdt"] <= exp["total_cost_bdt"] + TOL
            failures += not (ok_i and ok_p and ok_c)
            note = why or (problems[0] if problems else "")
            print(f"{case['id']:<11}{'ok' if ok_i else 'FAIL':<8}{'ok' if ok_p else 'FAIL':<7}"
                  f"{body['total_cost_bdt']:>11.2f}{exp['total_cost_bdt']:>11.2f}{dt:>7.2f}  {note}")

    lat = sorted(latencies)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * len(lat))) - 1)]
    print(f"\n{len(cases) - failures}/{len(cases)} cases passed | "
          f"latency p50={statistics.median(lat):.2f}s p95={p95:.2f}s max={lat[-1]:.2f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
