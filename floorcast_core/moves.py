"""
Floorcast — Move Planning

Steps 3 and 4 of the escalation ladder: consolidate a fragmented account, or
relocate a small one, to open up usable space.

One thing to be clear about, because it is easy to get wrong: **moving people
within a site does not create seats.** Site capacity is unchanged — a seat
freed on 4F is a seat consumed on 5F. What a move does create is *contiguity*
and *segregation headroom*: a single block big enough for a new client, on a
floor that client can have to itself.

So these functions are not a way to close a raw capacity shortfall. They are a
way to make capacity you already have usable by an account that needs it in one
piece. Where the site is genuinely short of seats, the answer is still trapped
release, renovation, another site, or a later date.

The objective is **seats moved, minimised** — never utilisation. Every relocated
seat is an IT task, a weekend, and sometimes a client notification, so a plan
that packs the estate perfectly by moving four hundred people is a worse answer
than one that leaves forty seats idle and moves nobody.
"""

import pandas as pd

ALLOC_COLS = ["site", "building", "floor", "account", "seats"]


def validate_allocations(alloc: pd.DataFrame, floors: pd.DataFrame = None):
    problems = []
    missing = [c for c in ALLOC_COLS if c not in alloc.columns]
    if missing:
        problems.append(f"Missing column(s): {', '.join(missing)}")
        return problems
    if not pd.api.types.is_numeric_dtype(alloc["seats"]):
        problems.append("Column seats must be numeric")
        return problems
    if floors is not None:
        a = alloc.groupby(["site", "building", "floor"])["seats"].sum()
        f = floors.set_index(["site", "building", "floor"])["total_seats"]
        for k, v in a.items():
            if k in f.index and v > f.loc[k]:
                problems.append(f"{k[0]} {k[2]}: allocations total {int(v)} but the floor "
                                f"holds {int(f.loc[k])} seats")
    return problems


def _key(r):
    return f"{r['site']}|{r['building']}|{r['floor']}"


def prepare_moves(alloc: pd.DataFrame, floors: pd.DataFrame):
    """Attach floor capacity to the allocation rows and compute free space."""
    a = alloc.copy()
    a["floor_id"] = a.apply(_key, axis=1)
    f = floors.copy()
    f["floor_id"] = f.apply(_key, axis=1)
    cap = f.set_index("floor_id")["total_seats"].to_dict()
    trapped = f.set_index("floor_id")["trapped"].to_dict()
    used = a.groupby("floor_id")["seats"].sum().to_dict()
    a["floor_capacity"] = a["floor_id"].map(cap)
    a["floor_used"] = a["floor_id"].map(used)
    a["floor_free"] = a["floor_capacity"] - a["floor_used"] - a["floor_id"].map(trapped).fillna(0)
    a["floor_free"] = a["floor_free"].clip(lower=0)
    return a


def fragmentation(alloc: pd.DataFrame) -> pd.DataFrame:
    """Accounts spread across more than one floor at a site — the candidates for
    consolidation, with the size of the smallest piece."""
    g = (alloc.groupby(["site", "account"])
         .agg(floors=("floor_id", "nunique"),
              seats=("seats", "sum"),
              smallest_block=("seats", "min"))
         .reset_index())
    g = g[g["floors"] > 1]
    return g.sort_values(["floors", "smallest_block"], ascending=[False, True])


# ────────────────────────────── step 3 · consolidate
def consolidation_options(alloc: pd.DataFrame, rules=None) -> pd.DataFrame:
    """Move an account's smaller fragments onto the floor where it already sits
    largest, cheapest first.

    Only proposed where the receiving floor genuinely has room once the movers
    arrive — a proposal that does not fit is not an option.
    """
    rows = []
    for (site, account), g in alloc.groupby(["site", "account"]):
        if g["floor_id"].nunique() < 2:
            continue
        if rules is not None and rules.is_frozen(account):
            why = rules.frozen_reason(account)
            rows.append({"site": site, "account": account, "action": "Frozen",
                         "seats_moved": 0, "from": "—", "to": "—", "frees": 0,
                         "note": "Must not be moved" + (f" — {why}" if why else "")})
            continue
        g = g.sort_values("seats", ascending=False)
        home = g.iloc[0]
        room = float(home["floor_free"])
        moved, from_floors = 0, []
        for _, frag in g.iloc[1:].iterrows():
            if frag["seats"] <= room:
                moved += int(frag["seats"])
                room -= frag["seats"]
                from_floors.append((frag["floor_id"], int(frag["seats"])))
        if not from_floors:
            rows.append({"site": site, "account": account,
                         "action": "Cannot consolidate",
                         "seats_moved": 0,
                         "from": ", ".join(sorted(g.iloc[1:]["floor_id"])),
                         "to": home["floor_id"],
                         "frees": 0,
                         "note": "Receiving floor has no room for the fragments"})
            continue
        rows.append({"site": site, "account": account,
                     "action": "Consolidate",
                     "seats_moved": moved,
                     "from": ", ".join(f"{fid.split('|')[-1]} ({n})" for fid, n in from_floors),
                     "to": home["floor_id"].split("|")[-1],
                     "frees": moved,
                     "note": f"Vacates {len(from_floors)} fragment(s); "
                             f"{home['floor_id'].split('|')[-1]} becomes this account's only floor at the site"})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["action", "seats_moved"], ascending=[True, True])


