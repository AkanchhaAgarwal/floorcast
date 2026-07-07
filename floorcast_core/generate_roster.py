"""
SeatIQ — Synthetic Roster Generator
Generates a realistic contact-center roster: ~1,200 agents, 4 programs,
mixed 24/7 / business-hours / split shifts, WFH flags, and leave.

Run:  python -m seatiq.generate_roster
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta

RNG_SEED = 42

# ---------------------------------------------------------------------------
# Program catalogue
# Each program has: headcount, channel type, WFH share, and a shift catalogue.
# Shift = (label, start_hour, duration_hours, weight)  — weight = share of agents
# ---------------------------------------------------------------------------
PROGRAMS = {
    "RetailVoice": {          # 24/7 voice program (client requires dedicated bays)
        "headcount": 420,
        "channel": "voice",
        "wfh_share": 0.15,
        "dedicated": True,
        "shifts": [
            ("06:00-15:00", 6, 9, 0.20),
            ("09:00-18:00", 9, 9, 0.25),
            ("13:30-22:30", 13.5, 9, 0.25),   # deliberately overlaps next wave
            ("14:00-23:00", 14, 9, 0.15),
            ("22:00-07:00", 22, 9, 0.15),     # night, crosses midnight
        ],
    },
    "TechSupport": {          # 24/7 voice+chat
        "headcount": 360,
        "channel": "voice",
        "wfh_share": 0.30,
        "dedicated": False,
        "shifts": [
            ("07:00-16:00", 7, 9, 0.30),
            ("12:00-21:00", 12, 9, 0.30),
            ("16:00-01:00", 16, 9, 0.20),
            ("23:00-08:00", 23, 9, 0.20),
        ],
    },
    "ChatSupport": {          # split shifts — the classic seat-math headache
        "headcount": 240,
        "channel": "chat",
        "wfh_share": 0.40,
        "dedicated": False,
        "shifts": [
            ("08:00-17:00", 8, 9, 0.40),
            ("SPLIT 09:00-13:00 + 17:00-21:00", 9, 4, 0.30),  # split handled below
            ("11:00-20:00", 11, 9, 0.30),
        ],
    },
    "BackOffice": {           # business hours, high WFH
        "headcount": 180,
        "channel": "backoffice",
        "wfh_share": 0.50,
        "dedicated": False,
        "shifts": [
            ("09:00-18:00", 9, 9, 0.70),
            ("10:00-19:00", 10, 9, 0.30),
        ],
    },
}

LEAVE_RATE = 0.08          # daily probability an agent is on leave
TRAINING_RATE = 0.03       # daily probability an agent is in a training room (no prod seat)
DAYS = 14                  # roster horizon
START_DATE = date(2026, 7, 6)


def _assign_shifts(rng, program_cfg, n):
    labels = [s[0] for s in program_cfg["shifts"]]
    weights = [s[3] for s in program_cfg["shifts"]]
    return rng.choice(labels, size=n, p=np.array(weights) / sum(weights))


def generate_roster(days: int = DAYS, start: date = START_DATE, seed: int = RNG_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    agent_id = 1000

    for prog, cfg in PROGRAMS.items():
        n = cfg["headcount"]
        agents = [f"A{agent_id + i}" for i in range(n)]
        agent_id += n
        base_shift = _assign_shifts(rng, cfg, n)          # agents keep a base shift
        wfh_agents = set(rng.choice(agents, size=int(n * cfg["wfh_share"]), replace=False))

        for d in range(days):
            day = start + timedelta(days=d)
            # weekly offs: 2 rotating days per agent
            week_off = {(hash(a) + d) % 7 in (0, 1) for a in []}  # placeholder, computed per agent below
            for a, shift in zip(agents, base_shift):
                if (hash(a) % 7) in ((day.weekday()) % 7, (day.weekday() + 1) % 7):
                    continue  # weekly off
                on_leave = rng.random() < LEAVE_RATE
                in_training = (not on_leave) and rng.random() < TRAINING_RATE
                rows.append({
                    "date": day.isoformat(),
                    "agent_id": a,
                    "program": prog,
                    "channel": cfg["channel"],
                    "shift": shift,
                    "work_mode": "WFH" if a in wfh_agents else "Office",
                    "status": "Leave" if on_leave else ("Training" if in_training else "Working"),
                })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_roster()
    out = "data/roster.csv"
    df.to_csv(out, index=False)
    print(f"Wrote {len(df):,} roster rows for {df['agent_id'].nunique():,} agents -> {out}")
    print(df.groupby('program')['agent_id'].nunique())
