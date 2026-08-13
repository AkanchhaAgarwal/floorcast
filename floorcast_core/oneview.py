"""
Floorcast — One Source Browser

Everything in one table, filterable the way a planner already works.

The tabs answer specific questions well, but a planner also wants to sit inside
a geography and see the lot — floors, who is on them, what is needed, what is
unusable and why — without hopping between views. That is what a spreadsheet is
good at, and the reason people keep going back to one.

So: one wide table, cascading filters, and a column set that can be cut down to
what is being looked at. No pivoting, no formulas, and it exports to Excel
because that is where the next question usually gets asked.
"""

import numpy as np
import pandas as pd

# columns in the order a planner reads them
COLUMN_GROUPS = {
    "Where": ["geo", "country", "city", "site", "building", "floor"],
    "Capacity": ["total_seats", "allocated", "available", "occupancy_%", "utilisation_%"],
    "Unusable": ["trapped", "trapped_%", "trapped_reason", "trapped_owner", "trapped_notes"],
    "Coming": ["expansion_space", "expansion_status", "expansion_started",
               "expansion_expected"],
    "Who": ["programmes", "accounts_on_floor", "seats_allocated_detail"],
    "Demand": ["required_this_period", "incremental_need"],
    "Notes": ["notes"],
}


def build(floors: pd.DataFrame, alloc: pd.DataFrame = None,
          demand: pd.DataFrame = None, period: str = None) -> pd.DataFrame:
    """One row per floor, with everything known about it attached."""
    if floors is None or floors.empty:
        return pd.DataFrame()
    d = floors.copy()

    for c in ("trapped_notes", "trapped_owner", "expansion_status",
              "expansion_started", "expansion_expected", "notes", "programmes"):
        if c not in d.columns:
            d[c] = ""

    d["occupancy_%"] = np.where(d["total_seats"] > 0,
                                (d["allocated"] / d["total_seats"] * 100).round(1), 0)
    d["trapped_%"] = np.where(d["total_seats"] > 0,
                              (d["trapped"] / d["total_seats"] * 100).round(1), 0)

    if alloc is not None and not alloc.empty:
        a = alloc.copy()
        g = (a.groupby(["site", "building", "floor"], dropna=False)
             .agg(accounts_on_floor=("account", lambda x: ", ".join(sorted(set(x)))),
                  seats_allocated_detail=("seats", "sum"))
             .reset_index())
        d = d.merge(g, on=["site", "building", "floor"], how="left")
    else:
        d["accounts_on_floor"] = ""
        d["seats_allocated_detail"] = np.nan

    # utilisation: of the space this site has been given, how much is genuinely
    # needed. Occupancy says how much of the building is handed out; utilisation
    # says whether the people handed it actually need it.
    if demand is not None and not demand.empty and period:
        need = (demand[demand["week"] == period].groupby("Site")["seats"].sum()
                if "Site" in demand.columns else pd.Series(dtype=float))
        site_alloc = d.groupby("site")["allocated"].sum()
        util = (need.reindex(site_alloc.index) / site_alloc.replace(0, np.nan) * 100).round(1)
        d["utilisation_%"] = d["site"].map(util)
        d["required_this_period"] = d["site"].map(need).fillna(0).astype(int)
        d["incremental_need"] = (d["required_this_period"]
                                 - d.groupby("site")["allocated"].transform("sum")).clip(lower=0)
    else:
        d["utilisation_%"] = np.nan
        d["required_this_period"] = np.nan
        d["incremental_need"] = np.nan

    ordered = [c for grp in COLUMN_GROUPS.values() for c in grp if c in d.columns]
    rest = [c for c in d.columns if c not in ordered]
    return d[ordered + rest]


def filter_options(table: pd.DataFrame, column: str, current: dict = None) -> list:
    """Values still available for a column once the other filters are applied,
    so the filters cascade rather than offering dead ends."""
    if table is None or table.empty or column not in table.columns:
        return []
    d = table
    for k, v in (current or {}).items():
        if k != column and v and k in d.columns:
            d = d[d[k].isin(v)]
    return sorted(x for x in d[column].dropna().unique().tolist() if str(x) != "")


def apply_filters(table: pd.DataFrame, selections: dict) -> pd.DataFrame:
    if table is None or table.empty:
        return table
    d = table
    for col, vals in (selections or {}).items():
        if vals and col in d.columns:
            d = d[d[col].isin(vals)]
    return d


def summarise(table: pd.DataFrame) -> dict:
    """Totals for whatever is currently on screen — the number a planner reads
    off the bottom of a filtered spreadsheet."""
    if table is None or table.empty:
        return {}
    tot = int(table["total_seats"].sum()) if "total_seats" in table else 0
    alloc = int(table["allocated"].sum()) if "allocated" in table else 0
    return {
        "floors": len(table),
        "sites": table["site"].nunique() if "site" in table else 0,
        "total_seats": tot,
        "allocated": alloc,
        "available": int(table["available"].sum()) if "available" in table else 0,
        "trapped": int(table["trapped"].sum()) if "trapped" in table else 0,
        "occupancy_%": round(alloc / tot * 100, 1) if tot else 0,
        "expansion_space": int(table["expansion_space"].sum())
        if "expansion_space" in table else 0,
    }
