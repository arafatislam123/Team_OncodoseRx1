"""Pipeline for one /optimize-energy request:
interpret notes -> build limits -> solve LP -> build plan -> replay -> response."""
import logging
import time
from typing import Any, Dict

from starlette.concurrency import run_in_threadpool

from app.interpret.interpreter import Interpreter
from app.optimize.constraints import build_limits
from app.optimize.plan_builder import build_plan, summary, totals
from app.optimize.solver import solve
from app.schemas.request import OptimizeRequest
from app.schemas.response import OptimizeResponse
from app.validate.replay import replay

log = logging.getLogger("gridwise.service")


def _schedule(req: OptimizeRequest, directives):
    lim = build_limits(req, directives)
    sol = solve(lim)
    rows = build_plan(lim, sol)
    tot = totals(rows, lim.tariff)
    problems = replay(lim, rows, tot)
    if problems and not sol.relaxed:
        # Should never happen (the LP respects every limit); try again with finer rounding.
        log.error("replay found %d problem(s), rebuilding at higher precision: %s", len(problems), problems[:3])
        rows = build_plan(lim, sol, decimals=6)
        tot = totals(rows, lim.tariff)
        still = replay(lim, rows, tot)
        if still:
            log.error("replay still reports %d problem(s): %s", len(still), still[:3])
    return rows, tot, sol


async def optimize(req: OptimizeRequest, interpreter: Interpreter, deadline_s: float) -> Dict[str, Any]:
    started = time.monotonic()
    directives = await interpreter.interpret(req.operator_notes, req.battery.capacity_kwh,
                                             deadline=started + deadline_s)
    rows, tot, sol = await run_in_threadpool(_schedule, req, directives)

    response = {
        "scenario_id": req.scenario_id,
        "directive_interpretation": [d.to_response() for d in directives],
        "hourly_plan": rows,
        **tot,
        "plan_summary": summary(rows, directives, sol, tot["total_cost_bdt"]),
    }
    OptimizeResponse.model_validate(response)  # contract check, raises on drift
    log.info("scenario=%s notes=%d sources=%s relaxed=%s cost=%.2f took=%.2fs",
             req.scenario_id[:64], len(directives), [d.source for d in directives], sol.relaxed,
             tot["total_cost_bdt"], time.monotonic() - started)
    return response
