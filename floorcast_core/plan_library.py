"""
Floorcast — Plan Library

Holding one floor plan answers "who sits where on this floor". Holding every
plan for a site answers a better question: **given what this site owes each
client, which floors should each of them get?**

That is a different problem, and a more useful one. Segregation is naturally a
floor-level concern — a client wanting its own space usually means its own
floor, not a corner of somebody else's. Allocating a site's demand across all
its surveyed floors at once lets the tool give whole floors where it can, and
say plainly where it cannot.

Plans are keyed by site, building and floor. The key is read from the filename
where it follows the usual conventions, then from the drawing's own title block,
and only then asked for.
"""

import re

import numpy as np
import pandas as pd

FLOOR_PAT = re.compile(r"\b(\d{1,2}\s?F|F\s?\d{1,2}|L\d{1,2}|LEVEL\s?\d{1,2}|GF|MEZZ)\b", re.I)
SITE_PAT = re.compile(r"\b([A-Z]{2,4}-[A-Z]{2,4}-\d{1,3})\b")


def key_from_name(filename: str) -> dict:
    """Best guess at which floor a file describes, from its name."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", str(filename or ""))
    site = SITE_PAT.search(stem.upper())
    floor = FLOOR_PAT.search(stem.upper())
    return {"site": site.group(1) if site else "",
            "floor": _norm_floor(floor.group(1)) if floor else ""}


def key_from_plan(read_result: dict) -> dict:
    """Fall back to the title block — most drawings name themselves."""
    labels = read_result.get("zone_labels")
    if labels is None or getattr(labels, "empty", True):
        return {"site": "", "floor": ""}
    text = " ".join(str(t) for t in labels.get("text", []))
    site = SITE_PAT.search(text.upper())
    floor = FLOOR_PAT.search(text.upper())
    return {"site": site.group(1) if site else "",
            "floor": _norm_floor(floor.group(1)) if floor else ""}


def _norm_floor(raw: str) -> str:
    s = str(raw).upper().replace(" ", "")
    if s in ("GF", "MEZZ"):
        return s
    n = re.sub(r"\D", "", s)
    return f"{int(n):02d}F" if n else s


def identify(filename: str, read_result: dict) -> dict:
    """Filename first, drawing second, blank third — the app asks for blanks."""
    a = key_from_name(filename)
    b = key_from_plan(read_result)
    return {"site": a["site"] or b["site"], "floor": a["floor"] or b["floor"],
            "source": "filename" if a["site"] else ("drawing" if b["site"] else "unknown")}


# ────────────────────────────────────────────── the library
def combine(plans: dict) -> pd.DataFrame:
    """One seat table across every loaded plan.

    plans: {(site, building, floor): {"seats": df, "background":..., "extent":...}}
    """
    frames = []
    for (site, building, floor), p in plans.items():
        s = p["seats"].copy()
        s["site"], s["building"], s["floor"] = site, building, floor
        s["floor_id"] = f"{site} / {building} / {floor}"
        # seat ids must be unique across the site, not just the floor
        s["seat_id"] = floor + "-" + s["seat_id"].astype(str)
        frames.append(s)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    for c, v in [("country", "-"), ("city", "-"), ("tower", "T1")]:
        if c not in out.columns:
            out[c] = v
    return out


def inventory(plans: dict) -> pd.DataFrame:
    """What has been surveyed, and how it compares with the floor inventory."""
    rows = []
    for (site, building, floor), p in plans.items():
        s = p["seats"]
        prod = int((s["zone_type"].astype(str).str.lower() == "production").sum())
        trapped = int(s.get("seat_status", pd.Series(dtype=str)).eq("trapped").sum())
        rows.append({"site": site, "building": building, "floor": floor,
                     "seats_on_plan": len(s), "production": prod,
                     "support": len(s) - prod, "unusable": trapped,
                     "zones": s["zone"].nunique()})
    return pd.DataFrame(rows).sort_values(["site", "floor"]) if rows else pd.DataFrame()


def reconcile(plans: dict, floors: pd.DataFrame) -> pd.DataFrame:
    """Does each drawing agree with the inventory row for that floor?

    A mismatch is worth surfacing rather than smoothing over — it usually means
    the drawing is out of date, which is a real finding.
    """
    inv = inventory(plans)
    if inv.empty or floors is None or floors.empty:
        return inv
    f = floors[["site", "building", "floor", "total_seats"]].copy()
    m = inv.merge(f, on=["site", "building", "floor"], how="left")
    m["inventory_seats"] = m["total_seats"]
    m["difference"] = m["seats_on_plan"] - m["inventory_seats"]
    m["agrees"] = np.where(m["inventory_seats"].isna(), "no inventory row",
                           np.where(m["difference"] == 0, "yes", "no"))
    return m.drop(columns=["total_seats"])


def surveyed_sites(plans: dict) -> list:
    return sorted({site for (site, _, _) in plans})


def floors_for(plans: dict, site: str) -> list:
    return sorted(floor for (s, _, floor) in plans if s == site)


# ────────────────────────────────────────────── site-wide allocation
def allocate_site(seat_forecast: pd.DataFrame, seats: pd.DataFrame,
                  allocatable_zones=None, rules=None):
    """Place a site's demand across every surveyed floor at that site.

    The seat engine already treats each floor as its own container, so the work
    here is ordering: the largest account is offered the emptiest floor first,
    which pushes towards whole floors per client rather than everybody sharing
    everything. Where that is not possible the engine still fills, and the
    segregation report says which floors ended up shared.
    """
    from . import seatmap_engine as sm

    if seats.empty or seat_forecast.empty:
        return seats, pd.DataFrame(), pd.DataFrame()

    s = seats.copy()
    if "zone_type" not in s.columns:
        s["zone_type"] = "Production"
    if allocatable_zones is None:
        allocatable_zones = sorted(
            s.loc[s["zone_type"].astype(str).str.lower() == "production", "zone"].unique())

    fc = seat_forecast.copy()
    fc["site"] = s["site"].iloc[0]
    fc["building"] = s["building"].iloc[0]
    fc["tower"] = s.get("tower", pd.Series("T1", index=s.index)).iloc[0]
    fc["floor"] = None
    return sm.allocate_seats(fc, s, allocatable_zones=allocatable_zones)


def floor_summary(assigned: pd.DataFrame) -> pd.DataFrame:
    """Per floor: who is on it, how full it is, and whether it is shared."""
    if assigned.empty:
        return pd.DataFrame()
    a = assigned.copy()
    a["used"] = a["account"].notna().astype(int)
    g = (a.groupby(["site", "building", "floor"], dropna=False)
         .agg(seats=("seat_id", "count"), allocated=("used", "sum"),
              accounts=("account", lambda x: ", ".join(sorted(set(x.dropna())))),
              n_accounts=("account", lambda x: x.dropna().nunique()))
         .reset_index())
    g["empty"] = g["seats"] - g["allocated"]
    g["full_%"] = (g["allocated"] / g["seats"] * 100).round(1)
    g["shared"] = np.where(g["n_accounts"] > 1, "yes", "no")
    return g.sort_values(["site", "floor"])


def account_spread(assigned: pd.DataFrame) -> pd.DataFrame:
    """How many floors each account ended up on. One is ideal; more is a
    conversation about whether the client will accept being split."""
    if assigned.empty:
        return pd.DataFrame()
    a = assigned[assigned["account"].notna()]
    if a.empty:
        return pd.DataFrame()
    g = (a.groupby("account")
         .agg(seats=("seat_id", "count"), floors=("floor", "nunique"),
              on=("floor", lambda x: ", ".join(sorted(set(x)))))
         .reset_index())
    g["verdict"] = np.where(g["floors"] == 1, "Whole floor to itself",
                            "Split across floors")
    return g.sort_values("seats", ascending=False)
