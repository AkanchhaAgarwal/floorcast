"""
Floorcast — Estate Model

The planner does not work one floor at a time. He works a portfolio: geo → site →
floor, and a seat is in one of four states, not two.

    allocated   assigned to a program
    available   free and usable today
    trapped     physically present but not usable
    expansion   not built yet, unlocked by a renovation with a lead time

Trapped seats are the single biggest number in the estate — roughly a quarter of
it — so they are modelled explicitly with a reason code rather than folded into
"available". What exactly makes a seat trapped is a business definition that
differs by site, so this module never infers it: it is an input, and the reason
code drives whether the tool treats it as releasable.

Occupancy is allocated / total, matching the planner's overview sheet.
"""

import numpy as np
import pandas as pd

FLOOR_COLS = ["geo", "country", "city", "site", "building", "floor",
              "total_seats", "allocated", "available", "trapped"]

# reason codes and whether releasing them is a planning lever
TRAPPED_REASONS = {
    "segregation": "Stranded inside another client's secure zone",
    "layout": "Block too small or awkward to allocate",
    "it_not_ready": "No IT provisioning (monitor, softphone, thin client)",
    "under_renovation": "Out of service while the floor is being worked on",
    "contractual_hold": "Held under contract by a client but unoccupied",
    "condition": "Damaged or unusable furniture",
    "other": "Not classified",
}
# what a partition move or a relocation can realistically free up
RELEASABLE_BY_DEFAULT = ["segregation", "layout", "it_not_ready", "under_renovation"]


