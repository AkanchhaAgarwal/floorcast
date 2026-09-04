"""
Floorcast — Seating Models

Not every organisation seats people the same way, and the planning question
changes with the model.

**Pooled** — a contact centre. Seats are shared within an account at a ratio, and
nobody owns a desk. The question is how many seats, not whose.

**Assigned** — a bank, a legal firm, a trading floor. Every person has their own
desk, the ratio is one, and the question becomes *whose desk is whose* and which
desks are sitting empty against a name.

**Neighbourhood** — a team owns a zone and people sit anywhere inside it. The
middle ground, and the one hybrid workplaces have converged on: the team has a
home, the individual does not have a chair.

Floorcast was built for the first. This module adds the other two, because a
tool that only plans contact centres can only be sold to contact centres.

One honest boundary: assigned seating here is a *planning* model. It says who
should sit where and what is vacant. It is not a booking system and does not
know who actually turned up — that needs badge or sensor data, which is a
different product.
"""

import numpy as np
import pandas as pd

MODELS = {
    "pooled": {
        "label": "Pooled seats",
        "for": "Contact centres and shift operations",
        "ratio": "Seat sharing ratio applied per account",
        "question": "How many seats does each account need?",
    },
    "assigned": {
        "label": "Assigned seats",
        "for": "Banks, professional services, anywhere people own a desk",
        "ratio": "Fixed at one person per seat",
        "question": "Whose desk is whose, and which are empty?",
    },
    "neighbourhood": {
        "label": "Team neighbourhoods",
        "for": "Hybrid offices where a team has a home but nobody owns a chair",
        "ratio": "Sharing applied within the team's own zone",
        "question": "Which zone belongs to which team, and is it the right size?",
    },
}

ROSTER_COLS = ["employee_id", "account", "site"]


def effective_ratio(model: str, stated_ratio: float) -> float:
    """Assigned seating overrides whatever ratio the workbook carries — one
    person, one desk, regardless of what the forecast says."""
    return 1.0 if model == "assigned" else float(stated_ratio or 1.0)


def seats_needed(model: str, headcount: float, ratio: float,
                 zone_size: int = None) -> int:
    if model == "assigned":
        return int(np.ceil(headcount))
    n = int(np.ceil(headcount / max(effective_ratio(model, ratio), 0.01)))
    if model == "neighbourhood" and zone_size:
        # a neighbourhood is taken whole; a team of 30 in a zone of 48 holds 48
        return int(np.ceil(n / zone_size) * zone_size)
    return n


# ────────────────────────────────────────── assigned seating
def validate_roster(roster: pd.DataFrame):
    problems = []
    if roster is None or roster.empty:
        return ["The roster has no rows."]
    missing = [c for c in ROSTER_COLS if c not in roster.columns]
    if missing:
        problems.append(f"Missing column(s): {', '.join(missing)}")
        return problems
    dup = roster["employee_id"].duplicated()
    if dup.any():
        problems.append(f"{int(dup.sum())} duplicate employee id(s), "
                        f"e.g. {roster.loc[dup, 'employee_id'].iloc[0]}")
    return problems


def assign(roster: pd.DataFrame, seats: pd.DataFrame,
           keep_existing: bool = True) -> pd.DataFrame:
    """Give every person a named desk.

    People already sitting somewhere stay there. Moving someone who does not
    need to move is the most expensive thing a seating plan can do, so the
    default is to leave them alone and place only those without a seat.
    """
    if roster is None or roster.empty or seats is None or seats.empty:
        return pd.DataFrame()
    r = roster.copy()
    s = seats.copy()
    if "seat_id" not in s.columns:
        return pd.DataFrame()
    if "zone_type" in s.columns:
        s = s[s["zone_type"].astype(str).str.lower() == "production"]
    if "seat_status" in s.columns:
        s = s[s["seat_status"].fillna("usable").ne("trapped")]

    taken, out = set(), []
    if keep_existing and "seat_id" in r.columns:
        for _, p in r[r["seat_id"].notna()].iterrows():
            if p["seat_id"] in set(s["seat_id"]) and p["seat_id"] not in taken:
                taken.add(p["seat_id"])
                out.append({**p.to_dict(), "assigned_seat": p["seat_id"],
                            "status": "Kept existing seat"})

    seated_ids = {o["employee_id"] for o in out}
    free = [x for x in s["seat_id"].tolist() if x not in taken]
    zone_of = dict(zip(s["seat_id"], s.get("zone", pd.Series(index=s.index, dtype=str))))

    # place each account's people together, largest account first
    remaining = r[~r["employee_id"].isin(seated_ids)]
    for acct, grp in sorted(remaining.groupby("account"),
                            key=lambda kv: -len(kv[1])):
        for _, p in grp.iterrows():
            if free:
                seat = free.pop(0)
                out.append({**p.to_dict(), "assigned_seat": seat,
                            "zone": zone_of.get(seat, ""), "status": "Newly assigned"})
            else:
                out.append({**p.to_dict(), "assigned_seat": None, "zone": "",
                            "status": "No seat available"})
    return pd.DataFrame(out)