# ────────────────────────────── step 4 · relocate
def relocation_options(alloc: pd.DataFrame, need: int, site: str,
                       max_moves: int = None, rules=None,
                       floor_attrs: dict = None) -> pd.DataFrame:
    """To open a contiguous block of `need` seats on one floor at `site`, which
    accounts would have to move, and where would they go?

    Returns one row per workable option, cheapest in seats moved first. An
    option is only workable if the movers fit on other floors at the same site —
    people are not relocated between cities.

    `rules` is a restrictions.Rules object. Frozen accounts are never proposed
    for a move, and a receiving floor is only offered if the movers are actually
    allowed to sit there — a dedicated floor, a no-colocation pair or a missing
    IT build all rule it out. Blocked options are reported with the reason
    rather than hidden, because "we cannot" is an answer a planner needs.
    """
    floor_attrs = floor_attrs or {}
    a = alloc[alloc["site"] == site]
    if a.empty:
        return pd.DataFrame()
    need = int(need)
    free_by_floor = a.groupby("floor_id")["floor_free"].first().to_dict()
    rows = []

    for fid, g in a.groupby("floor_id"):
        free_here = float(free_by_floor.get(fid, 0))
        if free_here >= need:
            rows.append({"floor": fid.split("|")[-1], "seats_moved": 0,
                         "accounts_moved": "—", "receiving_floors": "—",
                         "block_opened": need,
                         "note": "Already has a block this size — no move needed"})
            continue
        gap = need - free_here
        # cheapest accounts to shift off this floor, smallest first. The last
        # one moves only as far as the gap requires — moving a whole 250-seat
        # account to open 60 seats is not an option a planner would accept.
        cands = g.sort_values("seats")
        picked, moved, partial, blocked = [], 0, None, []
        for _, r in cands.iterrows():
            if moved >= gap:
                break
            if rules is not None and rules.is_frozen(r["account"], fid):
                why = rules.frozen_reason(r["account"])
                blocked.append(f"{r['account']} is frozen" + (f" ({why})" if why else ""))
                continue
            take = int(min(r["seats"], gap - moved))
            picked.append((r["account"], take, take < int(r["seats"])))
            if take < int(r["seats"]):
                partial = r["account"]
            moved += take
        if moved < gap:
            if blocked:
                rows.append({"floor": fid.split("|")[-1], "seats_moved": 0,
                             "accounts_moved": "—", "receiving_floors": "—",
                             "block_opened": 0, "splits_an_account": False,
                             "note": "Blocked — " + "; ".join(blocked)})
            continue                      # even emptying the floor is not enough
        elsewhere = {k: v for k, v in free_by_floor.items() if k != fid}
        # a floor with space is not automatically a floor these people may use
        if rules is not None:
            occ = a.groupby("floor_id")["account"].apply(lambda s: sorted(set(s))).to_dict()
            allowed, refused = {}, []
            for k, v in elsewhere.items():
                ok_here = True
                for acct, _, _ in picked:
                    ok, why = rules.may_place(acct, k, occ.get(k, []), floor_attrs.get(k, []))
                    if not ok:
                        refused.append(f"{k.split('|')[-1]}: {acct} {why}")
                        ok_here = False
                        break
                if ok_here:
                    allowed[k] = v
            if refused and not allowed:
                rows.append({"floor": fid.split("|")[-1], "seats_moved": moved,
                             "accounts_moved": ", ".join(f"{n} ({k})" for n, k, _ in picked),
                             "receiving_floors": "—", "block_opened": 0,
                             "splits_an_account": False,
                             "note": "Blocked — " + "; ".join(sorted(set(refused))[:2])})
                continue
            elsewhere = allowed
        capacity_elsewhere = sum(elsewhere.values())
        if capacity_elsewhere < moved:
            rows.append({"floor": fid.split("|")[-1], "seats_moved": moved,
                         "accounts_moved": ", ".join(f"{n} ({k})" for n, k, _ in picked),
                         "receiving_floors": "—", "block_opened": 0,
                         "note": "No room elsewhere on site for the movers"})
            continue
        # place the movers on the emptiest floors first
        targets, remaining = [], moved
        for k, v in sorted(elsewhere.items(), key=lambda kv: -kv[1]):
            if remaining <= 0:
                break
            take = int(min(remaining, v))
            if take > 0:
                targets.append(f"{k.split('|')[-1]} (+{take})")
                remaining -= take
        note = f"Opens {int(free_here + moved)} contiguous seats"
        if partial:
            note += f" — {partial} is split across floors by this move"
        rows.append({"floor": fid.split("|")[-1], "seats_moved": moved,
                     "accounts_moved": ", ".join(f"{n} ({k})" for n, k, _ in picked),
                     "receiving_floors": ", ".join(targets),
                     "block_opened": int(free_here + moved),
                     "splits_an_account": bool(partial),
                     "note": note})

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    if max_moves is not None:
        out = out[out["seats_moved"] <= max_moves]
    return out.sort_values(["seats_moved", "block_opened"], ascending=[True, False])


def move_cost_summary(options: pd.DataFrame) -> dict:
    """Headline for whichever option is cheapest."""
    if options.empty:
        return {}
    workable = options[options["block_opened"] > 0]
    if workable.empty:
        return {"workable": 0}
    best = workable.iloc[0]
    return {"workable": len(workable),
            "cheapest_moves": int(best["seats_moved"]),
            "floor": best["floor"],
            "block_opened": int(best["block_opened"]),
            "accounts": best["accounts_moved"]}
