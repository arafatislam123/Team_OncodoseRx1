"""Final deterministic checks on a directive before it may reach the optimizer
(Problem Statement section 8). Returns a list of problems; empty means accepted."""
import math
from typing import List

from app.schemas.directives import (DIRECTIVE_TYPES, MAX_GRID, MIN_RESERVE, NO_OP,
                                    SOLAR_REDUCTION, VALUE_KEY, Directive)


def check(d: Directive, n_notes: int, capacity: float) -> List[str]:
    p = f"note {d.note_index}"
    errs: List[str] = []
    if not isinstance(d.note_index, int) or not 0 <= d.note_index < n_notes:
        errs.append(f"{p}: note_index out of range")
    if d.directive_type not in DIRECTIVE_TYPES:
        return errs + [f"{p}: unsupported directive_type {d.directive_type!r}"]

    if d.directive_type == NO_OP:
        if d.hours or d.value is not None:
            errs.append(f"{p}: no_op must not carry an adjustment")
        return errs

    hours = d.hours
    if not hours:
        errs.append(f"{p}: hours must not be empty")
    if any(not isinstance(h, int) or isinstance(h, bool) or not 0 <= h <= 23 for h in hours):
        errs.append(f"{p}: hours must be integers 0-23")
    if hours != sorted(set(hours)):
        errs.append(f"{p}: hours must be unique and ascending")

    key = VALUE_KEY[d.directive_type]
    if key is None:
        if d.value is not None:
            errs.append(f"{p}: {d.directive_type} takes no numeric value")
        return errs

    v = d.value
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        return errs + [f"{p}: {key} must be a finite number"]
    if d.directive_type == SOLAR_REDUCTION and not 0.0 <= v <= 1.0:
        errs.append(f"{p}: factor must be between 0 and 1 (got {v})")
    if d.directive_type == MIN_RESERVE and not 0.0 <= v <= capacity + 1e-9:
        errs.append(f"{p}: minimum_energy_kwh must be between 0 and battery capacity {capacity} (got {v})")
    if d.directive_type == MAX_GRID and v < 0:
        errs.append(f"{p}: max_grid_kwh must be non-negative (got {v})")
    return errs
