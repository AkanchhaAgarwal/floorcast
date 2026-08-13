"""
Floorcast — Commercial and Operational Detail

Five things a seat planner works with that raw capacity does not capture.

**Nesting.** New starters sit somewhere before they reach production. Those seats
are real, occupied, and easy to forget in a plan built from production headcount.

**Move lead time.** A reallocation is not done when the plan says so — it is done
when IT has moved the network. That takes one to two weeks, so a move promised
inside a fortnight is a move that has not happened yet.

**Enclosed or shared.** The request that starts everything specifies whether a
programme needs its own enclosed space or can sit in a shared area. A plan that
puts an enclosed-only client in shared space is wrong before it is inefficient.

**Billable seats.** A seat that is paid for and not used still costs. A seat used
and not paid for costs more.

**Over-contracted floors.** A programme paying for a whole floor while occupying
part of it is the clearest recovery in an estate — the seats already exist, are
already paid for, and nobody has to build anything.
"""

import numpy as np
import pandas as pd

# how long a move actually takes once IT is involved
VLAN_LEAD_WEEKS_MIN = 1
VLAN_LEAD_WEEKS_MAX = 2

SPACE_TYPES = {
    "shared": "Can sit in shared floor space alongside other programmes",
    "enclosed": "Needs its own enclosed space — cannot share a floor",
    "secure": "Needs an access-controlled zone",
}


# ────────────────────────────────────────── nesting
def nesting_need(demand: pd.DataFrame, period: str, nest_pct: float = 0.0,
                 nest_weeks: int = 4) -> pd.DataFrame:
    """Seats a programme needs for new starters on top of its production seats.

    Where the workbook already carries a nesting figure it is used. Otherwise a
    percentage of the period-on-period increase is applied, because nesting
    scales with how fast a programme is growing, not with how big it is.
    """
    if demand is None or demand.empty or "week" not in demand.columns:
        return pd.DataFrame()
    d = demand.copy()
    if "nesting_seats" in d.columns:
        out = d[d["week"] == period].groupby(["Account", "Site"], dropna=False).agg(
            nesting=("nesting_seats", "sum")).reset_index()
        out["basis"] = "from the workbook"
        return out

    periods = sorted(d["week"].unique())
    if period not in periods:
        return pd.DataFrame()
    i = periods.index(period)
    prev = periods[i - 1] if i > 0 else None
    now = d[d["week"] == period].groupby(["Account", "Site"])["seats"].sum()
    if prev is None:
        growth = now * 0
    else:
        was = d[d["week"] == prev].groupby(["Account", "Site"])["seats"].sum()
        growth = (now - was.reindex(now.index).fillna(now)).clip(lower=0)
    out = growth.rename("growth").reset_index()
    out["nesting"] = np.ceil(out["growth"] * float(nest_pct)).astype(int)
    out["basis"] = (f"{nest_pct:.0%} of the increase, held for about {nest_weeks} weeks"
                    if nest_pct else "no nesting assumption set")
    return out[out["nesting"] > 0] if nest_pct else out


def with_nesting(seat_forecast: pd.DataFrame, nesting: pd.DataFrame) -> pd.DataFrame:
    """Add nesting seats to a period's requirement."""
    if nesting is None or nesting.empty or seat_forecast.empty:
        return seat_forecast
    n = nesting.rename(columns={"Account": "account", "Site": "site"})
    key = [c for c in ("account", "site") if c in seat_forecast.columns and c in n.columns]
    if not key:
        return seat_forecast
    out = seat_forecast.merge(n[key + ["nesting"]], on=key, how="left")
    out["nesting"] = out["nesting"].fillna(0).astype(int)
    out["seats_production"] = out["seats"]
    out["seats"] = out["seats"] + out["nesting"]
    return out


# ────────────────────────────────────────── move lead time
def move_schedule(options: pd.DataFrame, wanted_by: str = None,
                  lead_min: int = VLAN_LEAD_WEEKS_MIN,
                  lead_max: int = VLAN_LEAD_WEEKS_MAX) -> pd.DataFrame:
    """When a move would actually land, not just how many seats it touches.

    The network has to follow the people. Until IT has moved the VLAN the seats
    are not usable by the incoming programme, so a plan that needs them sooner
    than the lead time is a plan that does not work.
    """
    if options is None or options.empty:
        return options
    o = options.copy()
    o["it_weeks"] = np.where(o.get("seats_moved", 0) > 100, lead_max, lead_min)
    o["ready_in"] = o["it_weeks"].map(lambda w: f"about {w} week{'s' if w > 1 else ''}")
    if wanted_by:
        try:
            want = pd.Timestamp(wanted_by)
            o["ready_on"] = [pd.Timestamp.today().normalize() + pd.Timedelta(weeks=int(w))
                             for w in o["it_weeks"]]
            o["in_time"] = np.where(o["ready_on"] <= want, "yes", "no")
        except Exception:
            pass
    return o


