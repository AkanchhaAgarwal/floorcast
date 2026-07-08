"""
Floorcast — Facility Engine (facility-management mode)

Inputs
------
seat_forecast: account, lob, country, city, site, building, tower, floor(optional), seats
floor_inventory: country, city, site, building, tower, floor, seat_rows, seat_cols
employee_roster (optional): employee_id, employee_name, account, lob

Rules
-----
- ACCOUNT = security boundary: contiguous dedicated block per floor; accounts never interleave.
- LOB = open within its account: LOBs fill the account block contiguously but need no partition.
- Demand rows with a floor are pinned there; site-level rows are distributed largest-first
  across that site's floors with free capacity.

Outputs
-------
- allocation: floor-level seat blocks per account/lob with seat IDs
- rollups: floor / site / city requirement vs capacity
- assignment: employee -> seat ID register
"""

import math
import numpy as np
import pandas as pd

GEO = ["country", "city", "site", "building", "tower", "floor"]


def floor_key(row):
    return f"{row['site']}|{row['building']}|{row['tower']}|{row['floor']}"


def allocate(seat_forecast: pd.DataFrame, floors: pd.DataFrame):
    """Returns (floor_maps, blocks_df, unplaced_df).

    floor_maps: {floor_key: 2D numpy array of dicts/None}, each cell:
        {seat_id, account, lob}
    blocks_df: one row per (floor, account, lob) with seats + seat_id range
    """
    floors = floors.copy()
    floors["key"] = floors.apply(floor_key, axis=1)
    floors["capacity"] = floors["seat_rows"] * floors["seat_cols"]
    cap_free = dict(zip(floors["key"], floors["capacity"]))
    floor_rows = {r["key"]: r for _, r in floors.iterrows()}

    fc = seat_forecast.copy()
    fc["floor"] = fc.get("floor", pd.Series([None] * len(fc))).where(
        fc.get("floor", pd.Series([None] * len(fc))).notna(), None)

    # aggregate demand to account+lob (+pinned floor)
    demand = (fc.groupby(["account", "lob", "site", "building", "tower", "floor"],
                         dropna=False)["seats"].sum().reset_index())

    # place accounts: pinned floors first, then site-level largest-first
    placements = []      # (floor_key, account, lob, seats)
    unplaced = []

    def site_floors(site):
        return sorted([k for k in cap_free if k.startswith(site + "|")],
                      key=lambda k: -cap_free[k])

    acc_order = (demand.groupby("account")["seats"].sum()
                 .sort_values(ascending=False).index.tolist())
    for acc in acc_order:
        acc_rows = demand[demand["account"] == acc]
        for _, r in acc_rows.iterrows():
            need = int(r["seats"])
            if pd.notna(r["floor"]) and r["floor"] is not None:
                k = f"{r['site']}|{r['building']}|{r['tower']}|{r['floor']}"
                if k in cap_free and cap_free[k] >= need:
                    placements.append((k, acc, r["lob"], need))
                    cap_free[k] -= need
                else:
                    unplaced.append({**r.to_dict(), "reason": "pinned floor lacks capacity"})
            else:
                # keep an account on as few floors as possible: try single floor first
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
                                         "reason": "site capacity exhausted"})

    # build seat grids: row-major fill, account blocks contiguous (security),
    # LOBs contiguous inside the account block
    floor_maps, block_rows = {}, []
    by_floor = {}
    for k, acc, lob, n in placements:
        by_floor.setdefault(k, []).append((acc, lob, n))

    for k, entries in by_floor.items():
        fr = floor_rows[k]
        R, C = int(fr["seat_rows"]), int(fr["seat_cols"])
        grid = np.full((R, C), None, dtype=object)
        pos = 0
        # group by account so each account occupies one contiguous run
        entries_sorted = sorted(entries, key=lambda e: (e[0], e[1]))
        from itertools import groupby
        for acc, acc_entries in groupby(entries_sorted, key=lambda e: e[0]):
            for _, lob, n in acc_entries:
                first_id, last_id = None, None
                for _ in range(n):
                    r, c = divmod(pos, C)
                    sid = f"{fr['tower']}-{fr['floor']}-R{r+1:02d}S{c+1:02d}"
                    grid[r][c] = {"seat_id": sid, "account": acc, "lob": lob}
                    first_id = first_id or sid
                    last_id = sid
                    pos += 1
                block_rows.append({"site": fr["site"], "building": fr["building"],
                                   "tower": fr["tower"], "floor": fr["floor"],
                                   "account": acc, "lob": lob, "seats": n,
                                   "from_seat": first_id, "to_seat": last_id})
        floor_maps[k] = grid

    return floor_maps, pd.DataFrame(block_rows), pd.DataFrame(unplaced)


