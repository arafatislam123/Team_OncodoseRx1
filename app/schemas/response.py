"""Response models. Used to make sure the returned JSON always has the exact contract."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

DirectiveType = Literal["solar_reduction", "minimum_battery_reserve", "no_charge_window",
                        "no_discharge_window", "max_grid_window", "no_op"]


class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[Dict[str, Any]]
    explanation: str


class HourPlan(BaseModel):
    hour: int = Field(ge=0, le=23)
    grid_kwh: float = Field(ge=0)
    solar_used_kwh: float = Field(ge=0)
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float = Field(ge=0)
    battery_energy_after_kwh: float = Field(ge=0)


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourPlan] = Field(min_length=24, max_length=24)
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
