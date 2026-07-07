"""Floorcast — Stage 5: Layout Engine.

Divides a rectangular floor grid of pods among programs, minimising the
total length of internal partitions (boundaries between different programs
or between a program and empty space).
"""
import pulp
import numpy as np

def solve_layout(rows: int, cols: int, pods_needed: dict,
                 quiet_cols: list = None, voice_programs: set = None,
                 pod_edge_ft: float = 17.0):
    """pods_needed: {program: n_pods}. quiet_cols: column indices reserved quiet.
    Returns (grid array of program labels or '', partition_linear_ft)."""
    quiet_cols = quiet_cols or []
    voice_programs = voice_programs or set()
    cells = [(r, c) for r in range(rows) for c in range(cols)]
    progs = list(pods_needed)

    if sum(pods_needed.values()) > len(cells):
        return None, None   # infeasible: floor too small

    prob = pulp.LpProblem("Layout", pulp.LpMinimize)
    z = pulp.LpVariable.dicts("z", (progs, range(rows), range(cols)), cat="Binary")

    # edges between orthogonally adjacent cells
    edges = []
    for r, c in cells:
        if c + 1 < cols: edges.append(((r, c), (r, c + 1)))
        if r + 1 < rows: edges.append(((r, c), (r + 1, c)))
    w = pulp.LpVariable.dicts("w", range(len(edges)), lowBound=0)

    prob += pulp.lpSum(w[i] for i in range(len(edges)))          # partition length

    for p in progs:
        prob += pulp.lpSum(z[p][r][c] for r, c in cells) >= pods_needed[p]
        for c in quiet_cols:
            if p in voice_programs:
                for r in range(rows):
                    prob += z[p][r][c] == 0
    for r, c in cells:
        prob += pulp.lpSum(z[p][r][c] for p in progs) <= 1        # one program per pod

    for i, ((r1, c1), (r2, c2)) in enumerate(edges):
        for p in progs:
            prob += w[i] >= z[p][r1][c1] - z[p][r2][c2]
            prob += w[i] >= z[p][r2][c2] - z[p][r1][c1]

    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=60))
    if pulp.LpStatus[prob.status] not in ("Optimal", "Not Solved"):
        return None, None

    grid = np.full((rows, cols), "", dtype=object)
    for p in progs:
        for r, c in cells:
            if (z[p][r][c].value() or 0) > 0.5:
                grid[r][c] = p
    part_ft = sum((w[i].value() or 0) for i in range(len(edges))) * pod_edge_ft
    return grid, part_ft
