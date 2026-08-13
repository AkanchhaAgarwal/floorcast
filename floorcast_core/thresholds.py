"""
Floorcast — Thresholds and Status

Two things the planning process turns on that raw occupancy does not capture.

**An alert level depends on the size of the region.** A large estate can run hot
because there is always somewhere to flex to; a small one cannot. So the
threshold is not a single number — the Philippines at sixteen to eighteen
thousand seats does not go red until occupancy passes 96%, while South Africa at
around two and a half thousand goes red at 90%, because it has far less room to
absorb a surprise.

**Trapped seats have to be justified, not merely reported.** There is a limit on
how many the organisation will carry, and the monthly report to leadership has
to defend the ones being held. A number with no reason attached is a question
waiting to be asked in the meeting.

Status language matches what leadership already reads: healthy, strained,
critical.
"""

import numpy as np
import pandas as pd

# Defaults derived from how the thresholds actually behave: the smaller the
# estate, the earlier it goes red, because there is less room to flex.
DEFAULT_BANDS = [
    # min seats, max seats, strained above, critical above
    (0,      1000,  80.0, 85.0),
    (1000,   5000,  85.0, 90.0),
    (5000,  10000,  88.0, 93.0),
    (10000, 10**9,  92.0, 96.0),
]

STATUS_ORDER = {"Critical": 0, "Strained": 1, "Healthy": 2}
STATUS_COLOUR = {"Critical": "#B3261E", "Strained": "#C9A227", "Healthy": "#1E7A46"}

# how much of a region may sit unusable before it needs defending
TRAPPED_BUDGET_PCT = 10.0


def thresholds_for(total_seats: float, bands=None) -> tuple:
    """(strained_above, critical_above) for an estate of this size."""
    for lo, hi, strained, critical in (bands or DEFAULT_BANDS):
        if lo <= total_seats < hi:
            return strained, critical
    return 90.0, 95.0


def status_for(occupancy_pct: float, total_seats: float, bands=None) -> str:
    strained, critical = thresholds_for(total_seats, bands)
    if occupancy_pct >= critical:
        return "Critical"
    if occupancy_pct >= strained:
        return "Strained"
    return "Healthy"


def rag(floors: pd.DataFrame, level: str = "geo", bands=None,
        overrides: dict = None) -> pd.DataFrame:
    """Occupancy against the threshold for each region's own size.

    overrides: {region: (strained_above, critical_above)} where the business has
    set its own numbers — those always win over the size-derived default.
    """
    if floors is None or floors.empty or level not in floors.columns:
        return pd.DataFrame()
    g = (floors.groupby(level, dropna=False)
         .agg(total_seats=("total_seats", "sum"),
              allocated=("allocated", "sum"),
              available=("available", "sum"),
              trapped=("trapped", "sum"))
         .reset_index())
    g["occupancy_%"] = np.where(g["total_seats"] > 0,
                                (g["allocated"] / g["total_seats"] * 100).round(2), 0)
    g["trapped_%"] = np.where(g["total_seats"] > 0,
                              (g["trapped"] / g["total_seats"] * 100).round(2), 0)

    ov = overrides or {}
    rows = []
    for _, r in g.iterrows():
        key = r[level]
        strained, critical = ov.get(key) or thresholds_for(r["total_seats"], bands)
        rows.append({"strained_above": strained, "critical_above": critical,
                     "status": ("Critical" if r["occupancy_%"] >= critical
                                else "Strained" if r["occupancy_%"] >= strained
                                else "Healthy"),
                     "source": "set by the business" if key in ov else "from estate size"})
    g = pd.concat([g, pd.DataFrame(rows)], axis=1)
    g["headroom_seats"] = ((g["critical_above"] / 100 * g["total_seats"])
                           - g["allocated"]).round(0).astype(int)
    g["_o"] = g["status"].map(STATUS_ORDER)
    return g.sort_values(["_o", "occupancy_%"], ascending=[True, False]).drop(columns="_o")


