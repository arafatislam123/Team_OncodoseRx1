import copy

from app.optimize.constraints import build_limits
from app.optimize.plan_builder import build_plan, totals
from app.optimize.solver import solve
from app.schemas.directives import Directive
from app.schemas.request import OptimizeRequest
from app.validate.replay import replay
from tests.conftest import SAMPLES


def _base():
    req = OptimizeRequest.model_validate(SAMPLES[6]["input"])  # reserve + grid cap case
    dirs = [Directive(0, "minimum_battery_reserve", [18, 19, 20, 21], 90),
            Directive(1, "max_grid_window", [19, 20], 180)]
    lim = build_limits(req, dirs)
    rows = build_plan(lim, solve(lim))
    return lim, rows, totals(rows, lim.tariff)


def test_clean_plan_passes():
    lim, rows, tot = _base()
    assert replay(lim, rows, tot) == []


def test_detects_each_kind_of_breakage():
    lim, rows, tot = _base()

    broken = copy.deepcopy(rows)
    broken[5]["grid_kwh"] += 10
    assert any("balance" in e for e in replay(lim, broken))

    broken = copy.deepcopy(rows)
    broken[19]["grid_kwh"] = 500
    assert any("cap" in e for e in replay(lim, broken))

    broken = copy.deepcopy(rows)
    broken[23]["battery_energy_after_kwh"] -= 5
    assert replay(lim, broken)

    broken = copy.deepcopy(rows)
    broken[0]["battery_action"] = "idle"
    assert replay(lim, broken)

    broken = copy.deepcopy(rows)
    del broken[3]
    assert replay(lim, broken)

    bad_tot = dict(tot, total_cost_bdt=tot["total_cost_bdt"] + 1)
    assert any("total_cost_bdt" in e for e in replay(lim, rows, bad_tot))
