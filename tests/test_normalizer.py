import pytest

from app.interpret.normalizer import expand_windows, to_directive


@pytest.mark.parametrize("windows,expected", [
    ([{"start_hour": 13, "end_hour": 15}], [13, 14]),
    ([{"start_hour": 18, "end_hour": 22}], [18, 19, 20, 21]),
    ([{"start_hour": 19, "end_hour": 20}], [19]),
    ([{"start_hour": 20, "end_hour": 24}], [20, 21, 22, 23]),
    ([{"start_hour": 0, "end_hour": 6}], [0, 1, 2, 3, 4, 5]),
    ([{"start_hour": 22, "end_hour": 2}], [0, 1, 22, 23]),
    ([{"start_hour": 10, "end_hour": 12}, {"start_hour": 11, "end_hour": 13}], [10, 11, 12]),
    ([{"start_hour": 7.0, "end_hour": 9}], [7, 8]),
    ([], list(range(24))),
])
def test_expand_windows(windows, expected):
    hours, errs = expand_windows(windows)
    assert errs == []
    assert hours == expected


@pytest.mark.parametrize("windows", [
    [{"start_hour": 25, "end_hour": 26}],
    [{"start_hour": "six", "end_hour": 9}],
    [{"start_hour": 1.5, "end_hour": 3}],
    "18-20",
])
def test_bad_windows_rejected(windows):
    _, errs = expand_windows(windows)
    assert errs


@pytest.mark.parametrize("value,factor", [
    ({"amount": 20, "unit": "percent_remaining"}, 0.2),
    ({"amount": 80, "unit": "percent_reduction"}, 0.2),
    ({"amount": 0.25, "unit": "fraction_remaining"}, 0.25),
    ({"amount": 50, "unit": "percent_remaining"}, 0.5),
    ({"amount": 20, "unit": "percent_reduction"}, 0.8),
    ({"amount": 0, "unit": "percent_remaining"}, 0.0),
])
def test_solar_units(value, factor):
    d, errs = to_directive(0, {"relevant": True, "directive_type": "solar_reduction",
                               "windows": [{"start_hour": 13, "end_hour": 15}], "value": value}, 200)
    assert errs == []
    assert d.value == pytest.approx(factor)
    assert d.structured_adjustment() == {"hours": [13, 14], "factor": pytest.approx(factor)}


def test_percent_reserve_uses_capacity():
    d, errs = to_directive(0, {"relevant": True, "directive_type": "minimum_battery_reserve",
                               "windows": [{"start_hour": 18, "end_hour": 21}],
                               "value": {"amount": 50, "unit": "percent_of_capacity"}}, 200)
    assert errs == []
    assert d.structured_adjustment() == {"hours": [18, 19, 20], "minimum_energy_kwh": 100}


def test_no_op_has_null_adjustment():
    d, errs = to_directive(1, {"relevant": False, "directive_type": "no_op", "windows": [], "value": None,
                               "explanation": "unrelated"}, 200)
    assert errs == []
    assert d.to_response() == {"note_index": 1, "applies": False, "directive_type": "no_op",
                               "structured_adjustment": None, "explanation": "unrelated"}


def test_relevant_false_forces_no_op():
    d, _ = to_directive(0, {"relevant": False, "directive_type": "no_charge_window",
                            "windows": [{"start_hour": 1, "end_hour": 2}]}, 200)
    assert d.directive_type == "no_op"


def test_unknown_type_rejected():
    d, errs = to_directive(0, {"relevant": True, "directive_type": "shed_load", "windows": []}, 200)
    assert d is None and errs


def test_missing_value_rejected():
    d, errs = to_directive(0, {"relevant": True, "directive_type": "max_grid_window",
                               "windows": [{"start_hour": 18, "end_hour": 20}], "value": None}, 200)
    assert d is None and errs
