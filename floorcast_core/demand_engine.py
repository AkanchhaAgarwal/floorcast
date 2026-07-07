"""
SeatIQ — Module 1: Seat Demand Engine
Converts a roster into 30-minute interval seat demand (bodies-in-building),
per program. Handles overnight shifts (crossing midnight) and split shifts.
"""

import re
import numpy as np
import pandas as pd

INTERVAL_MIN = 30
SLOTS_PER_DAY = 24 * 60 // INTERVAL_MIN   # 48

_SPLIT_RE = re.compile(r"SPLIT (\d{2}):(\d{2})-(\d{2}):(\d{2}) \+ (\d{2}):(\d{2})-(\d{2}):(\d{2})")
_PLAIN_RE = re.compile(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})")


def _shift_to_slots(shift_label: str) -> np.ndarray:
    """Return a boolean array of length 48 (+48 spillover) marking occupied slots.

    Index 0..47 = today's slots; 48..95 = tomorrow's slots (overnight spill).
    """
    occ = np.zeros(SLOTS_PER_DAY * 2, dtype=bool)

    def mark(h1, m1, h2, m2):
        start = int((h1 * 60 + m1) / INTERVAL_MIN)
        end = int((h2 * 60 + m2) / INTERVAL_MIN)
        if end <= start:                      # crosses midnight
            end += SLOTS_PER_DAY
        occ[start:end] = True

    m = _SPLIT_RE.match(shift_label)
    if m:
        g = list(map(int, m.groups()))
        mark(g[0], g[1], g[2], g[3])
        mark(g[4], g[5], g[6], g[7])
        return occ

    m = _PLAIN_RE.match(shift_label)
    if m:
        h1, m1, h2, m2 = map(int, m.groups())
        mark(h1, m1, h2, m2)
        return occ

    raise ValueError(f"Unparseable shift label: {shift_label}")


def compute_demand(roster: pd.DataFrame) -> pd.DataFrame:
    """Interval seat demand per (date, program).

    Only Office-mode, Working-status agents consume a production seat.
    Returns long dataframe: date, program, slot (0-47), time, seats_needed.
    """
    seated = roster[(roster["work_mode"] == "Office") & (roster["status"] == "Working")].copy()

    dates = sorted(seated["date"].unique())
    programs = sorted(seated["program"].unique())
    date_ix = {d: i for i, d in enumerate(dates)}

    # demand[date, program, slot]
    demand = np.zeros((len(dates), len(programs), SLOTS_PER_DAY), dtype=int)
    shift_cache = {}

    for (dt, prog, shift), grp in seated.groupby(["date", "program", "shift"]):
        occ = shift_cache.setdefault(shift, _shift_to_slots(shift))
        n = len(grp)
        di, pi = date_ix[dt], programs.index(prog)
        demand[di, pi, :] += n * occ[:SLOTS_PER_DAY]
        spill = occ[SLOTS_PER_DAY:]
        if spill.any() and di + 1 < len(dates):          # overnight spill into next day
            demand[di + 1, pi, :] += n * spill

    records = []
    for di, dt in enumerate(dates):
        for pi, prog in enumerate(programs):
            for s in range(SLOTS_PER_DAY):
                records.append((dt, prog, s, f"{s // 2:02d}:{(s % 2) * 30:02d}", demand[di, pi, s]))
    return pd.DataFrame(records, columns=["date", "program", "slot", "time", "seats_needed"])


def demand_summary(demand: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    """Peak concurrency, rostered office headcount, and true seat-sharing ratio per program."""
    peak = demand.groupby("program")["seats_needed"].max().rename("peak_seats")
    office_hc = (
        roster[roster["work_mode"] == "Office"]
        .groupby("program")["agent_id"].nunique().rename("office_headcount")
    )
    out = pd.concat([office_hc, peak], axis=1)
    out["seat_sharing_ratio"] = (out["office_headcount"] / out["peak_seats"]).round(2)
    return out.reset_index()


if __name__ == "__main__":
    roster = pd.read_csv("data/roster.csv")
    demand = compute_demand(roster)
    demand.to_csv("data/demand.csv", index=False)
    print(demand_summary(demand, roster).to_string(index=False))
