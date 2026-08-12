"""
Floorcast — Seat-Map Engine (real-floor mode)

The grid engine in facility_engine.py models every floor as a perfect
seat_rows x seat_cols rectangle. Real floors are not rectangles: they have
named zones, irregular seat positions, and mixed desk sizes.

This module takes a seat-level inventory — one row per physical seat, with
real millimetre coordinates lifted from a CAD floor plan — and allocates
demand onto those actual seats.

Seat inventory columns
----------------------
country, city, site, building, tower, floor, zone, seat_id, x_mm, y_mm, desk_size_mm

Security model (unchanged)
--------------------------
- ACCOUNT = security boundary. An account fills whole zones wherever it can,
  so segregation follows the floor's own physical zoning rather than an
  arbitrary run of grid cells.
- LOB = open within its account, contiguous inside the account's seats.

The grid path is untouched; this is an additive alternative.
"""

import pandas as pd

SEAT_COLS = ["country", "city", "site", "building", "tower", "floor",
             "zone", "seat_id", "x_mm", "y_mm"]


def validate_seats(seats: pd.DataFrame):
    """Returns a list of human-readable problems; empty list means usable."""
    problems = []
    missing = [c for c in SEAT_COLS if c not in seats.columns]
    if missing:
        problems.append(f"Missing column(s): {', '.join(missing)}")
        return problems
    if seats["seat_id"].duplicated().any():
        dup = seats.loc[seats["seat_id"].duplicated(), "seat_id"].head(5).tolist()
        problems.append(f"Duplicate seat_id(s), e.g. {', '.join(map(str, dup))}")
    for c in ("x_mm", "y_mm"):
        if not pd.api.types.is_numeric_dtype(seats[c]):
            problems.append(f"Column {c} must be numeric")
    if seats[["zone"]].isna().any().any():
        problems.append("Some seats have no zone")
    return problems


def floor_key(row):
    return f"{row['site']}|{row['building']}|{row['tower']}|{row['floor']}"


def floors_from_seats(seats: pd.DataFrame) -> pd.DataFrame:
    """Derive a floor inventory from the seat list. Capacity is counted, not
    multiplied out of a grid, so it is exactly the number of real seats."""
    g = (seats.groupby(["country", "city", "site", "building", "tower", "floor"],
                       dropna=False)
         .agg(capacity=("seat_id", "count"), zones=("zone", "nunique"))
         .reset_index())
    g["key"] = g.apply(floor_key, axis=1)
    return g


def zone_summary(seats: pd.DataFrame) -> pd.DataFrame:
    cols = ["site", "building", "tower", "floor", "zone"]
    g = seats.groupby(cols, dropna=False).agg(seats=("seat_id", "count")).reset_index()
    if "desk_size_mm" in seats.columns:
        mix = (seats.groupby(cols + ["desk_size_mm"]).size()
               .reset_index(name="n")
               .sort_values("n", ascending=False)
               .groupby(cols)["desk_size_mm"].first().reset_index()
               .rename(columns={"desk_size_mm": "predominant_desk"}))
        g = g.merge(mix, on=cols, how="left")
    return g.sort_values("seats", ascending=False)


def _order_seats(seats: pd.DataFrame) -> pd.DataFrame:
    """Order seats so that consecutive seats are physically adjacent: largest
    zone first, and within a zone walk the seats in reading order."""
    sizes = seats.groupby("zone")["seat_id"].count().sort_values(ascending=False)
    rank = {z: i for i, z in enumerate(sizes.index)}
    s = seats.copy()
    s["_z"] = s["zone"].map(rank)
    # band y into rows so a zone is walked row by row rather than diagonally
    s["_band"] = (s["y_mm"] / 1500).round()
    return s.sort_values(["_z", "_band", "x_mm"]).drop(columns=["_z", "_band"])



def _pack_zones(need: int, free: dict):
    """Give an account whole zones wherever possible, largest zone first.

    A zone is only split when the account's remainder is smaller than every
    zone still free — so accounts sit inside physical zone boundaries instead
    of running across them, and any zone-sharing is the rare tail case rather
    than the norm. Returns (taken {zone: seats}, shortfall).
    """
    taken = {}
    while need > 0:
        cands = [(z, c) for z, c in free.items() if c > 0]
        if not cands:
            break
        fits = [(z, c) for z, c in cands if c <= need]
        if fits:
            z, c = max(fits, key=lambda t: t[1])
            taken[z] = taken.get(z, 0) + c
            free[z] = 0
            need -= c
        else:
            z, c = min(cands, key=lambda t: t[1])   # split the smallest zone
            taken[z] = taken.get(z, 0) + need
            free[z] = c - need
            need = 0
    return taken, need


