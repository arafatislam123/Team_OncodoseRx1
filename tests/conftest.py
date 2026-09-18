import json
from pathlib import Path

import pytest

from app.config import ProviderConfig, Settings
from app.interpret.cache import IntentCache
from app.interpret.interpreter import Interpreter
from app.interpret.llm_client import LLMError

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = json.loads((ROOT / "samples" / "public_cases.json").read_text(encoding="utf-8"))["cases"]


def expected_to_intent(e: dict) -> dict:
    """Express an expected directive in the model's intent format."""
    t = e["directive_type"]
    if t == "no_op":
        return {"relevant": False, "directive_type": "no_op", "windows": [], "value": None,
                "explanation": e["explanation"]}
    adj = e["structured_adjustment"]
    value = None
    if t == "solar_reduction":
        value = {"amount": adj["factor"], "unit": "fraction_remaining"}
    elif t == "minimum_battery_reserve":
        value = {"amount": adj["minimum_energy_kwh"], "unit": "kwh"}
    elif t == "max_grid_window":
        value = {"amount": adj["max_grid_kwh"], "unit": "kwh"}
    return {"relevant": True, "directive_type": t, "windows": [], "hours": adj["hours"],
            "value": value, "explanation": e["explanation"]}


def sample_intents() -> dict:
    out = {}
    for case in SAMPLES:
        for note, e in zip(case["input"]["operator_notes"], case["expected_output"]["directive_interpretation"]):
            out[note] = expected_to_intent(e)
    return out


class FakeLLM:
    """Answers like a well-behaved model using a note-text -> intent table.
    mode: "ok" | "down" | "garbage" | "bad_then_ok"."""

    def __init__(self, table: dict, mode: str = "ok"):
        self.table = table
        self.mode = mode
        self.calls = 0

    async def chat(self, provider, messages, timeout):
        self.calls += 1
        if self.mode == "down":
            raise LLMError("fake provider down")
        if self.mode == "garbage":
            return "Sorry, I can't help with that."
        notes = json.loads(messages[3]["content"])["notes"]
        items = []
        for n in notes:
            intent = dict(self.table.get(n["text"], {"relevant": False, "directive_type": "no_op",
                                                     "windows": [], "value": None, "explanation": "n/a"}))
            if self.mode == "bad_then_ok" and self.calls == 1 and intent["directive_type"] != "no_op":
                intent["directive_type"] = "reduce_everything"  # unsupported type, must be repaired
            items.append({"note_index": n["note_index"], **intent})
        return json.dumps({"notes": items})


def make_settings(primary=True, backup=False, degraded=True) -> Settings:
    p = ProviderConfig("primary", "http://fake", "k", "fake-model") if primary else ProviderConfig("primary", "", "", "")
    b = ProviderConfig("backup", "http://fake2", "k", "fake-model-2") if backup else ProviderConfig("backup", "", "", "")
    return Settings(primary=p, backup=b, llm_timeout_s=5, request_deadline_s=20,
                    enable_degraded_fallback=degraded, cache_size=64, log_level="INFO")


def make_interpreter(mode="ok", **kw) -> Interpreter:
    return Interpreter(FakeLLM(sample_intents(), mode), make_settings(**kw), IntentCache(64))


@pytest.fixture
def samples():
    return SAMPLES