def vacancies(assignment: pd.DataFrame, seats: pd.DataFrame) -> pd.DataFrame:
    """Desks with nobody's name against them. In an assigned model this is the
    number that matters — an empty named desk is paid for and idle."""
    if seats is None or seats.empty:
        return pd.DataFrame()
    s = seats.copy()
    if "zone_type" in s.columns:
        s = s[s["zone_type"].astype(str).str.lower() == "production"]
    used = set(assignment["assigned_seat"].dropna()) if assignment is not None \
        and not assignment.empty else set()
    v = s[~s["seat_id"].isin(used)]
    cols = [c for c in ("site", "building", "floor", "zone", "seat_id", "desk_size_mm")
            if c in v.columns]
    return v[cols]


def assignment_summary(assignment: pd.DataFrame, seats: pd.DataFrame) -> dict:
    if assignment is None or assignment.empty:
        return {}
    placed = int(assignment["assigned_seat"].notna().sum())
    unplaced = int(assignment["assigned_seat"].isna().sum())
    total_seats = 0
    if seats is not None and not seats.empty:
        s = seats
        if "zone_type" in s.columns:
            s = s[s["zone_type"].astype(str).str.lower() == "production"]
        total_seats = len(s)
    return {"people": len(assignment), "seated": placed, "without_a_seat": unplaced,
            "desks": total_seats, "empty_desks": max(total_seats - placed, 0),
            "kept_existing": int((assignment["status"] == "Kept existing seat").sum())
            if "status" in assignment.columns else 0}


# ────────────────────────────────────────── neighbourhoods
def neighbourhoods(seats: pd.DataFrame, demand: pd.DataFrame, period: str,
                   ratio_default: float = 1.0) -> pd.DataFrame:
    """Match each team to a zone and say whether the zone is the right size.

    A neighbourhood is taken whole, so a team of thirty in a zone of forty-eight
    holds forty-eight. That waste is the price of the model, and it should be
    visible rather than buried.
    """
    if seats is None or seats.empty or "zone" not in seats.columns:
        return pd.DataFrame()
    zones = (seats[seats.get("zone_type", "Production").astype(str).str.lower()
                   == "production"]
             .groupby("zone").size().sort_values(ascending=False))
    if zones.empty or demand is None or demand.empty:
        return pd.DataFrame()
    need = (demand[demand["week"] == period]
            .groupby(["Account", "LOB"])["seats"].sum().sort_values(ascending=False)
            if "LOB" in demand.columns
            else demand[demand["week"] == period].groupby("Account")["seats"].sum())

    free = zones.to_dict()
    rows = []
    for key, want in need.items():
        team = " / ".join(str(k) for k in key) if isinstance(key, tuple) else str(key)
        fit = {z: n for z, n in free.items() if n > 0 and n >= want}
        if fit:
            z = min(fit, key=fit.get)                # smallest zone that holds them
        elif any(v > 0 for v in free.values()):
            z = max((k for k, v in free.items() if v > 0), key=lambda k: free[k])
        else:
            rows.append({"team": team, "seats_needed": int(want), "zone": "—",
                         "zone_size": 0, "spare_in_zone": 0,
                         "verdict": "No zone left at this site"})
            continue
        size = int(free[z])
        free[z] = 0
        rows.append({"team": team, "seats_needed": int(want), "zone": z,
                     "zone_size": size, "spare_in_zone": int(size - want),
                     "verdict": ("Fits, with room to grow" if size - want > 0
                                 else "Exact fit" if size == want
                                 else f"Overflows by {int(want - size)}")})
    return pd.DataFrame(rows)