def alerts(rag_table: pd.DataFrame, level: str = "geo") -> list:
    """One sentence per region that needs saying out loud."""
    if rag_table is None or rag_table.empty:
        return []
    out = []
    for _, r in rag_table.iterrows():
        if r["status"] == "Critical":
            out.append(f"**{r[level]} is critical** — {r['occupancy_%']:.1f}% occupied against "
                       f"a {r['critical_above']:.0f}% limit for an estate this size. "
                       f"{abs(int(r['headroom_seats']))} seats over.")
        elif r["status"] == "Strained":
            out.append(f"**{r[level]} is strained** — {r['occupancy_%']:.1f}% occupied, "
                       f"{int(r['headroom_seats'])} seats before it goes critical.")
    return out


# ────────────────────────────────────────── trapped seat governance
def trapped_position(floors: pd.DataFrame, level: str = "geo",
                     budget_pct: float = TRAPPED_BUDGET_PCT) -> pd.DataFrame:
    """Unusable seats against what the organisation will carry, with the reason
    each is held — which is what the monthly report has to defend."""
    if floors is None or floors.empty:
        return pd.DataFrame()
    keys = [level] if level in floors.columns else []
    g = (floors.groupby(keys, dropna=False)
         .agg(total_seats=("total_seats", "sum"), trapped=("trapped", "sum"))
         .reset_index())
    g["trapped_%"] = np.where(g["total_seats"] > 0,
                              (g["trapped"] / g["total_seats"] * 100).round(2), 0)
    g["budget_seats"] = (g["total_seats"] * budget_pct / 100).round(0).astype(int)
    g["over_budget"] = (g["trapped"] - g["budget_seats"]).astype(int)
    g["within_budget"] = np.where(g["over_budget"] <= 0, "yes", "no")
    return g.sort_values("over_budget", ascending=False)


def justification(floors: pd.DataFrame, level: str = "geo") -> pd.DataFrame:
    """Every held seat, with the reason and whether that reason has an end date.

    A reason with a route out is defensible. One without is a seat the business
    is simply carrying, and that is the conversation leadership will open.
    """
    if floors is None or floors.empty:
        return pd.DataFrame()
    ROUTE = {
        "under_renovation": ("Yes — work is scheduled", "Ends when the refit completes"),
        "it_not_ready": ("Yes — provisioning", "Ends when IT builds the seats"),
        "segregation": ("Yes — partition or move", "Needs a layout change"),
        "layout": ("Yes — reconfiguration", "Needs blocks reshaped"),
        "contractual_hold": ("No — held under contract", "Ends only at renegotiation"),
        "condition": ("No — needs spend", "Furniture replacement required"),
        "other": ("Unclassified", "Reason not recorded"),
    }
    keys = [c for c in (level, "trapped_reason") if c in floors.columns]
    g = (floors.groupby(keys, dropna=False)
         .agg(seats=("trapped", "sum"), floors=("floor", "count")).reset_index())
    g = g[g["seats"] > 0]
    g["route_out"] = g["trapped_reason"].map(lambda r: ROUTE.get(r, ROUTE["other"])[0])
    g["defence"] = g["trapped_reason"].map(lambda r: ROUTE.get(r, ROUTE["other"])[1])
    return g.sort_values("seats", ascending=False)


# ────────────────────────────────────────── team splitting
SPLIT_LIMIT = 20        # a group this size or smaller can go to a training room


def split_guidance(seats_needed: int, largest_block: int) -> str:
    """Teams are kept together wherever possible. Where a split is unavoidable,
    a small remainder can sit in a training room; a large one needs real space."""
    if seats_needed <= largest_block:
        return "Fits in one block — no split needed."
    overflow = seats_needed - largest_block
    if overflow <= SPLIT_LIMIT:
        return (f"{overflow} seats would sit apart from the rest. A group this size can "
                "usually be placed in a training room without breaking the team up.")
    return (f"{overflow} seats would sit apart from the rest — too many for a training "
            "room. This needs a genuinely separate space, and the operations manager "
            "should decide whether to split the team or wait.")
