"""Floorcast — Stage 3: Shift-Mix Scheduler (set covering MIP)."""
import pulp
import numpy as np

SLOTS = 48

def shift_coverage(start_hour: float, dur_hours: float = 9) -> np.ndarray:
    """48-slot coverage vector (wraps midnight)."""
    cov = np.zeros(SLOTS, dtype=int)
    s = int(start_hour * 2)
    for k in range(int(dur_hours * 2)):
        cov[(s + k) % SLOTS] = 1
    return cov

def solve_shift_mix(requirement: list, start_hours: list = None, dur: float = 9):
    """Min total agents s.t. every interval covered. Returns (mix dict, coverage array)."""
    starts = start_hours or [0, 2, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22]
    covs = {s: shift_coverage(s, dur) for s in starts}

    prob = pulp.LpProblem("ShiftMix", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("shift", starts, lowBound=0, cat="Integer")
    prob += pulp.lpSum(x[s] for s in starts)
    for t in range(SLOTS):
        prob += pulp.lpSum(int(covs[s][t]) * x[s] for s in starts) >= requirement[t], f"cover_{t}"
    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    mix = {s: int(x[s].value() or 0) for s in starts if (x[s].value() or 0) > 0.5}
    coverage = np.zeros(SLOTS, dtype=int)
    for s, n in mix.items():
        coverage += covs[s] * n
    return mix, coverage