def zone_security_report(assigned: pd.DataFrame) -> pd.DataFrame:
    """Zones hosting more than one account — contractual segregation exceptions."""
    a = assigned[assigned["account"].notna()]
    if a.empty:
        return pd.DataFrame(columns=["site", "floor", "zone", "accounts", "account_list"])
    g = (a.groupby(["site", "floor", "zone"])["account"]
         .agg(accounts="nunique", account_list=lambda s: ", ".join(sorted(set(s))))
         .reset_index())
    return g[g["accounts"] > 1].sort_values("accounts", ascending=False)


def allocate_seats(seat_forecast: pd.DataFrame, seats: pd.DataFrame,
                   allocatable_zones=None):
    """Returns (assigned_seats_df, blocks_df, unplaced_df).

    assigned_seats_df is the seat inventory plus account / lob columns
    (NaN where the seat is unallocated), ready to plot at real coordinates.

    allocatable_zones: zones that may take production demand. Support space —
    manager cabins, counselling rooms, IT and HR rooms — is excluded by default
    via the zone_type column, because filling the tail of an account's demand
    with a single seat in a counselling room is arithmetically valid and
    operationally wrong.
    """
    seats = seats.copy()
    if allocatable_zones is None:
        if "zone_type" in seats.columns:
            allocatable_zones = sorted(
                seats.loc[seats["zone_type"].astype(str).str.lower() == "production",
                          "zone"].unique())
        else:
            allocatable_zones = sorted(seats["zone"].unique())
    allocatable_zones = set(allocatable_zones)
    seats["key"] = seats.apply(floor_key, axis=1)
    pool_all = seats[seats["zone"].isin(allocatable_zones)]
    cap_free = pool_all.groupby("key")["seat_id"].count().to_dict()

    fc = seat_forecast.copy()
    if "floor" not in fc.columns:
        fc["floor"] = None
    fc["floor"] = fc["floor"].where(fc["floor"].notna(), None)

    demand = (fc.groupby(["account", "lob", "site", "building", "tower", "floor"],
                         dropna=False)["seats"].sum().reset_index())

    placements, unplaced = [], []

    def site_floors(site):
        return sorted([k for k in cap_free if k.startswith(str(site) + "|")],
                      key=lambda k: -cap_free[k])

    acc_order = (demand.groupby("account")["seats"].sum()
                 .sort_values(ascending=False).index.tolist())
    for acc in acc_order:
        for _, r in demand[demand["account"] == acc].iterrows():
            need = int(r["seats"])
            if r["floor"] is not None and pd.notna(r["floor"]):
                k = f"{r['site']}|{r['building']}|{r['tower']}|{r['floor']}"
                if k in cap_free and cap_free[k] >= need:
                    placements.append((k, acc, r["lob"], need))
                    cap_free[k] -= need
                else:
                    have = cap_free.get(k, 0)
                    unplaced.append({**r.to_dict(), "short_by": need - have,
                                     "reason": "pinned floor lacks capacity"})
            else:
                cands = site_floors(r["site"])
                one = next((k for k in cands if cap_free[k] >= need), None)
                if one:
                    placements.append((one, acc, r["lob"], need))
                    cap_free[one] -= need
                else:
                    remaining = need
                    for k in cands:
                        take = min(remaining, cap_free[k])
                        if take > 0:
                            placements.append((k, acc, r["lob"], take))
                            cap_free[k] -= take
                            remaining -= take
                        if remaining == 0:
                            break
                    if remaining > 0:
                        unplaced.append({**r.to_dict(), "seats": remaining,
                                         "short_by": remaining,
                                         "reason": "site capacity exhausted"})

    seats["account"] = None
    seats["lob"] = None
    block_rows = []
    by_floor = {}
    for k, acc, lob, n in placements:
        by_floor.setdefault(k, []).append((acc, lob, n))

    for k, entries in by_floor.items():
        pool = _order_seats(seats[(seats["key"] == k)
                                  & (seats["zone"].isin(allocatable_zones))])
        zone_seats = {z: list(g.index) for z, g in pool.groupby("zone", sort=False)}
        free = {z: len(v) for z, v in zone_seats.items()}

        # demand per account on this floor, largest account first
        acc_need = {}
        for acc, lob, n in entries:
            acc_need[acc] = acc_need.get(acc, 0) + n

        acc_zones = {}
        for acc in sorted(acc_need, key=lambda a: -acc_need[a]):
            taken, short = _pack_zones(acc_need[acc], free)
            acc_zones[acc] = taken
            if short:
                unplaced.append({"account": acc, "seats": short, "short_by": short,
                                 "reason": "floor capacity exhausted after zone packing"})

        # hand each account its seats, LOBs contiguous inside
        for acc, taken in acc_zones.items():
            idx = []
            for z, cnt in taken.items():
                idx += zone_seats[z][:cnt]
                zone_seats[z] = zone_seats[z][cnt:]
            pos = 0
            for acc2, lob, n in [e for e in entries if e[0] == acc]:
                grab = idx[pos:pos + n]
                if not grab:
                    continue
                seats.loc[grab, "account"] = acc
                seats.loc[grab, "lob"] = lob
                zs = list(dict.fromkeys(seats.loc[grab, "zone"]))
                block_rows.append({
                    "site": seats.loc[grab[0], "site"],
                    "building": seats.loc[grab[0], "building"],
                    "tower": seats.loc[grab[0], "tower"],
                    "floor": seats.loc[grab[0], "floor"],
                    "account": acc, "lob": lob, "seats": len(grab),
                    "zones": ", ".join(map(str, zs)),
                    "from_seat": seats.loc[grab[0], "seat_id"],
                    "to_seat": seats.loc[grab[-1], "seat_id"]})
                pos += n

    return seats, pd.DataFrame(block_rows), pd.DataFrame(unplaced)


