"""
Floorcast — Expansion Planning

What used to be called ramp matching, framed the way the approval actually works.

**Expansion is gated on occupancy.** A site does not get new space because it
wants it — it gets new space when it is demonstrably using what it already has.
Below the gate the answer is not "no", it is "fill what you hold first", and the
tool says which seats those are.

**Escalation is the output, not the shortfall.** A number of seats short is a
fact. What a planner needs to leave the meeting with is who has to do something,
by when, and why. So every site that breaks produces a named escalation with a
reason attached.

Trapped seats are deliberately absent here. Whether a seat is recoverable is an
estate question and belongs on the estate view; mixing it into the expansion
case muddies an approval that has to stand on occupancy alone.
"""

import numpy as np
import pandas as pd

# a site must be using this much of its space before new space is approved
OCCUPANCY_GATE = 90.0

ESCALATION = {
    "gate_not_met": ("Operations", "Fill the space already held before new space is sought"),
    "expansion_in_flight": ("Facility", "Confirm the renovation lands on the date given"),
    "expansion_needed": ("Facility", "Bring forward or scope a renovation"),
    "it_provisioning": ("IT", "Provision the seats that are built but not usable"),
    "another_site": ("Operations", "Place the overflow at another site"),
    "defer": ("Operations", "Defer the ramp or reduce the ask"),
    "commercial": ("Finance", "Approve the spend, or renegotiate the contracted space"),
}


def site_position(floors: pd.DataFrame, demand_by_site: dict) -> pd.DataFrame:
    """Where each site stands: how full it is, what it needs, what is coming."""
    if floors is None or floors.empty:
        return pd.DataFrame()
    g = (floors.groupby("site", dropna=False)
         .agg(total_seats=("total_seats", "sum"),
              allocated=("allocated", "sum"),
              available=("available", "sum"),
              expansion_space=("expansion_space", "sum"))
         .reset_index())
    g["occupancy_%"] = np.where(g["total_seats"] > 0,
                                (g["allocated"] / g["total_seats"] * 100).round(1), 0)
    g["required"] = g["site"].map(demand_by_site).fillna(0).astype(int)
    g["incremental_need"] = (g["required"] - g["allocated"]).clip(lower=0).astype(int)
    g["gate_met"] = np.where(g["occupancy_%"] >= OCCUPANCY_GATE, "yes", "no")
    g["after_available"] = (g["incremental_need"] - g["available"]).clip(lower=0).astype(int)
    g["after_expansion"] = (g["after_available"] - g["expansion_space"]).clip(lower=0).astype(int)
    return g.sort_values("after_expansion", ascending=False)


def escalations(position: pd.DataFrame, floors: pd.DataFrame = None) -> pd.DataFrame:
    """One row per site that needs somebody to do something, and who."""
    if position is None or position.empty:
        return pd.DataFrame()
    exp_status = {}
    if floors is not None and "expansion_status" in floors.columns:
        for _, r in floors[floors["expansion_space"] > 0].iterrows():
            exp_status.setdefault(r["site"], []).append(
                (r.get("expansion_status", ""), r.get("expansion_expected", "")))

    rows = []
    for _, r in position.iterrows():
        if r["incremental_need"] <= 0:
            continue
        if r["gate_met"] == "no":
            owner, action = ESCALATION["gate_not_met"]
            rows.append({"site": r["site"], "raise_with": owner, "action": action,
                         "seats": int(r["incremental_need"]),
                         "why": f"Occupancy is {r['occupancy_%']:.0f}%, below the "
                                f"{OCCUPANCY_GATE:.0f}% needed before new space is approved. "
                                f"{int(r['available'])} seats are already free here."})
            continue
        if r["after_available"] <= 0:
            continue
        if r["after_expansion"] <= 0:
            st = exp_status.get(r["site"], [("", "")])[0]
            owner, action = ESCALATION["expansion_in_flight"]
            rows.append({"site": r["site"], "raise_with": owner, "action": action,
                         "seats": int(r["after_available"]),
                         "why": f"Covered by renovation already {st[0].lower() or 'planned'}"
                                + (f", expected {st[1]}" if st[1] else "")
                                + ". The date is the risk, not the space."})
            continue
        owner, action = ESCALATION["another_site"]
        rows.append({"site": r["site"], "raise_with": owner, "action": action,
                     "seats": int(r["after_expansion"]),
                     "why": f"Short by {int(r['after_expansion'])} seats even after every "
                            "renovation in the plan. This needs another site or a later date."})
    out = pd.DataFrame(rows)
    return out.sort_values("seats", ascending=False) if not out.empty else out


