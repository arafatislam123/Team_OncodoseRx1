"""Directive types and the validated directive object passed to the optimizer."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SOLAR_REDUCTION = "solar_reduction"
MIN_RESERVE = "minimum_battery_reserve"
NO_CHARGE = "no_charge_window"
NO_DISCHARGE = "no_discharge_window"
MAX_GRID = "max_grid_window"
NO_OP = "no_op"

DIRECTIVE_TYPES = (SOLAR_REDUCTION, MIN_RESERVE, NO_CHARGE, NO_DISCHARGE, MAX_GRID, NO_OP)

# Numeric key required in structured_adjustment for each type (None = only "hours").
VALUE_KEY = {
    SOLAR_REDUCTION: "factor",
    MIN_RESERVE: "minimum_energy_kwh",
    NO_CHARGE: None,
    NO_DISCHARGE: None,
    MAX_GRID: "max_grid_kwh",
}


@dataclass
class Directive:
    note_index: int
    directive_type: str
    hours: List[int] = field(default_factory=list)
    value: Optional[float] = None
    explanation: str = ""
    source: str = "llm"  # llm | cache | degraded | none

    @property
    def applies(self) -> bool:
        return self.directive_type != NO_OP

    def structured_adjustment(self) -> Optional[Dict[str, Any]]:
        if self.directive_type == NO_OP:
            return None
        adj: Dict[str, Any] = {"hours": list(self.hours)}
        key = VALUE_KEY[self.directive_type]
        if key is not None:
            adj[key] = self.value
        return adj

    def to_response(self) -> Dict[str, Any]:
        return {
            "note_index": self.note_index,
            "applies": self.applies,
            "directive_type": self.directive_type,
            "structured_adjustment": self.structured_adjustment(),
            "explanation": self.explanation,
        }


def no_op(note_index: int, explanation: str, source: str = "llm") -> Directive:
    return Directive(note_index=note_index, directive_type=NO_OP, explanation=explanation, source=source)
