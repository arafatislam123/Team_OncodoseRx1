"""Request models.

Structural problems (missing fields, wrong types, wrong sizes) are reported as 400.
Well-formed but impossible values (negative demand, initial energy above capacity...)
are reported as 422 through `semantic_errors`.
"""
from typing import Annotated, List

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pydantic.types import Strict

# Strict float: accepts JSON numbers (int or float), rejects strings, booleans, NaN and Infinity.
Number = Annotated[float, Strict(), Field(allow_inf_nan=False)]

MAX_NOTE_CHARS = 1000


class HourInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hour: StrictInt
    demand_kwh: Number
    solar_kwh: Number
    tariff_bdt_per_kwh: Number


class BatteryInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capacity_kwh: Number
    initial_energy_kwh: Number
    minimum_energy_kwh: Number
    max_charge_kwh_per_hour: Number
    max_discharge_kwh_per_hour: Number


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scenario_id: Annotated[str, Strict()]
    operator_notes: List[Annotated[str, Strict()]] = Field(min_length=1, max_length=3)
    hours: List[HourInput] = Field(min_length=24, max_length=24)
    battery: BatteryInput

    @field_validator("operator_notes")
    @classmethod
    def _notes_not_empty(cls, notes: List[str]) -> List[str]:
        cleaned = []
        for i, note in enumerate(notes):
            text = note.strip()
            if not text:
                raise ValueError(f"operator_notes[{i}] must be a non-empty string")
            if len(text) > MAX_NOTE_CHARS:
                raise ValueError(f"operator_notes[{i}] is longer than {MAX_NOTE_CHARS} characters")
            cleaned.append(text)
        return cleaned

    @model_validator(mode="after")
    def _hours_cover_day(self) -> "OptimizeRequest":
        seen = sorted(h.hour for h in self.hours)
        if seen != list(range(24)):
            raise ValueError("hours must contain each hour 0..23 exactly once")
        # Work with hours in order regardless of how they were sent.
        self.hours = sorted(self.hours, key=lambda h: h.hour)
        return self


def semantic_errors(req: OptimizeRequest) -> List[str]:
    """Checks that make a well-formed request impossible to schedule (-> 422)."""
    errors: List[str] = []
    for h in req.hours:
        for field in ("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh"):
            if getattr(h, field) < 0:
                errors.append(f"hours[{h.hour}].{field} must be >= 0")
    b = req.battery
    for field in ("capacity_kwh", "initial_energy_kwh", "minimum_energy_kwh",
                  "max_charge_kwh_per_hour", "max_discharge_kwh_per_hour"):
        if getattr(b, field) < 0:
            errors.append(f"battery.{field} must be >= 0")
    if b.minimum_energy_kwh > b.capacity_kwh:
        errors.append("battery.minimum_energy_kwh must not exceed capacity_kwh")
    if not (b.minimum_energy_kwh - 1e-9 <= b.initial_energy_kwh <= b.capacity_kwh + 1e-9):
        errors.append("battery.initial_energy_kwh must be between minimum_energy_kwh and capacity_kwh")
    return errors