def gate_summary(position: pd.DataFrame) -> dict:
    if position is None or position.empty:
        return {}
    asking = position[position["incremental_need"] > 0]
    blocked = asking[asking["gate_met"] == "no"]
    return {"sites_asking": len(asking),
            "blocked_by_gate": len(blocked),
            "seats_blocked": int(blocked["incremental_need"].sum()),
            "free_where_blocked": int(blocked["available"].sum())}


# ────────────────────────────────────────── renovation programme
def expansion_schedule(floors: pd.DataFrame) -> pd.DataFrame:
    """Every renovation in the plan, by site, with where it has got to."""
    if floors is None or floors.empty or "expansion_space" not in floors.columns:
        return pd.DataFrame()
    e = floors[floors["expansion_space"] > 0].copy()
    if e.empty:
        return pd.DataFrame()
    cols = ["site", "building", "floor", "expansion_space", "expansion_status",
            "expansion_started", "expansion_expected", "expansion_eta_weeks", "notes"]
    e = e[[c for c in cols if c in e.columns]]
    if "expansion_expected" in e.columns:
        exp = pd.to_datetime(e["expansion_expected"], errors="coerce")
        today = pd.Timestamp.today().normalize()
        e["weeks_away"] = ((exp - today).dt.days / 7).round(1)
        e["lands"] = np.where(exp.isna(), "no date",
                              np.where(exp < today, "overdue",
                                       np.where(e["weeks_away"] <= 12, "within the quarter",
                                                "later")))
    return e.sort_values("expansion_expected")


def expansion_by_year(floors: pd.DataFrame) -> pd.DataFrame:
    """The renovation programme rolled up by year — the view a budget holder wants."""
    e = expansion_schedule(floors)
    if e.empty or "expansion_expected" not in e.columns:
        return pd.DataFrame()
    e = e.copy()
    exp = pd.to_datetime(e["expansion_expected"], errors="coerce")
    e["year"] = exp.dt.year
    e["quarter"] = "Q" + exp.dt.quarter.astype("Int64").astype(str)
    g = (e.groupby(["year", "quarter"], dropna=False)
         .agg(seats=("expansion_space", "sum"), projects=("site", "count"),
              sites=("site", lambda x: ", ".join(sorted(set(x)))))
         .reset_index())
    g["running_total"] = g["seats"].cumsum()
    return g


# ────────────────────────────────────────── programmes winding down
def sunset_effect(sunset: pd.DataFrame, period: str = None) -> pd.DataFrame:
    """Seats coming back as programmes end.

    The mirror of a deal not yet won: demand that is leaving rather than arriving,
    and just as easy to forget until the floor empties.
    """
    if sunset is None or sunset.empty:
        return pd.DataFrame()
    s = sunset.copy()
    s["end_month"] = s["end_month"].astype(str)
    if period:
        s = s[s["end_month"] <= str(period)[:7]]
    return s.sort_values("end_month")


def sunset_by_site(sunset: pd.DataFrame) -> pd.DataFrame:
    if sunset is None or sunset.empty:
        return pd.DataFrame()
    return (sunset.groupby("site")
            .agg(seats_returning=("seats_released", "sum"),
                 programmes=("account", "count"),
                 earliest=("end_month", "min"))
            .reset_index().sort_values("seats_returning", ascending=False))


def net_position(position: pd.DataFrame, sunset: pd.DataFrame) -> pd.DataFrame:
    """Expansion need set against seats coming back — the number that matters."""
    if position is None or position.empty:
        return position
    p = position.copy()
    back = (sunset.groupby("site")["seats_released"].sum().to_dict()
            if sunset is not None and not sunset.empty else {})
    p["seats_returning"] = p["site"].map(back).fillna(0).astype(int)
    p["net_need"] = (p["after_expansion"] - p["seats_returning"]).clip(lower=0).astype(int)
    return p