def move_note(seats_moved: int) -> str:
    w = VLAN_LEAD_WEEKS_MAX if seats_moved > 100 else VLAN_LEAD_WEEKS_MIN
    return (f"Allow about {w} week{'s' if w > 1 else ''} for IT to move the network before "
            "these seats are usable by the incoming programme.")


# ────────────────────────────────────────── enclosed or shared
def space_check(alloc: pd.DataFrame, requirements: dict) -> pd.DataFrame:
    """Which programmes need enclosed space, and whether they have it.

    requirements: {account: 'shared' | 'enclosed' | 'secure'}
    """
    if alloc is None or alloc.empty or not requirements:
        return pd.DataFrame()
    a = alloc.copy()
    if "floor_id" not in a.columns:
        a["floor_id"] = (a["site"].astype(str) + " / " + a["building"].astype(str)
                         + " / " + a["floor"].astype(str))
    occupants = a.groupby("floor_id")["account"].nunique().to_dict()
    rows = []
    for acct, need in requirements.items():
        mine = a[a["account"] == acct]
        if mine.empty:
            continue
        shared_floors = sorted({f for f in mine["floor_id"] if occupants.get(f, 1) > 1})
        ok = (need == "shared") or not shared_floors
        rows.append({"account": acct, "needs": need,
                     "requirement": SPACE_TYPES.get(need, need),
                     "floors_held": mine["floor_id"].nunique(),
                     "shared_floors": len(shared_floors),
                     "meets_requirement": "yes" if ok else "no",
                     "where": ", ".join(f.split(" / ")[-1] for f in shared_floors[:3])})
    out = pd.DataFrame(rows)
    return out.sort_values("meets_requirement") if not out.empty else out


# ────────────────────────────────────────── billing
def billing_position(floors: pd.DataFrame, alloc: pd.DataFrame,
                     contracted: pd.DataFrame = None) -> pd.DataFrame:
    """Seats paid for against seats used.

    contracted: account, site, building, floor, seats_contracted. Without it the
    allocation is treated as the contract, which answers nothing — so the
    function says so rather than inventing a number.
    """
    if alloc is None or alloc.empty:
        return pd.DataFrame()
    used = (alloc.groupby(["site", "building", "floor", "account"], dropna=False)["seats"]
            .sum().rename("seats_used").reset_index())
    if contracted is None or contracted.empty:
        used["seats_contracted"] = np.nan
        used["note"] = "no contract figure supplied"
        return used
    m = used.merge(contracted, on=["site", "building", "floor", "account"], how="outer")
    m["seats_used"] = m["seats_used"].fillna(0).astype(int)
    m["seats_contracted"] = m["seats_contracted"].fillna(0).astype(int)
    m["paid_not_used"] = (m["seats_contracted"] - m["seats_used"]).clip(lower=0)
    m["used_not_paid"] = (m["seats_used"] - m["seats_contracted"]).clip(lower=0)
    m["utilisation_%"] = np.where(m["seats_contracted"] > 0,
                                  (m["seats_used"] / m["seats_contracted"] * 100).round(1),
                                  np.nan)
    return m.sort_values("paid_not_used", ascending=False)


# ────────────────────────────────────────── over-contracted floors
def over_contracted(floors: pd.DataFrame, alloc: pd.DataFrame,
                    threshold_pct: float = 70.0) -> pd.DataFrame:
    """Floors where one programme holds the space but uses well under it.

    The cheapest capacity in any estate: the seats exist, they are already paid
    for, and nothing has to be built. Recovering them is a conversation, not a
    project.
    """
    if floors is None or floors.empty or alloc is None or alloc.empty:
        return pd.DataFrame()
    a = alloc.copy()
    used = a.groupby(["site", "building", "floor"], dropna=False).agg(
        used=("seats", "sum"), accounts=("account", "nunique"),
        who=("account", lambda x: ", ".join(sorted(set(x))))).reset_index()
    m = floors.merge(used, on=["site", "building", "floor"], how="left")
    m["used"] = m["used"].fillna(0).astype(int)
    m["accounts"] = m["accounts"].fillna(0).astype(int)
    m["floor_use_%"] = np.where(m["total_seats"] > 0,
                                (m["used"] / m["total_seats"] * 100).round(1), 0)
    m["recoverable"] = (m["total_seats"] - m["used"] - m["trapped"]).clip(lower=0).astype(int)
    out = m[(m["accounts"] == 1) & (m["floor_use_%"] < threshold_pct) & (m["used"] > 0)]
    cols = ["site", "building", "floor", "who", "total_seats", "used", "floor_use_%",
            "trapped", "recoverable"]
    out = out[[c for c in cols if c in out.columns]]
    return out.sort_values("recoverable", ascending=False)


def over_contracted_note(rows: pd.DataFrame) -> str:
    if rows is None or rows.empty:
        return "No floor is held by a single programme that is using well under it."
    n = int(rows["recoverable"].sum())
    return (f"**{n:,} seats** sit on floors held by one programme that is using well under "
            "the space. They already exist and are already paid for — reallocating them to an "
            "incoming programme is the cheapest capacity in the estate.")
