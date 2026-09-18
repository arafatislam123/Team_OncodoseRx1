"""Quick offline check: feed each public sample its expected directives (no LLM),
solve, replay, and compare cost with the reference optimum."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.optimize.constraints import build_limits  # noqa: E402
from app.optimize.plan_builder import build_plan, totals  # noqa: E402
from app.optimize.solver import solve  # noqa: E402
from app.schemas.directives import Directive  # noqa: E402
from app.schemas.request import OptimizeRequest  # noqa: E402
from app.validate.replay import replay  # noqa: E402


def directives_from_expected(expected):
    out = []
    for e in expected:
        adj = e["structured_adjustment"] or {}
        value = next((v for k, v in adj.items() if k != "hours"), None)
        out.append(Directive(note_index=e["note_index"], directive_type=e["directive_type"],
                             hours=adj.get("hours", []), value=value))
    return out


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    bad = 0
    for case in data["cases"]:
        req = OptimizeRequest.model_validate(case["input"])
        dirs = directives_from_expected(case["expected_output"]["directive_interpretation"])
        lim = build_limits(req, dirs)
        sol = solve(lim)
        rows = build_plan(lim, sol)
        tot = totals(rows, lim.tariff)
        errs = replay(lim, rows, tot)
        ref = case["expected_output"]["total_cost_bdt"]
        ok = not errs and tot["total_cost_bdt"] <= ref + 0.01
        bad += not ok
        print(f"{case['id']:<10} cost={tot['total_cost_bdt']:>10.2f} ref={ref:>10.2f} "
              f"replay={'ok' if not errs else errs[:2]} {'PASS' if ok else 'FAIL'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "samples" / "public_cases.json")))