def rollups_seats(assigned: pd.DataFrame):
    """Requirement vs capacity vs utilisation at floor / site / city level."""
    out = {}
    a = assigned.copy()
    a["used"] = a["account"].notna().astype(int)
    f = (a.groupby(["city", "site", "building", "tower", "floor"], dropna=False)
         .agg(seats=("used", "sum"), capacity=("seat_id", "count")).reset_index())
    f["utilization_%"] = (f["seats"] / f["capacity"] * 100).round(1)
    out["floor"] = f
    for lvl in ("site", "city"):
        g = f.groupby(lvl)[["seats", "capacity"]].sum().reset_index()
        g["utilization_%"] = (g["seats"] / g["capacity"] * 100).round(1)
        out[lvl] = g
    z = (a.groupby(["site", "floor", "zone"], dropna=False)
         .agg(seats=("used", "sum"), capacity=("seat_id", "count")).reset_index())
    z["utilization_%"] = (z["seats"] / z["capacity"] * 100).round(1)
    out["zone"] = z.sort_values("capacity", ascending=False)
    return out


def assign_employees_seats(assigned: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    pools = {}
    for _, s in assigned[assigned["account"].notna()].iterrows():
        pools.setdefault((s["account"], s["lob"]), []).append((s["seat_id"], s["zone"]))
    used, rows = {}, []
    for _, e in roster.iterrows():
        g = (e["account"], e["lob"])
        pool = pools.get(g, [])
        i = used.get(g, 0)
        if i < len(pool):
            sid, zone = pool[i]
            rows.append({"employee_id": e["employee_id"],
                         "employee_name": e.get("employee_name", ""),
                         "account": e["account"], "lob": e["lob"],
                         "zone": zone, "seat_id": sid, "status": "Assigned"})
            used[g] = i + 1
        else:
            rows.append({"employee_id": e["employee_id"],
                         "employee_name": e.get("employee_name", ""),
                         "account": e["account"], "lob": e["lob"],
                         "zone": "", "seat_id": "", "status": "No seat available"})
    return pd.DataFrame(rows)


def export_dxf_seats(assigned: pd.DataFrame, path: str, desk_w=1000.0, desk_d=600.0):
    """CAD export at true coordinates — every seat lands where it physically is,
    so the drawing overlays the real floor plan instead of a synthetic grid."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4          # millimetres
    msp = doc.modelspace()
    for lay, col in [("SEATS", 8), ("SEAT_IDS", 7), ("ACCOUNTS", 1),
                     ("ZONES", 4), ("UNALLOCATED", 253)]:
        if lay not in doc.layers:
            doc.layers.add(lay, color=col)

    for _, s in assigned.iterrows():
        x, y = float(s["x_mm"]) - desk_w / 2, float(s["y_mm"]) - desk_d / 2
        lay = "SEATS" if pd.notna(s["account"]) else "UNALLOCATED"
        msp.add_lwpolyline([(x, y), (x + desk_w, y), (x + desk_w, y + desk_d), (x, y + desk_d)],
                           close=True, dxfattribs={"layer": lay})
        msp.add_text(str(s["seat_id"]).split("-")[-1],
                     dxfattribs={"height": 120, "layer": "SEAT_IDS"}
                     ).set_placement((x + 60, y + 60))

    for (zone, acc), grp in assigned.groupby(["zone", assigned["account"].fillna("Unallocated")]):
        cx, cy = grp["x_mm"].mean(), grp["y_mm"].max() + 900
        msp.add_text(f"{zone} — {acc} ({len(grp)})",
                     dxfattribs={"height": 400,
                                 "layer": "ACCOUNTS" if acc != "Unallocated" else "ZONES"}
                     ).set_placement((cx, cy))
    doc.saveas(path)
    return path