# ────────────────────────────────────────────── validation
def validate_floors(df: pd.DataFrame):
    problems = []
    missing = [c for c in FLOOR_COLS if c not in df.columns]
    if missing:
        problems.append(f"Missing column(s): {', '.join(missing)}")
        return problems
    for c in ("total_seats", "allocated", "available", "trapped"):
        if not pd.api.types.is_numeric_dtype(df[c]):
            problems.append(f"Column {c} must be numeric")
    if problems:
        return problems
    bad = df[df["allocated"] + df["available"] > df["total_seats"] + 0.5]
    if not bad.empty:
        problems.append(
            f"{len(bad)} floor(s) where allocated + available exceeds total seats "
            f"(e.g. {bad.iloc[0]['site']} {bad.iloc[0]['floor']}) — check the source.")
    if (df["trapped"] > df["total_seats"]).any():
        problems.append("Some floors report more trapped seats than total seats.")
    return problems


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Adds derived columns used everywhere else."""
    d = df.copy()
    for c in ("expansion_space", "expansion_eta_weeks"):
        if c not in d.columns:
            d[c] = 0
    d["expansion_space"] = pd.to_numeric(d["expansion_space"], errors="coerce").fillna(0)
    d["expansion_eta_weeks"] = pd.to_numeric(d["expansion_eta_weeks"], errors="coerce")
    if "trapped_reason" not in d.columns:
        d["trapped_reason"] = "other"
    d["trapped_reason"] = d["trapped_reason"].fillna("other")
    if "floor_id" not in d.columns:
        d["floor_id"] = (d["site"].astype(str) + " / " + d["building"].astype(str)
                         + " / " + d["floor"].astype(str))
    d["usable_now"] = d["allocated"] + d["available"]
    d["occupancy_%"] = np.where(d["total_seats"] > 0,
                                (d["allocated"] / d["total_seats"] * 100).round(2), 0)
    d["trapped_%"] = np.where(d["total_seats"] > 0,
                              (d["trapped"] / d["total_seats"] * 100).round(2), 0)
    return d


# ────────────────────────────────────────────── rollups
def rollup(d: pd.DataFrame, level="site") -> pd.DataFrame:
    """level: 'geo', 'country', 'city', 'site' or 'floor_id'."""
    if level == "floor_id":
        keys = ["geo", "site", "building", "floor", "floor_id"]
    else:
        order = ["geo", "country", "city", "site"]
        keys = order[:order.index(level) + 1] if level in order else [level]
    g = (d.groupby(keys, dropna=False)
         .agg(total_seats=("total_seats", "sum"),
              allocated=("allocated", "sum"),
              available=("available", "sum"),
              trapped=("trapped", "sum"),
              expansion_space=("expansion_space", "sum"))
         .reset_index())
    g["occupancy_%"] = np.where(g["total_seats"] > 0,
                                (g["allocated"] / g["total_seats"] * 100).round(2), 0)
    g["trapped_%"] = np.where(g["total_seats"] > 0,
                              (g["trapped"] / g["total_seats"] * 100).round(2), 0)
    return g.sort_values("total_seats", ascending=False)


def with_utilisation(rolled: pd.DataFrame, demand: pd.DataFrame, period: str,
                     level: str = "site") -> pd.DataFrame:
    """Add utilisation alongside occupancy.

    They answer different questions and are easy to conflate. **Occupancy** is
    how much of the building has been handed out. **Utilisation** is how much of
    what was handed out is genuinely needed. A site can be fully occupied and
    poorly utilised — every seat assigned, half of them not required — and that
    is exactly the case worth finding.
    """
    if rolled is None or rolled.empty or demand is None or demand.empty:
        return rolled
    if "Site" not in demand.columns or level not in rolled.columns:
        return rolled
    need = demand[demand["week"] == period].groupby("Site")["seats"].sum()
    out = rolled.copy()
    if level == "site":
        out["required"] = out[level].map(need).fillna(0).astype(int)
    else:
        return out
    out["utilisation_%"] = np.where(out["allocated"] > 0,
                                    (out["required"] / out["allocated"] * 100).round(1), np.nan)
    out["verdict"] = np.where(out["utilisation_%"].isna(), "",
                              np.where(out["utilisation_%"] < 80, "Holding more than needed",
                                       np.where(out["utilisation_%"] > 105, "Needs more space",
                                                "About right")))
    return out


def estate_totals(d: pd.DataFrame) -> dict:
    t = int(d["total_seats"].sum())
    a = int(d["allocated"].sum())
    return {"total_seats": t, "allocated": a,
            "available": int(d["available"].sum()),
            "trapped": int(d["trapped"].sum()),
            "expansion_space": int(d["expansion_space"].sum()),
            "occupancy_%": round(a / t * 100, 2) if t else 0.0,
            "trapped_%": round(int(d["trapped"].sum()) / t * 100, 2) if t else 0.0,
            "floors": len(d), "sites": d["site"].nunique()}


# ────────────────────────────────────────────── trapped seats
def trapped_breakdown(d: pd.DataFrame) -> pd.DataFrame:
    g = (d.groupby("trapped_reason")
         .agg(seats=("trapped", "sum"), floors=("floor_id", "nunique")).reset_index())
    g["releasable"] = g["trapped_reason"].isin(RELEASABLE_BY_DEFAULT)
    g["definition"] = g["trapped_reason"].map(TRAPPED_REASONS)
    tot = g["seats"].sum()
    g["share_%"] = (g["seats"] / tot * 100).round(1) if tot else 0
    return g.sort_values("seats", ascending=False)


def release_trapped(d: pd.DataFrame, reasons, fraction=1.0) -> pd.DataFrame:
    """Simulate freeing trapped seats — a partition move, an IT rollout, a
    relocation. Released seats become available."""
    out = d.copy()
    hit = out["trapped_reason"].isin(reasons)
    freed = (out.loc[hit, "trapped"] * float(fraction)).round().astype(int)
    out.loc[hit, "available"] = out.loc[hit, "available"] + freed
    out.loc[hit, "trapped"] = out.loc[hit, "trapped"] - freed
    out["released"] = 0
    out.loc[hit, "released"] = freed
    return prepare(out)


# ────────────────────────────────────────────── ramp matching
def match_ramp(d: pd.DataFrame, need_by_site: dict, include_expansion=True,
               within_weeks=None) -> pd.DataFrame:
    """Reproduces the planner's Ramp plan: need against available by floor,
    and the Delta left over.

    need_by_site: {site: seats needed}
    include_expansion: also count renovation capacity, respecting its lead time.
    """
    rows = []
    for site, need in need_by_site.items():
        floors = d[d["site"] == site].sort_values("available", ascending=False)
        remaining = int(round(need))
        for _, f in floors.iterrows():
            take = int(min(remaining, f["available"]))
            exp_take = 0
            eta = f["expansion_eta_weeks"]
            eta_ok = (within_weeks is None or (pd.notna(eta) and eta <= within_weeks))
            if include_expansion and remaining - take > 0 and f["expansion_space"] > 0 and eta_ok:
                exp_take = int(min(remaining - take, f["expansion_space"]))
            if take or exp_take:
                rows.append({
                    "site": site, "floor_id": f["floor_id"],
                    "total_seats": int(f["total_seats"]),
                    "utilised": int(f["allocated"]),
                    "available": int(f["available"]),
                    "expansion_space": int(f["expansion_space"]),
                    "expansion_eta_weeks": eta,
                    "from_available": take,
                    "from_expansion": exp_take,
                    "trapped": int(f["trapped"]),
                    "notes": f.get("notes", "")})
                remaining -= (take + exp_take)
            if remaining <= 0:
                break
        rows.append({"site": site, "floor_id": "— SITE TOTAL —",
                     "ramp_need": int(round(need)),
                     "placed": int(round(need)) - max(remaining, 0),
                     "delta": -max(remaining, 0)})
    return pd.DataFrame(rows)


def ramp_summary(d: pd.DataFrame, need_by_site: dict, **kw) -> pd.DataFrame:
    """One row per site: need, what fits today, what a renovation would add,
    and the Delta that remains."""
    rows = []
    for site, need in need_by_site.items():
        f = d[d["site"] == site]
        avail = int(f["available"].sum())
        exp = int(f["expansion_space"].sum())
        eta = f.loc[f["expansion_space"] > 0, "expansion_eta_weeks"]
        need = int(round(need))
        from_av = min(need, avail)
        from_exp = min(max(need - avail, 0), exp) if kw.get("include_expansion", True) else 0
        rows.append({"site": site, "ramp_need": need,
                     "available_today": avail,
                     "from_available": from_av,
                     "expansion_space": exp,
                     "from_expansion": from_exp,
                     "expansion_eta_weeks": (float(eta.max()) if len(eta) else np.nan),
                     "trapped_on_site": int(f["trapped"].sum()),
                     "delta": -(need - from_av - from_exp)})
    out = pd.DataFrame(rows)
    return out.sort_values("delta")


# ────────────────────────────────────────────── contiguous fitting
def fit_blocks(seats: pd.DataFrame, need: int, zone_col="zone"):
    """On a surveyed floor, find contiguous blocks that satisfy a requirement —
    the drag-select-and-count step, done by the tool.

    Returns (chosen blocks, seats still unplaced).
    """
    free = seats[seats.get("account").isna()] if "account" in seats.columns else seats
    if free.empty:
        return pd.DataFrame(), need
    blocks = (free.groupby(zone_col).size().sort_values(ascending=False)
              .reset_index(name="seats"))
    chosen, remaining = [], int(need)
    # whole blocks first, largest that fits, so a ramp lands in as few pieces as possible
    pool = dict(zip(blocks[zone_col], blocks["seats"]))
    while remaining > 0 and any(v > 0 for v in pool.values()):
        fits = {k: v for k, v in pool.items() if 0 < v <= remaining}
        if fits:
            k = max(fits, key=fits.get)
            chosen.append({zone_col: k, "seats": int(pool[k]), "whole_block": True})
            remaining -= pool[k]; pool[k] = 0
        else:
            k = min({k: v for k, v in pool.items() if v > 0}, key=lambda z: pool[z])
            chosen.append({zone_col: k, "seats": int(remaining), "whole_block": False})
            pool[k] -= remaining; remaining = 0
    return pd.DataFrame(chosen), max(remaining, 0)