def rollups(blocks: pd.DataFrame, floors: pd.DataFrame):
    floors = floors.copy()
    floors["capacity"] = floors["seat_rows"] * floors["seat_cols"]
    out = {}
    if blocks.empty:
        return out
    f = (blocks.groupby(["site", "building", "tower", "floor"])["seats"].sum()
         .reset_index().merge(
             floors[["site", "building", "tower", "floor", "capacity"]],
             on=["site", "building", "tower", "floor"], how="right").fillna(0))
    f["utilization_%"] = (f["seats"] / f["capacity"] * 100).round(1)
    out["floor"] = f
    s = f.groupby("site")[["seats", "capacity"]].sum().reset_index()
    s["utilization_%"] = (s["seats"] / s["capacity"] * 100).round(1)
    out["site"] = s
    city_map = floors[["site", "city"]].drop_duplicates()
    c = f.merge(city_map, on="site").groupby("city")[["seats", "capacity"]].sum().reset_index()
    c["utilization_%"] = (c["seats"] / c["capacity"] * 100).round(1)
    out["city"] = c
    return out


def assign_employees(blocks: pd.DataFrame, floor_maps: dict,
                     roster: pd.DataFrame) -> pd.DataFrame:
    """Named seat allocation: employees fill their account+lob blocks in order."""
    seats_by_group = {}
    for k, grid in floor_maps.items():
        for row in grid:
            for cell in row:
                if cell:
                    seats_by_group.setdefault((cell["account"], cell["lob"]), []).append(cell["seat_id"])
    rows = []
    used = {g: 0 for g in seats_by_group}
    for _, e in roster.iterrows():
        g = (e["account"], e["lob"])
        pool = seats_by_group.get(g, [])
        i = used.get(g, 0)
        if i < len(pool):
            rows.append({"employee_id": e["employee_id"], "employee_name": e.get("employee_name", ""),
                         "account": e["account"], "lob": e["lob"], "seat_id": pool[i],
                         "status": "Assigned"})
            used[g] = i + 1
        else:
            rows.append({"employee_id": e["employee_id"], "employee_name": e.get("employee_name", ""),
                         "account": e["account"], "lob": e["lob"], "seat_id": "",
                         "status": "No seat available"})
    return pd.DataFrame(rows)


def export_dxf(floor_maps: dict, path: str, seat_w=5.0, seat_d=5.0):
    """Draft CAD export: one DXF with each floor as a labeled seat grid."""
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    x_off = 0.0
    for k, grid in floor_maps.items():
        R, C = grid.shape
        msp.add_text(k.replace("|", " / "), dxfattribs={"height": 3}).set_placement((x_off, R * seat_d + 4))
        msp.add_lwpolyline([(x_off, 0), (x_off + C * seat_w, 0),
                            (x_off + C * seat_w, R * seat_d), (x_off, R * seat_d)],
                           close=True, dxfattribs={"layer": "FLOOR_BOUNDARY"})
        prev_acc = None
        for r in range(R):
            for c in range(C):
                cell = grid[r][c]
                x, y = x_off + c * seat_w, (R - 1 - r) * seat_d
                msp.add_lwpolyline([(x, y), (x + seat_w, y), (x + seat_w, y + seat_d),
                                    (x, y + seat_d)], close=True,
                                   dxfattribs={"layer": "SEATS"})
                if cell:
                    msp.add_text(cell["seat_id"].split("-")[-1],
                                 dxfattribs={"height": 0.9, "layer": "SEAT_IDS"}
                                 ).set_placement((x + 0.4, y + 0.4))
                    if cell["account"] != prev_acc:
                        msp.add_text(cell["account"],
                                     dxfattribs={"height": 1.6, "layer": "ACCOUNTS"}
                                     ).set_placement((x + 0.4, y + 2.6))
                    prev_acc = cell["account"]
        x_off += C * seat_w + 15
    doc.saveas(path)
