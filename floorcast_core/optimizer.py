"""
SeatIQ — Module 2: Allocation Optimizer
Assigns programs to bays via a mixed-integer program (PuLP/CBC).

Constraints encoded:
  1. Every program gets at least its required seats (peak demand + buffer).
  2. Bay capacity is never exceeded.
  3. Dedicated programs (client segregation) never share a bay.
  4. Voice programs cannot sit in quiet-zone bays.
Objective: minimise fragmentation (number of bays each program is split across).
"""

import pulp
import pandas as pd

# ---------------------------------------------------------------------------
# Floor configuration: (bay_id, floor, capacity, quiet_zone)
# ---------------------------------------------------------------------------
DEFAULT_BAYS = [
    ("F1-B1", "Floor 1", 96, False),
    ("F1-B2", "Floor 1", 96, False),
    ("F1-B3", "Floor 1", 72, False),
    ("F1-B4", "Floor 1", 72, True),    # quiet zone
    ("F2-B1", "Floor 2", 96, False),
    ("F2-B2", "Floor 2", 96, False),
    ("F2-B3", "Floor 2", 72, False),
    ("F2-B4", "Floor 2", 48, True),    # quiet zone
]


def optimize_allocation(
    requirements: pd.DataFrame,      # columns: program, peak_seats, channel, dedicated
    bays: list = None,
    buffer_pct: float = 0.05,
):
    """Returns (status, allocation_df, diagnostics)."""
    bays = bays or DEFAULT_BAYS
    bay_ids = [b[0] for b in bays]
    cap = {b[0]: b[2] for b in bays}
    quiet = {b[0]: b[3] for b in bays}

    programs = requirements["program"].tolist()
    need = {
        r.program: int(r.peak_seats * (1 + buffer_pct)) + 1
        for r in requirements.itertuples()
    }
    is_voice = {r.program: r.channel == "voice" for r in requirements.itertuples()}
    dedicated = {r.program: bool(r.dedicated) for r in requirements.itertuples()}

    prob = pulp.LpProblem("SeatIQ_Allocation", pulp.LpMinimize)

    # x[p,b] = seats of program p in bay b ; y[p,b] = 1 if program p uses bay b
    x = pulp.LpVariable.dicts("seats", (programs, bay_ids), lowBound=0, cat="Integer")
    y = pulp.LpVariable.dicts("uses", (programs, bay_ids), cat="Binary")

    # Objective: minimise total bays used across programs (fragmentation)
    prob += pulp.lpSum(y[p][b] for p in programs for b in bay_ids)

    for p in programs:
        prob += pulp.lpSum(x[p][b] for b in bay_ids) >= need[p], f"demand_{p}"
        for b in bay_ids:
            prob += x[p][b] <= cap[b] * y[p][b]                      # linking
            if is_voice[p] and quiet[b]:
                prob += y[p][b] == 0, f"quiet_{p}_{b}"               # noise rule

    for b in bay_ids:
        prob += pulp.lpSum(x[p][b] for p in programs) <= cap[b], f"cap_{b}"
        # segregation: a dedicated program in a bay excludes all others
        for p in programs:
            if dedicated[p]:
                for q in programs:
                    if q != p:
                        prob += y[p][b] + y[q][b] <= 1, f"seg_{p}_{q}_{b}"

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[prob.status]

    rows = []
    if status == "Optimal":
        for p in programs:
            for b in bay_ids:
                s = int(x[p][b].value() or 0)
                if s > 0:
                    rows.append({"program": p, "bay": b,
                                 "floor": dict((bb[0], bb[1]) for bb in bays)[b],
                                 "seats": s, "bay_capacity": cap[b]})
    alloc = pd.DataFrame(rows)

    diagnostics = {
        "status": status,
        "total_seats_required": sum(need.values()),
        "total_capacity": sum(cap.values()),
        "seats_needed_by_program": need,
        "bays_used": len(alloc["bay"].unique()) if not alloc.empty else 0,
    }
    return status, alloc, diagnostics


if __name__ == "__main__":
    from seatiq.demand_engine import compute_demand, demand_summary
    roster = pd.read_csv("data/roster.csv")
    demand = compute_demand(roster)
    summary = demand_summary(demand, roster)

    meta = roster.groupby("program").agg(channel=("channel", "first")).reset_index()
    meta["dedicated"] = meta["program"].eq("RetailVoice")
    req = summary.merge(meta, on="program")

    status, alloc, diag = optimize_allocation(req)
    print("Solver status:", status)
    print(alloc.to_string(index=False))
    print(diag)
