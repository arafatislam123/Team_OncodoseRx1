import pytest

from app.optimize.constraints import build_limits
from app.optimize.plan_builder import build_plan, totals
from app.optimize.solver import solve
from app.schemas.directives import Directive
from app.schemas.request import OptimizeRequest
from app.validate.replay import replay
from tests.conftest import SAMPLES


def _directives(expected):
    out = []
    for e in expected:
        adj = e["structured_adjustment"] or {}
        value = next((v for k, v in adj.items() if k != "hours"), None)
        out.append(Directive(e["note_index"], e["directive_type"], adj.get("hours", []), value))
    return out


@pytest.mark.parametrize("case", SAMPLES, ids=[c["id"] for c in SAMPLES])
def test_sample_reaches_reference_optimum(case):
    req = OptimizeRequest.model_validate(case["input"])
    lim = build_limits(req, _directives(case["expected_output"]["directive_interpretation"]))
    sol = solve(lim)
    rows = build_plan(lim, sol)
    tot = totals(rows, lim.tariff)
    assert not sol.relaxed
    assert replay(lim, rows, tot) == []
    assert tot["total_cost_bdt"] == pytest.approx(case["expected_output"]["total_cost_bdt"], abs=0.01)


def test_conflicting_directives_fall_back_to_relaxed_plan():
    case = SAMPLES[4]  # feeder cap case
    req = OptimizeRequest.model_validate(case["input"])
    # A 0 kWh grid cap all evening plus a ban on discharging cannot be met.
    dirs = [Directive(0, "max_grid_window", [18, 19, 20], 0.0), Directive(1, "no_discharge_window", [18, 19, 20])]
    lim = build_limits(req, dirs)
    sol = solve(lim)
    assert sol.relaxed and sol.violated
    rows = build_plan(lim, sol)
    # physics still holds even though a directive could not be met
    energy = lim.initial_energy
    for r in rows:
        c = r["battery_kwh"] if r["battery_action"] == "charge" else 0
        d = r["battery_kwh"] if r["battery_action"] == "discharge" else 0
        assert r["grid_kwh"] + r["solar_used_kwh"] + d == pytest.approx(lim.demand[r["hour"]] + c, abs=0.01)
        energy += c - d
        assert r["battery_energy_after_kwh"] == pytest.approx(energy, abs=0.01)
    assert rows[-1]["battery_energy_after_kwh"] == pytest.approx(lim.initial_energy, abs=0.01)


def test_zero_rate_battery_stays_idle():
    case = SAMPLES[0]
    data = {**case["input"], "battery": {**case["input"]["battery"], "max_charge_kwh_per_hour": 0,
                                         "max_discharge_kwh_per_hour": 0}}
    req = OptimizeRequest.model_validate(data)
    lim = build_limits(req, [])
    rows = build_plan(lim, solve(lim))
    assert all(r["battery_action"] == "idle" for r in rows)
    assert replay(lim, rows) == []
