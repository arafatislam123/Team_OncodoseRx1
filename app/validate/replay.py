"""Independent replay of a finished plan, written the way the judge checks it.

Returns a list of human-readable violations (empty list = valid plan).
"""
import math
from typing import Any, Dict, List

from app.optimize.constraints import H, HourlyLimits

TOL = 0.01


def replay(lim: HourlyLimits, rows: List[Dict[str, Any]], totals: Dict[str, float] = None) -> List[str]:
    errs: List[str] = []
    if len(rows) != H or sorted(r.get("hour") for r in rows) != list(range(H)):
        return ["hourly_plan must contain hours 0..23 exactly once"]
    rows = sorted(rows, key=lambda r: r["hour"])

    energy = lim.initial_energy
    for r in rows:
        h = r["hour"]
        g, s, a, k, e = (r["grid_kwh"], r["solar_used_kwh"], r["battery_action"],
                         r["battery_kwh"], r["battery_energy_after_kwh"])
        for name, v in (("grid_kwh", g), ("solar_used_kwh", s), ("battery_kwh", k),
                        ("battery_energy_after_kwh", e)):
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < -TOL:
                errs.append(f"h{h}: {name} must be a finite non-negative number")
        if a not in ("charge", "discharge", "idle"):
            errs.append(f"h{h}: invalid battery_action {a!r}")
            continue
        charge = k if a == "charge" else 0.0
        discharge = k if a == "discharge" else 0.0
        if a == "idle" and abs(k) > TOL:
            errs.append(f"h{h}: idle hour must have battery_kwh = 0")
        if a == "charge" and k > lim.charge_max[h] + TOL:
            errs.append(f"h{h}: charge {k} exceeds limit {lim.charge_max[h]}")
        if a == "discharge" and k > lim.discharge_max[h] + TOL:
            errs.append(f"h{h}: discharge {k} exceeds limit {lim.discharge_max[h]}")
        if s > lim.eff_solar[h] + TOL:
            errs.append(f"h{h}: solar used {s} exceeds effective solar {lim.eff_solar[h]}")
        if abs(g + s + discharge - lim.demand[h] - charge) > TOL:
            errs.append(f"h{h}: energy balance off")
        if lim.grid_max[h] is not None and g > lim.grid_max[h] + TOL:
            errs.append(f"h{h}: grid {g} exceeds cap {lim.grid_max[h]}")
        energy = energy + charge - discharge
        if abs(energy - e) > TOL:
            errs.append(f"h{h}: battery_energy_after_kwh {e} does not match transition {energy:.4f}")
        if e < lim.e_min[h] - TOL:
            errs.append(f"h{h}: battery energy {e} below minimum {lim.e_min[h]}")
        if e > lim.capacity + TOL:
            errs.append(f"h{h}: battery energy {e} above capacity {lim.capacity}")
        energy = e

    if abs(rows[-1]["battery_energy_after_kwh"] - lim.initial_energy) > TOL:
        errs.append("final battery energy does not equal initial energy")

    if totals is not None:
        grid_sum = sum(r["grid_kwh"] for r in rows)
        cost = sum(r["grid_kwh"] * lim.tariff[r["hour"]] for r in rows)
        peak = max(r["grid_kwh"] for r in rows)
        if abs(grid_sum - totals["total_grid_kwh"]) > TOL:
            errs.append("total_grid_kwh does not match hourly_plan")
        if abs(cost - totals["total_cost_bdt"]) > TOL:
            errs.append("total_cost_bdt does not match hourly_plan")
        if abs(peak - totals["peak_grid_kwh"]) > TOL:
            errs.append("peak_grid_kwh does not match hourly_plan")
    return errs
