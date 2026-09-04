"""
Floorcast — Revenue per Seat

A rate per seat, set per site, turns a capacity plan into a commercial one.

Two numbers come out of it, and they pull in opposite directions:

**Revenue per seat sold** is the rate — what a seat earns when somebody is
paying for it.

**Revenue per seat owned** is that rate spread across every seat in the
building, including the ones earning nothing. It is always lower, and the gap
between the two is the cost of empty and unusable space. A site can be charging
a healthy rate and still be diluted to nothing by seats it cannot sell.

That gap is the whole point. It converts the trapped-seat argument from a
capacity number into a revenue number, which is the version a finance director
acts on.

Forecasting it forward answers the question a plan cannot otherwise answer: if
we take this expansion, does revenue per seat go up or down?
"""

import numpy as np
import pandas as pd

RATE_COLS = ["site", "rate_per_seat"]


def validate(rates: pd.DataFrame):
    problems = []
    if rates is None or rates.empty:
        return ["No rate card supplied."]
    missing = [c for c in RATE_COLS if c not in rates.columns]
    if missing:
        return [f"Missing column(s): {', '.join(missing)}"]
    bad = pd.to_numeric(rates["rate_per_seat"], errors="coerce")
    if bad.isna().any():
        problems.append("Every rate_per_seat must be a number")
    elif (bad < 0).any():
        problems.append("A rate cannot be negative")
    if rates["site"].duplicated().any() and "account" not in rates.columns:
        problems.append("A site appears twice with no account column to tell them apart")
    return problems


def rate_lookup(rates: pd.DataFrame) -> dict:
    """{(site, account) or site: rate}. An account-specific rate wins over the
    site default, because a client's price is negotiated, not posted."""
    if rates is None or rates.empty:
        return {}
    out = {}
    for _, r in rates.iterrows():
        rate = float(pd.to_numeric(r["rate_per_seat"], errors="coerce") or 0)
        if "account" in rates.columns and str(r.get("account", "")).strip():
            out[(str(r["site"]), str(r["account"]))] = rate
        else:
            out[str(r["site"])] = rate
    return out


def rate_for(lookup: dict, site: str, account: str = None) -> float:
    if account is not None and (site, account) in lookup:
        return lookup[(site, account)]
    return lookup.get(site, 0.0)


# ────────────────────────────────────────── position today
def position(floors: pd.DataFrame, alloc: pd.DataFrame, rates: pd.DataFrame,
             currency: str = "£") -> pd.DataFrame:
    """Per site: what the seats earn, and what the whole building earns."""
    if floors is None or floors.empty:
        return pd.DataFrame()
    look = rate_lookup(rates)
    est = (floors.groupby("site", dropna=False)
           .agg(total_seats=("total_seats", "sum"),
                allocated=("allocated", "sum"),
                available=("available", "sum"),
                trapped=("trapped", "sum"))
           .reset_index())

    sold = None
    if alloc is not None and not alloc.empty:
        sold = alloc.groupby("site")["seats"].sum()
    est["seats_sold"] = (est["site"].map(sold).fillna(est["allocated"])
                         if sold is not None else est["allocated"]).astype(int)

    est["rate"] = est["site"].map(lambda s: rate_for(look, s))
    est["revenue"] = (est["seats_sold"] * est["rate"]).round(0)
    est["rev_per_seat_sold"] = np.where(est["seats_sold"] > 0,
                                        (est["revenue"] / est["seats_sold"]).round(2), 0)
    est["rev_per_seat_owned"] = np.where(est["total_seats"] > 0,
                                         (est["revenue"] / est["total_seats"]).round(2), 0)
    est["dilution"] = (est["rev_per_seat_sold"] - est["rev_per_seat_owned"]).round(2)
    est["earning_nothing"] = (est["total_seats"] - est["seats_sold"]).clip(lower=0).astype(int)
    est["cost_of_idle"] = (est["earning_nothing"] * est["rate"]).round(0)
    return est.sort_values("cost_of_idle", ascending=False)


def totals(position_table: pd.DataFrame, currency: str = "£") -> dict:
    if position_table is None or position_table.empty:
        return {}
    rev = float(position_table["revenue"].sum())
    seats = int(position_table["total_seats"].sum())
    sold = int(position_table["seats_sold"].sum())
    return {"revenue": rev,
            "seats_owned": seats,
            "seats_sold": sold,
            "per_seat_sold": round(rev / sold, 2) if sold else 0,
            "per_seat_owned": round(rev / seats, 2) if seats else 0,
            "idle_seats": seats - sold,
            "cost_of_idle": float(position_table["cost_of_idle"].sum()),
            "currency": currency}


# ────────────────────────────────────────── forecast
def forecast(demand: pd.DataFrame, floors: pd.DataFrame, rates: pd.DataFrame,
             periods: list = None) -> pd.DataFrame:
    """Revenue per seat across the horizon, on the seats the plan expects to sell.

    Capacity is held flat unless the floor inventory says otherwise, so a rising
    line means seats filling up and a falling one means the estate growing faster
    than the demand for it.
    """
    if demand is None or demand.empty or floors is None or floors.empty:
        return pd.DataFrame()
    look = rate_lookup(rates)
    owned = floors.groupby("site")["total_seats"].sum().to_dict()
    per = periods or sorted(demand["week"].unique())
    rows = []
    for p in per:
        d = demand[demand["week"] == p]
        rev = 0.0
        sold = 0
        for (site, acct), g in d.groupby(["Site", "Account"], dropna=False):
            n = int(g["seats"].sum())
            rev += n * rate_for(look, str(site), str(acct))
            sold += n
        total_owned = sum(owned.values())
        rows.append({"period": p, "seats_sold": sold, "seats_owned": total_owned,
                     "revenue": round(rev, 0),
                     "rev_per_seat_sold": round(rev / sold, 2) if sold else 0,
                     "rev_per_seat_owned": round(rev / total_owned, 2) if total_owned else 0,
                     "occupancy_%": round(sold / total_owned * 100, 1) if total_owned else 0})
    return pd.DataFrame(rows)


def expansion_effect(fc: pd.DataFrame, extra_seats: int, extra_revenue: float = 0.0) -> dict:
    """What taking more space does to revenue per seat.

    Adding seats without adding revenue always dilutes. Saying so plainly is
    more use than a capacity number, because it is the trade the business is
    actually making.
    """
    if fc is None or fc.empty:
        return {}
    last = fc.iloc[-1]
    owned_after = last["seats_owned"] + extra_seats
    rev_after = last["revenue"] + extra_revenue
    before = last["rev_per_seat_owned"]
    after = round(rev_after / owned_after, 2) if owned_after else 0
    return {"before": before, "after": after, "change": round(after - before, 2),
            "verdict": ("Revenue per seat improves" if after > before
                        else "Revenue per seat is diluted" if after < before
                        else "No change to revenue per seat"),
            "extra_seats": extra_seats, "extra_revenue": extra_revenue}


def recovery_value(floors: pd.DataFrame, rates: pd.DataFrame,
                   releasable_reasons: list = None) -> dict:
    """What the unusable seats would be worth if they could be sold.

    This is the trapped-seat argument in the currency a finance director uses.
    """
    if floors is None or floors.empty:
        return {}
    look = rate_lookup(rates)
    f = floors.copy()
    if releasable_reasons and "trapped_reason" in f.columns:
        f = f[f["trapped_reason"].isin(releasable_reasons)]
    val = 0.0
    for site, g in f.groupby("site"):
        val += float(g["trapped"].sum()) * rate_for(look, str(site))
    return {"seats": int(f["trapped"].sum()), "value": round(val, 0)}
