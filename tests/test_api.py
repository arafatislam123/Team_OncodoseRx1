import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import SAMPLES, make_interpreter

VALID = SAMPLES[5]["input"]  # three notes incl. a distractor


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.interpreter = make_interpreter("ok")
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize("case", SAMPLES, ids=[c["id"] for c in SAMPLES])
def test_samples_end_to_end(client, case):
    r = client.post("/optimize-energy", json=case["input"])
    assert r.status_code == 200
    body = r.json()
    exp = case["expected_output"]
    assert body["scenario_id"] == case["input"]["scenario_id"]
    assert set(body) == {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh",
                         "total_cost_bdt", "peak_grid_kwh", "plan_summary"}
    got = [{k: v for k, v in d.items() if k != "explanation"} for d in body["directive_interpretation"]]
    want = [{k: v for k, v in d.items() if k != "explanation"} for d in exp["directive_interpretation"]]
    assert got == want
    assert body["total_cost_bdt"] == pytest.approx(exp["total_cost_bdt"], abs=0.01)
    assert [h["hour"] for h in body["hourly_plan"]] == list(range(24))


def test_repair_step_fixes_bad_model_output(client):
    app.state.interpreter = make_interpreter("bad_then_ok")
    r = client.post("/optimize-energy", json=VALID)
    assert r.status_code == 200
    types = [d["directive_type"] for d in r.json()["directive_interpretation"]]
    assert types == ["solar_reduction", "no_charge_window", "no_op"]
    assert app.state.interpreter.llm.calls == 2


@pytest.mark.parametrize("mode", ["down", "garbage"])
def test_provider_failure_uses_degraded_mode(client, mode):
    app.state.interpreter = make_interpreter(mode)
    r = client.post("/optimize-energy", json=VALID)
    assert r.status_code == 200
    types = [d["directive_type"] for d in r.json()["directive_interpretation"]]
    assert types == ["solar_reduction", "no_charge_window", "no_op"]


def test_provider_failure_without_fallback_returns_no_op(client):
    app.state.interpreter = make_interpreter("down", degraded=False)
    r = client.post("/optimize-energy", json=VALID)
    assert r.status_code == 200
    for d in r.json()["directive_interpretation"]:
        assert d["applies"] is False and d["structured_adjustment"] is None


def test_backup_provider_used_when_primary_fails(client):
    interp = make_interpreter("ok", backup=True)

    real_chat = interp.llm.chat

    async def flaky(provider, messages, timeout):
        if provider.name == "primary":
            from app.interpret.llm_client import LLMError
            raise LLMError("primary down")
        return await real_chat(provider, messages, timeout)

    interp.llm.chat = flaky
    app.state.interpreter = interp
    r = client.post("/optimize-energy", json=VALID)
    assert r.status_code == 200
    assert [d["directive_type"] for d in r.json()["directive_interpretation"]] == \
        ["solar_reduction", "no_charge_window", "no_op"]


def test_cache_avoids_second_model_call(client):
    client.post("/optimize-energy", json=VALID)
    calls = app.state.interpreter.llm.calls
    client.post("/optimize-energy", json=VALID)
    assert app.state.interpreter.llm.calls == calls


def _mutate(fn):
    data = copy.deepcopy(VALID)
    fn(data)
    return data


@pytest.mark.parametrize("payload", [
    _mutate(lambda d: d.pop("battery")),
    _mutate(lambda d: d.pop("scenario_id")),
    _mutate(lambda d: d.__setitem__("operator_notes", [])),
    _mutate(lambda d: d.__setitem__("operator_notes", ["a", "b", "c", "d"])),
    _mutate(lambda d: d.__setitem__("operator_notes", ["   "])),
    _mutate(lambda d: d.__setitem__("operator_notes", "just a string")),
    _mutate(lambda d: d["hours"].pop()),
    _mutate(lambda d: d["hours"][3].__setitem__("hour", 2)),
    _mutate(lambda d: d["hours"][3].__setitem__("demand_kwh", "80")),
    _mutate(lambda d: d["hours"][3].__setitem__("demand_kwh", True)),
    _mutate(lambda d: d["battery"].pop("capacity_kwh")),
    [1, 2, 3],
])
def test_structural_errors_are_400(client, payload):
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 400
    assert "error" in r.json()


@pytest.mark.parametrize("raw", ["{not json", "", '{"scenario_id": NaN}', "null"])
def test_malformed_json_is_400(client, raw):
    r = client.post("/optimize-energy", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 400


@pytest.mark.parametrize("payload", [
    _mutate(lambda d: d["hours"][3].__setitem__("demand_kwh", -5)),
    _mutate(lambda d: d["battery"].__setitem__("initial_energy_kwh", 999)),
    _mutate(lambda d: d["battery"].__setitem__("minimum_energy_kwh", 500)),
])
def test_semantic_errors_are_422(client, payload):
    assert client.post("/optimize-energy", json=payload).status_code == 422


def test_hours_in_any_order_are_accepted(client):
    data = _mutate(lambda d: d["hours"].reverse())
    r = client.post("/optimize-energy", json=data)
    assert r.status_code == 200
    assert r.json()["total_cost_bdt"] == pytest.approx(SAMPLES[5]["expected_output"]["total_cost_bdt"], abs=0.01)


def test_unknown_route_is_json_404(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json() == {"error": "not found"}


def test_no_secret_in_error_bodies(client):
    r = client.post("/optimize-energy", content="{bad", headers={"Content-Type": "application/json"})
    assert "Traceback" not in r.text and "api_key" not in json.dumps(r.json()).lower()
