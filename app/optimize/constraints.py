"""Turns validated directives into per-hour bounds.

The same object is used by the solver and by the replay validator, so both always
agree on what the effective limits are.
"""
from dataclasses import dataclass
from typing import List, Optional

from app.schemas.directives import (MAX_GRID, MIN_RESERVE, NO_CHARGE, NO_DISCHARGE,
                                    SOLAR_REDUCTION, Directive)
from app.schemas.request import OptimizeRequest

H = 24


@dataclass
class HourlyLimits:
    demand: List[float]
    tariff: List[float]
    eff_solar: List[float]
    e_min: List[float]
    charge_max: List[float]
    discharge_max: List[float]
    grid_max: List[Optional[float]]
    capacity: float
    initial_energy: float
    base_minimum: float
    charge_rate: float
    discharge_rate: float


def build_limits(req: OptimizeRequest, directives: List[Directive]) -> HourlyLimits:
    b = req.battery
    demand = [h.demand_kwh for h in req.hours]
    tariff = [h.tariff_bdt_per_kwh for h in req.hours]
    solar_factor = [1.0] * H
    e_min = [b.minimum_energy_kwh] * H
    charge_max = [b.max_charge_kwh_per_hour] * H
    discharge_max = [b.max_discharge_kwh_per_hour] * H
    grid_max: List[Optional[float]] = [None] * H

    for d in directives:
        for h in d.hours:
            if d.directive_type == SOLAR_REDUCTION:
                # Several reductions on one hour: multiply (strictest reading, always safe).
                solar_factor[h] *= d.value
            elif d.directive_type == MIN_RESERVE:
                e_min[h] = max(e_min[h], d.value)
            elif d.directive_type == NO_CHARGE:
                charge_max[h] = 0.0
            elif d.directive_type == NO_DISCHARGE:
                discharge_max[h] = 0.0
            elif d.directive_type == MAX_GRID:
                grid_max[h] = d.value if grid_max[h] is None else min(grid_max[h], d.value)

    eff_solar = [req.hours[h].solar_kwh * solar_factor[h] for h in range(H)]
    return HourlyLimits(
        demand=demand, tariff=tariff, eff_solar=eff_solar, e_min=e_min,
        charge_max=charge_max, discharge_max=discharge_max, grid_max=grid_max,
        capacity=b.capacity_kwh, initial_energy=b.initial_energy_kwh,
        base_minimum=b.minimum_energy_kwh,
        charge_rate=b.max_charge_kwh_per_hour,
        discharge_rate=b.max_discharge_kwh_per_hour,
    )
