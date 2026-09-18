"""Linear program for the 24-hour schedule, solved with HiGHS (scipy).

Variables per hour h: g grid, s solar used, c charge, d discharge, E energy after hour.

    min  sum tariff[h]*g[h] + EPS*sum(c[h]+d[h])
    s.t. g + s + d = demand + c                 (energy balance)
         E[h] = E[h-1] + c[h] - d[h]            (battery transition, E[-1] = E0)
         E[23] = E0                             (end-of-day neutrality)
         bounds from HourlyLimits

If the directives make the model infeasible (should not happen with a correct
interpretation), `solve` re-runs with directive limits softened by penalised slack,
keeping all physical rules hard.
"""
from dataclasses import dataclass, field
from typing import List

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

from app.optimize.constraints import H, HourlyLimits

EPS = 1e-6          # discourages useless charge/discharge cycling, far below 0.01 BDT
PENALTY = 1e6       # cost per kWh of directive violation in relaxed mode


class SolverError(RuntimeError):
    pass


@dataclass
class Solution:
    grid: List[float]
    solar: List[float]
    charge: List[float]
    discharge: List[float]
    relaxed: bool = False
    violated: List[str] = field(default_factory=list)


def _idx(block: int, h: int) -> int:
    return block * H + h


G, S, C, D, E = range(5)


def solve(lim: HourlyLimits) -> Solution:
    sol = _solve(lim, relaxed=False)
    if sol is not None:
        return sol
    sol = _solve(lim, relaxed=True)
    if sol is None:
        raise SolverError("optimizer could not find a schedule")
    return sol


def _solve(lim: HourlyLimits, relaxed: bool):
    n_core = 5 * H
    # Relaxed mode adds slack blocks: reserve shortfall, grid excess, forbidden charge, forbidden discharge.
    n = n_core + (4 * H if relaxed else 0)
    RS, GX, CX, DX = 5, 6, 7, 8

    cost = np.zeros(n)
    for h in range(H):
        cost[_idx(G, h)] = lim.tariff[h]
        cost[_idx(C, h)] = EPS
        cost[_idx(D, h)] = EPS
    if relaxed:
        cost[n_core:] = PENALTY

    # In relaxed mode the variable bounds are the physical ones; directive limits move to soft rows.
    bounds = []
    for h in range(H):  # grid
        cap = lim.grid_max[h]
        bounds.append((0.0, None if (cap is None or relaxed) else cap))
    for h in range(H):  # solar
        bounds.append((0.0, max(0.0, lim.eff_solar[h])))
    for h in range(H):  # charge
        bounds.append((0.0, max(0.0, lim.charge_rate if relaxed else lim.charge_max[h])))
    for h in range(H):  # discharge
        bounds.append((0.0, max(0.0, lim.discharge_rate if relaxed else lim.discharge_max[h])))
    for h in range(H):  # energy
        bounds.append((lim.base_minimum if relaxed else lim.e_min[h], lim.capacity))
    if relaxed:
        bounds += [(0.0, None)] * (4 * H)

    rows_eq = 2 * H + 1
    A_eq = lil_matrix((rows_eq, n))
    b_eq = np.zeros(rows_eq)
    for h in range(H):
        # balance: g + s + d - c = demand
        A_eq[h, _idx(G, h)] = 1
        A_eq[h, _idx(S, h)] = 1
        A_eq[h, _idx(D, h)] = 1
        A_eq[h, _idx(C, h)] = -1
        b_eq[h] = lim.demand[h]
        # transition: E[h] - E[h-1] - c + d = 0 (or E0 at h=0)
        r = H + h
        A_eq[r, _idx(E, h)] = 1
        A_eq[r, _idx(C, h)] = -1
        A_eq[r, _idx(D, h)] = 1
        if h == 0:
            b_eq[r] = lim.initial_energy
        else:
            A_eq[r, _idx(E, h - 1)] = -1
    A_eq[2 * H, _idx(E, H - 1)] = 1
    b_eq[2 * H] = lim.initial_energy

    A_ub = None
    b_ub = None
    if relaxed:
        # Directive limits become soft:  E + rs >= e_min,  g - gx <= cap,  c - cx <= cmax, d - dx <= dmax
        rows = []
        rhs = []
        for h in range(H):
            if lim.e_min[h] > lim.base_minimum:
                rows.append({_idx(E, h): -1, _idx(RS, h): -1}); rhs.append(-lim.e_min[h])
            if lim.grid_max[h] is not None:
                rows.append({_idx(G, h): 1, _idx(GX, h): -1}); rhs.append(lim.grid_max[h])
            if lim.charge_max[h] < lim.charge_rate:
                rows.append({_idx(C, h): 1, _idx(CX, h): -1}); rhs.append(lim.charge_max[h])
            if lim.discharge_max[h] < lim.discharge_rate:
                rows.append({_idx(D, h): 1, _idx(DX, h): -1}); rhs.append(lim.discharge_max[h])
        if not rows:
            return None  # nothing to relax, the base problem itself failed
        A_ub = lil_matrix((len(rows), n))
        for i, row in enumerate(rows):
            for j, v in row.items():
                A_ub[i, j] = v
        A_ub = A_ub.tocsr()
        b_ub = np.array(rhs)

    res = linprog(cost, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq.tocsr(), b_eq=b_eq,
                  bounds=bounds, method="highs")
    if res.status != 0 or res.x is None:
        return None

    x = res.x
    sol = Solution(
        grid=[float(x[_idx(G, h)]) for h in range(H)],
        solar=[float(x[_idx(S, h)]) for h in range(H)],
        charge=[float(x[_idx(C, h)]) for h in range(H)],
        discharge=[float(x[_idx(D, h)]) for h in range(H)],
        relaxed=relaxed,
    )
    if relaxed:
        labels = {RS: "battery reserve", GX: "grid cap", CX: "no-charge window", DX: "no-discharge window"}
        for block, label in labels.items():
            if any(x[_idx(block, h)] > 1e-6 for h in range(H)):
                sol.violated.append(label)
    return sol
