"""
Floorcast — Role Views

Seat planning is a shared problem with unshared responsibilities. Operations
wants to know whether its teams have seats. Security wants to know whether two
clients are about to share a floor. IT wants the provisioning queue. Nobody
wants the other six people's screens.

Same data, one source of truth, eight framings — each ending in the step that
role would actually take. A view that does not end in an action is a report, and
the estate already has plenty of those.
"""

import numpy as np
import pandas as pd

ROLES = {
    "Leadership": {
        "icon": "📊",
        "question": "Is the estate healthy, and what is it costing us?",
        "cares": "Occupancy, capital tied up in unusable seats, whether the plan is credible",
        "action": "Decide on renovation spend, new space, or holding position",
    },
    "Operations": {
        "icon": "🎧",
        "question": "Will my teams have seats when they ramp?",
        "cares": "Seats required against seats held, by site and account, month by month",
        "action": "Escalate a shortfall early, or confirm a ramp can be committed",
    },
    "Facility": {
        "icon": "🏗",
        "question": "What needs building, fixing or reconfiguring, and by when?",
        "cares": "Renovation pipeline and lead times, underused floors, partition work",
        "action": "Schedule the renovation or the move, and give a realistic date",
    },
    "Security": {
        "icon": "🔐",
        "question": "Is client segregation intact?",
        "cares": "Shared zones, dedicated floors, accounts that must not sit together",
        "action": "Clear the plan, or block it before it reaches a client audit",
    },
    "WFM": {
        "icon": "📈",
        "question": "Are the assumptions behind the seat numbers sound?",
        "cares": "Seat sharing ratios, shrinkage, outliers against the geo norm",
        "action": "Challenge a ratio, or recover seats a programme is over-holding",
    },
    "IT": {
        "icon": "🖥",
        "question": "What needs provisioning, and where?",
        "cares": "Seats unusable for want of IT, and the build work a move would create",
        "action": "Queue the provisioning, sized and located",
    },
    "Client": {
        "icon": "🤝",
        "question": "Where does my programme sit, and can it grow?",
        "cares": "Their own footprint, headroom beside it, segregation assurance",
        "action": "Confirm the footprint, or ask for the growth to be reserved",
    },
    "PMO": {
        "icon": "📋",
        "question": "Is the plan complete, and who is holding it up?",
        "cares": "Collection status against the due date, and data that does not add up",
        "action": "Chase the named owners, with the list generated rather than maintained",
    },
}


# ────────────────────────────────────────── per-role figures
def leadership(floors, totals, comp=None, issues=None) -> dict:
    trapped_pct = totals.get("trapped_%", 0)
    out = {
        "Total seats": f"{totals['total_seats']:,}",
        "Occupancy": f"{totals['occupancy_%']}%",
        "Unusable seats": f"{totals['trapped']:,}",
        "Share of estate unusable": f"{trapped_pct}%",
    }
    if comp is not None and not comp.empty:
        done = int(comp["updated"].sum()); tot = int(comp["programmes"].sum())
        out["Plan collected"] = f"{done / max(tot, 1) * 100:.0f}%"
    return out


def operations(sites: pd.DataFrame) -> pd.DataFrame:
    """Per site: what is needed, what is usable, and whether it lands."""
    if sites.empty:
        return sites
    d = sites[["site", "incremental_need", "usable", "gap", "status"]].copy()
    d = d.rename(columns={"incremental_need": "seats needed", "usable": "seats usable",
                          "gap": "spare or short"})
    return d.sort_values("spare or short")


def facility(floors: pd.DataFrame) -> pd.DataFrame:
    """Where the building work is, and what it would unlock."""
    d = floors.copy()
    d["idle_%"] = np.where(d["total_seats"] > 0,
                           (d["available"] / d["total_seats"] * 100).round(1), 0)
    keep = ["site", "building", "floor", "total_seats", "available", "idle_%",
            "trapped", "trapped_reason", "expansion_space", "expansion_eta_weeks", "notes"]
    d = d[[c for c in keep if c in d.columns]]
    d = d[(d["expansion_space"] > 0) | (d["trapped"] > 0) | (d["idle_%"] > 25)]
    return d.sort_values(["expansion_space", "trapped"], ascending=False)


def security(seg: pd.DataFrame, rules_summary: pd.DataFrame, floors: pd.DataFrame) -> dict:
    return {
        "shared_zones": seg if seg is not None else pd.DataFrame(),
        "rules": rules_summary if rules_summary is not None else pd.DataFrame(),
        "segregation_trapped": int(
            floors.loc[floors["trapped_reason"].eq("segregation"), "trapped"].sum())
        if "trapped_reason" in floors.columns else 0,
    }


