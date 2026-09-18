"""Turns the raw LP solution into clean hourly rows and totals.

- charge and discharge in the same hour are netted into one action (no losses, so
  grid is unchanged),
- flows are rounded, then battery energy and grid are recomputed from the rounded
  values so balance and transitions hold exactly,
- totals are computed from the final rows only.
"""
from typing import Any, Dict, List

from app.optimize.constraints import H, HourlyLimits
from app.optimize.solver import Solution

DECIMALS = 4
ZERO = 1e-6


def _r(x: float, decimals: int = DECIMALS) -> float:
    v = round(x, decimals)
    return 0.0 if v == 0 else v  # avoid -0.0


def build_plan(lim: HourlyLimits, sol: Solution, decimals: int = DECIMALS) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    energy = lim.initial_energy
    for h in range(H):
        net = sol.charge[h] - sol.discharge[h]
        amount = _r(abs(net), decimals) if abs(net) > ZERO else 0.0
        if amount == 0.0:
            action = "idle"
        else:
            action = "charge" if net > 0 else "discharge"

        charge = amount if action == "charge" else 0.0
        discharge = amount if action == "discharge" else 0.0
        solar = _r(min(max(sol.solar[h], 0.0), lim.eff_solar[h]), decimals)
        grid = lim.demand[h] + charge - solar - discharge
        if grid < 0:
            # Tiny negative from rounding: use a bit less solar instead.
            solar = _r(max(0.0, solar + grid), decimals)
            grid = lim.demand[h] + charge - solar - discharge
        grid = _r(max(grid, 0.0), decimals)

        energy_out = _r(energy + charge - discharge, decimals)
        energy = energy_out
        rows.append({
            "hour": h,
            "grid_kwh": grid,
            "solar_used_kwh": solar,
            "battery_action": action,
            "battery_kwh": amount,
            "battery_energy_after_kwh": energy_out,
        })
    return rows


def totals(rows: List[Dict[str, Any]], tariff: List[float]) -> Dict[str, float]:
    total_grid = sum(r["grid_kwh"] for r in rows)
    total_cost = sum(r["grid_kwh"] * tariff[r["hour"]] for r in rows)
    peak = max(r["grid_kwh"] for r in rows)
    return {
        "total_grid_kwh": _r(total_grid),
        "total_cost_bdt": _r(total_cost),
        "peak_grid_kwh": _r(peak),
    }


def _hours_text(hours: List[int]) -> str:
    if not hours:
        return "none"
    spans, start, prev = [], hours[0], hours[0]
    for h in hours[1:] + [None]:
        if h is not None and h == prev + 1:
            prev = h
            continue
        spans.append(f"{start:02d}:00-{prev + 1:02d}:00")
        if h is not None:
            start = prev = h
    return ", ".join(spans)


def summary(rows: List[Dict[str, Any]], directives, sol: Solution, total_cost: float) -> str:
    applied = [d for d in directives if d.applies]
    ignored = [d for d in directives if not d.applies]
    charge_hours = [r["hour"] for r in rows if r["battery_action"] == "charge"]
    discharge_hours = [r["hour"] for r in rows if r["battery_action"] == "discharge"]
    parts = []
    if applied:
        names = ", ".join(sorted({d.directive_type for d in applied}))
        parts.append(f"Applied {len(applied)} operator directive(s) ({names}).")
    if ignored:
        parts.append(f"Ignored {len(ignored)} note(s) that do not affect today's schedule.")
    parts.append(f"Battery charges during {_hours_text(charge_hours)} and discharges during "
                 f"{_hours_text(discharge_hours)} to shift energy toward higher-tariff hours, "
                 f"returning to its initial level at the end of the day.")
    parts.append(f"Total grid cost {total_cost:.2f} BDT.")
    if sol.relaxed:
        parts.append("Warning: the directives could not all be met together; the plan minimises the "
                     f"violation of: {', '.join(sol.violated) or 'none'}.")
    return " ".join(parts)
