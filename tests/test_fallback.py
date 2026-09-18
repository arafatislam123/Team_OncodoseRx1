"""The degraded extractor only runs during provider outages, but when it runs it
should handle the plain phrasings correctly and prefer no_op over guessing."""
import pytest

from app.interpret.fallback import extract
from app.interpret.normalizer import to_directive
from tests.conftest import SAMPLES


def _directive(text, capacity=200):
    d, errs = to_directive(0, extract(text), capacity)
    assert errs == [], errs
    return d


@pytest.mark.parametrize("case", SAMPLES, ids=[c["id"] for c in SAMPLES])
def test_public_samples(case):
    cap = case["input"]["battery"]["capacity_kwh"]
    for note, exp in zip(case["input"]["operator_notes"], case["expected_output"]["directive_interpretation"]):
        d = _directive(note, cap)
        assert d.directive_type == exp["directive_type"], note
        assert d.structured_adjustment() == exp["structured_adjustment"], note


@pytest.mark.parametrize("text,dtype,adj", [
    ("PV production will drop to about 20% between 13:00 and 15:00.", "solar_reduction", {"hours": [13, 14], "factor": 0.2}),
    ("Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.", "solar_reduction", {"hours": [13, 14], "factor": 0.2}),
    ("Do not charge the battery between 2 PM and 4 PM.", "no_charge_window", {"hours": [14, 15]}),
    ("Keep at least 120 kWh in reserve from 6 PM until 9 PM.", "minimum_battery_reserve", {"hours": [18, 19, 20], "minimum_energy_kwh": 120}),
    ("The cafeteria menu changes tomorrow.", "no_op", None),
    ("Solar panels will be cleaned next week.", "no_op", None),
])
def test_problem_statement_examples(text, dtype, adj):
    d = _directive(text)
    assert d.directive_type == dtype
    assert d.structured_adjustment() == adj