def wfm(long: pd.DataFrame, ceiling: float = 2.6) -> dict:
    """Ratio and shrinkage behaviour, and who sits outside the geo norm."""
    if long is None or long.empty:
        return {}
    d = long.copy()
    d["ssr"] = d["ssr_onsite"].where(d["ssr_onsite"].fillna(0) > 0, d["ssr_combined"])
    d = d[d["ssr"].fillna(0) > 0]
    if d.empty:
        return {}
    by_geo = (d.groupby("geo")["ssr"].agg(programmes="count", median="median",
                                          mean="mean", max="max").reset_index())
    by_geo[["median", "mean", "max"]] = by_geo[["median", "mean", "max"]].round(2)

    d = d[d["onsite_tms"].fillna(0) > 0]
    if d.empty:
        return {}
    latest = d.sort_values("period").groupby(["geo", "account"]).tail(1)
    norm = latest.groupby("geo")["ssr"].median().rename("geo_median")
    j = latest.merge(norm, on="geo", how="left")
    j["vs_geo"] = (j["ssr"] - j["geo_median"]).round(2)
    low = j[(j["vs_geo"] < -0.3) & (j["onsite_tms"].fillna(0) > 0)][
        ["geo", "account", "ssr", "geo_median", "vs_geo", "onsite_tms"]].copy()
    low["seats_if_at_norm"] = (low["onsite_tms"] / low["geo_median"]).round(0)
    low["seats_now"] = (low["onsite_tms"] / low["ssr"]).round(0)
    low["potential_saving"] = (low["seats_now"] - low["seats_if_at_norm"]).clip(lower=0)
    return {
        "by_geo": by_geo.sort_values("median"),
        "below_norm": low.sort_values("potential_saving", ascending=False).head(25),
        "above_ceiling": j[j["ssr"] > ceiling][["geo", "account", "ssr", "onsite_tms"]]
        .assign(verdict=lambda x: np.where(x["ssr"] > 4,
                                           "Implausible — check the entry",
                                           "High, worth confirming"))
        .sort_values("ssr", ascending=False).head(15),
        "total_saving": int(low["potential_saving"].sum()) if not low.empty else 0,
    }


def it(floors: pd.DataFrame) -> dict:
    """Seats waiting on IT, and where they are."""
    if "trapped_reason" not in floors.columns:
        return {"queue": pd.DataFrame(), "seats": 0}
    q = floors[floors["trapped_reason"].eq("it_not_ready")]
    keep = ["site", "building", "floor", "trapped", "notes"]
    q = q[[c for c in keep if c in q.columns]].rename(columns={"trapped": "seats to provision"})
    return {"queue": q.sort_values("seats to provision", ascending=False),
            "seats": int(q["seats to provision"].sum()) if not q.empty else 0}


def client(alloc: pd.DataFrame, floors: pd.DataFrame, account: str,
           demand: pd.DataFrame = None, period: str = None) -> dict:
    """One account's footprint, and whether it can grow where it already sits.

    A client asks two things: where are we, and is there room. Answering only
    the first turns this into a report. The headroom and the growth check are
    what make it a conversation.
    """
    if alloc is None or alloc.empty:
        return {}
    mine = alloc[alloc["account"] == account]
    if mine.empty:
        return {}
    my_floors = set(mine["floor_id"])
    others = alloc[alloc["floor_id"].isin(my_floors) & (alloc["account"] != account)]
    headroom = int(alloc[alloc["floor_id"].isin(my_floors)]
                   .groupby("floor_id")["floor_free"].first().sum())
    held = int(mine["seats"].sum())

    # can they grow where they already are?
    need, growth, verdict = None, None, ""
    if demand is not None and not demand.empty and period:
        d = demand[(demand["Account"] == account) & (demand["week"] == period)]
        if not d.empty:
            need = int(d["seats"].sum())
            growth = need - held
            if growth <= 0:
                verdict = "Holds more seats than the plan calls for."
            elif growth <= headroom:
                verdict = (f"Needs {growth} more seats; {headroom} are free beside them, "
                           "so the growth fits without a move.")
            else:
                verdict = (f"Needs {growth} more seats but only {headroom} are free "
                           f"beside them — {growth - headroom} would land elsewhere "
                           "unless space is freed.")

    by_site = (mine.groupby("site")
               .agg(seats=("seats", "sum"), floors=("floor_id", "nunique"))
               .reset_index().sort_values("seats", ascending=False))

    return {
        "seats": held,
        "floors": mine["floor_id"].nunique(),
        "sites": mine["site"].nunique(),
        "shares_with": sorted(others["account"].unique().tolist()),
        "headroom": headroom,
        "need": need,
        "growth": growth,
        "verdict": verdict,
        "by_site": by_site,
        "detail": mine[["site", "building", "floor", "lob", "seats"]]
        .sort_values("seats", ascending=False),
    }


def pmo(comp: pd.DataFrame, chase: pd.DataFrame, issues: pd.DataFrame) -> dict:
    return {
        "completion": comp if comp is not None else pd.DataFrame(),
        "chase": chase if chase is not None else pd.DataFrame(),
        "high_issues": int((issues["severity"] == "High").sum()) if issues is not None
        and not issues.empty else 0,
        "programmes_flagged": int(issues["programme"].nunique()) if issues is not None
        and not issues.empty else 0,
    }
