"""
Floorcast — Cross-Account Seat Sharing

The seat ratio handles one account sharing seats among its own people. This
handles something rarer and more valuable: **two different accounts sharing the
same physical seat because they work different hours.**

A day-shift programme and a night-shift programme can occupy the same desk. The
seats are already there and already paid for, so the saving is immediate and
costs nothing to build.

Four things have to be true, and all of them are reasons a planner would refuse:

* the shifts genuinely do not overlap
* neither account needs enclosed or dedicated space
* no rule says those two clients must not share a floor
* neither is **system locked** — a machine carrying a client's software or data
  cannot simply be handed to another client at six o'clock

That last one is the constraint people forget. A seat is not just furniture; it
is a machine with a build on it. Where the systems are locked the shifts are
irrelevant.
"""

import numpy as np
import pandas as pd

SHIFT_COLS = ["account", "site", "shift", "start_hour", "end_hour"]

# a little contact is tolerable — handover time, not a clash
OVERLAP_TOLERANCE_HOURS = 1.0


def validate(shifts: pd.DataFrame):
    problems = []
    if shifts is None or shifts.empty:
        return ["No shift pattern supplied."]
    missing = [c for c in SHIFT_COLS if c not in shifts.columns]
    if missing:
        problems.append(f"Missing column(s): {', '.join(missing)}")
        return problems
    for c in ("start_hour", "end_hour"):
        bad = pd.to_numeric(shifts[c], errors="coerce")
        if bad.isna().any() or ((bad < 0) | (bad > 24)).any():
            problems.append(f"Column {c} must be an hour between 0 and 24")
    return problems


def _windows(row) -> list:
    """A shift as one or two windows on a 24-hour clock. A night shift crosses
    midnight, so it becomes two."""
    s, e = float(row["start_hour"]), float(row["end_hour"])
    if s == e:
        return [(0.0, 24.0)]                 # round the clock
    return [(s, e)] if s < e else [(s, 24.0), (0.0, e)]


def overlap_hours(a: pd.Series, b: pd.Series) -> float:
    total = 0.0
    for s1, e1 in _windows(a):
        for s2, e2 in _windows(b):
            total += max(0.0, min(e1, e2) - max(s1, s2))
    return round(total, 2)


def _locked(row) -> bool:
    v = str(row.get("system_locked", "")).strip().lower()
    return v in ("yes", "y", "true", "1")


def _needs_own_space(account: str, space_requirements: dict) -> bool:
    return str(space_requirements.get(account, "shared")).lower() in ("enclosed", "secure")


def pairs(shifts: pd.DataFrame, rules=None, space_requirements: dict = None,
          tolerance: float = OVERLAP_TOLERANCE_HOURS) -> pd.DataFrame:
    """Every pair of accounts at a site, and whether they could share a seat.

    Pairs that cannot share are kept in the output with the reason, because
    "why not" is the question a planner will ask next.
    """
    if shifts is None or shifts.empty:
        return pd.DataFrame()
    space_requirements = space_requirements or {}
    rows = []
    for site, g in shifts.groupby("site", dropna=False):
        recs = g.to_dict("records")
        for i in range(len(recs)):
            for j in range(i + 1, len(recs)):
                a, b = pd.Series(recs[i]), pd.Series(recs[j])
                if a["account"] == b["account"]:
                    continue
                ov = overlap_hours(a, b)
                reasons = []
                if ov > tolerance:
                    reasons.append(f"shifts overlap by {ov:.0f} hours")
                if _locked(a) or _locked(b):
                    which = " and ".join(x["account"] for x in (a, b) if _locked(x))
                    reasons.append(f"{which} needs its own systems on the machine")
                for acct in (a["account"], b["account"]):
                    if _needs_own_space(acct, space_requirements):
                        reasons.append(f"{acct} needs its own space")
                if rules is not None:
                    ok, why = rules.may_place(a["account"], "", [b["account"]], [])
                    if not ok:
                        reasons.append(f"{a['account']} {why}")
                rows.append({
                    "site": site, "account_a": a["account"], "shift_a": a["shift"],
                    "account_b": b["account"], "shift_b": b["shift"],
                    "overlap_hours": ov,
                    "can_share": "yes" if not reasons else "no",
                    "why_not": "; ".join(dict.fromkeys(reasons)),
                })
    out = pd.DataFrame(rows)
    return out.sort_values(["can_share", "overlap_hours"]) if not out.empty else out


def savings(pair_table: pd.DataFrame, demand: pd.DataFrame, period: str) -> pd.DataFrame:
    """Seats saved if each workable pair actually shares.

    Two accounts sharing need the larger of the two, not the sum. The saving is
    therefore the smaller of the two requirements — and it is only counted once
    per account, because a seat cannot be shared three ways across two shifts.
    """
    if pair_table is None or pair_table.empty or demand is None or demand.empty:
        return pd.DataFrame()
    ok = pair_table[pair_table["can_share"] == "yes"]
    if ok.empty:
        return pd.DataFrame()
    need = (demand[demand["week"] == period]
            .groupby(["Site", "Account"])["seats"].sum().to_dict())
    rows, used = [], set()
    for _, r in ok.iterrows():
        a = need.get((r["site"], r["account_a"]), 0)
        b = need.get((r["site"], r["account_b"]), 0)
        if not a or not b:
            continue
        if (r["site"], r["account_a"]) in used or (r["site"], r["account_b"]) in used:
            continue
        used.add((r["site"], r["account_a"]))
        used.add((r["site"], r["account_b"]))
        rows.append({"site": r["site"],
                     "pair": f"{r['account_a']} + {r['account_b']}",
                     "shifts": f"{r['shift_a']} / {r['shift_b']}",
                     "seats_apart": int(a + b),
                     "seats_shared": int(max(a, b)),
                     "seats_saved": int(min(a, b))})
    out = pd.DataFrame(rows)
    return out.sort_values("seats_saved", ascending=False) if not out.empty else out


def summary(saving_table: pd.DataFrame) -> dict:
    if saving_table is None or saving_table.empty:
        return {"pairs": 0, "seats_saved": 0}
    return {"pairs": len(saving_table),
            "seats_saved": int(saving_table["seats_saved"].sum()),
            "sites": saving_table["site"].nunique()}


def note(saving_table: pd.DataFrame) -> str:
    s = summary(saving_table)
    if not s["seats_saved"]:
        return ("No two accounts at any site can share a seat today — either their shifts "
                "overlap, or their systems cannot be handed over.")
    return (f"**{s['seats_saved']:,} seats** could be shared across {s['pairs']} pair(s) of "
            f"accounts working opposite shifts at {s['sites']} site(s). Those seats already "
            "exist and are already paid for.")
