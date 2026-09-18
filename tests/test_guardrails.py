import math

import pytest

from app.interpret.guardrails import check
from app.schemas.directives import Directive


def test_valid_directives_pass():
    assert check(Directive(0, "solar_reduction", [12, 13], 0.25), 1, 200) == []
    assert check(Directive(0, "minimum_battery_reserve", [18], 200), 1, 200) == []
    assert check(Directive(0, "no_charge_window", [2, 3, 4]), 1, 200) == []
    assert check(Directive(0, "max_grid_window", [18], 0), 1, 200) == []
    assert check(Directive(0, "no_op"), 1, 200) == []


@pytest.mark.parametrize("d", [
    Directive(0, "solar_reduction", [12], 1.5),
    Directive(0, "solar_reduction", [12], -0.1),
    Directive(0, "solar_reduction", [12], math.nan),
    Directive(0, "minimum_battery_reserve", [18], 250),
    Directive(0, "minimum_battery_reserve", [18], -5),
    Directive(0, "max_grid_window", [18], -1),
    Directive(0, "max_grid_window", [18], math.inf),
    Directive(0, "no_charge_window", []),
    Directive(0, "no_charge_window", [24]),
    Directive(0, "no_charge_window", [3, 2]),
    Directive(0, "no_charge_window", [2, 2]),
    Directive(0, "no_charge_window", [2], 5),
    Directive(0, "invent_new_rule", [2]),
    Directive(3, "no_charge_window", [2]),
    Directive(0, "no_op", [1, 2]),
])
def test_bad_directives_rejected(d):
    assert check(d, 1, 200)
