"""
Floorcast — Scenario Engine

The files are loaded once. After that the planner should be able to move levers
and watch the whole picture recompute, rather than re-uploading anything.

A scenario is a set of levers applied to the loaded data:

    period            which month or week to plan against
    demand_uplift     demand up or down on plan, as a percentage
    release_reasons   which trapped-seat reasons are assumed recovered
    release_fraction  how much of that trapped pool comes back
    include_expansion whether renovation capacity counts
    horizon_weeks     and how soon it has to land to count
    pipeline_mode     signed business only, weighted by likelihood, or every deal

Everything is computed against the same baseline, so two scenarios can be put
side by side and the difference is real rather than an artefact of what was
loaded when.
"""

import numpy as np
import pandas as pd

from . import estate as es
from . import demand_reader as dr

DEFAULTS = {
    "period": None,
    "demand_uplift": 0.0,
    "release_reasons": [],
    "release_fraction": 0.0,
    "include_expansion": True,
    "horizon_weeks": 12,
    "pipeline_mode": "exclude",
    "pipeline_ratio": 1.2,
}


def levers(**kw) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in kw.items() if v is not None})
    return out


def _supply(floors: pd.DataFrame, lv: dict) -> pd.DataFrame:
    """Usable seats per site under this scenario."""
    f = floors.copy()
    if lv["release_reasons"] and lv["release_fraction"] > 0:
        f = es.release_trapped(f, lv["release_reasons"], lv["release_fraction"])
    else:
        f = f.copy()
        f["released"] = 0
    exp = f["expansion_space"].where(
        lv["include_expansion"]
        & (f["expansion_eta_weeks"].isna() | (f["expansion_eta_weeks"] <= lv["horizon_weeks"])),
        0)
    f["expansion_counted"] = exp.fillna(0)
    g = (f.groupby("site")
         .agg(allocated=("allocated", "sum"),
              available=("available", "sum"),
              trapped=("trapped", "sum"),
              released=("released", "sum"),
              expansion_counted=("expansion_counted", "sum"),
              total_seats=("total_seats", "sum"))
         .reset_index())
    g["usable"] = g["available"] + g["expansion_counted"]
    return g


def _requirement(demand: pd.DataFrame, pipe: pd.DataFrame, lv: dict) -> pd.DataFrame:
    """Seats needed per site under this scenario, committed plus pipeline."""
    need = dr.need_by_site(demand, lv["period"]) if lv["period"] else {}
    base = pd.DataFrame({"site": list(need), "committed": list(need.values())})
    if base.empty:
        base = pd.DataFrame(columns=["site", "committed"])
    base["committed"] = (base["committed"] * (1 + lv["demand_uplift"])).round().astype(int)

    if pipe is not None and not pipe.empty and lv["pipeline_mode"] != "exclude":
        ps = dr.pipeline_seats(pipe, lv["pipeline_mode"], lv["pipeline_ratio"])
        pv = ps.groupby("site")["seats"].sum().reset_index().rename(columns={"seats": "pipeline"})
    else:
        pv = pd.DataFrame(columns=["site", "pipeline"])
    out = base.merge(pv, on="site", how="outer").fillna(0)
    for c in ("committed", "pipeline"):
        out[c] = out[c].astype(int)
    out["required"] = out["committed"] + out["pipeline"]
    return out


def compute(floors: pd.DataFrame, demand: pd.DataFrame, pipe: pd.DataFrame, lv: dict) -> dict:
    """Returns per-site detail and the headline totals for one scenario."""
    sup = _supply(floors, lv)
    req = _requirement(demand, pipe, lv)
    t = sup.merge(req, on="site", how="outer").fillna(0)
    for c in ("allocated", "available", "usable", "required", "committed",
              "pipeline", "released", "expansion_counted", "trapped", "total_seats"):
        if c in t.columns:
            t[c] = pd.to_numeric(t[c], errors="coerce").fillna(0).astype(int)

    # a site only needs seats beyond what it already holds
    t["incremental_need"] = (t["required"] - t["allocated"]).clip(lower=0)
    t["gap"] = t["usable"] - t["incremental_need"]
    t["status"] = np.where(t["gap"] >= 0, "Fits", "Short")
    t = t.sort_values("gap")

    short = t[t["gap"] < 0]
    return {
        "sites": t,
        "totals": {
            "required": int(t["required"].sum()),
            "incremental_need": int(t["incremental_need"].sum()),
            "usable": int(t["usable"].sum()),
            "available": int(t["available"].sum()),
            "released": int(t["released"].sum()),
            "expansion_counted": int(t["expansion_counted"].sum()),
            "trapped_left": int(t["trapped"].sum()),
            "shortfall": int(-short["gap"].sum()) if len(short) else 0,
            "sites_short": len(short),
            "sites_short_names": ", ".join(short["site"].tolist()),
        },
    }


def waterfall(result: dict) -> pd.DataFrame:
    """How the scenario gets from today's free seats to the answer — the steps a
    planner would talk through in a meeting."""
    t = result["totals"]
    rows = [
        ("Free today", t["available"] - t["released"]),
        ("Trapped seats released", t["released"]),
        ("Renovation in horizon", t["expansion_counted"]),
        ("Seats needed", -t["incremental_need"]),
    ]
    out = pd.DataFrame(rows, columns=["step", "seats"])
    out["running"] = out["seats"].cumsum()
    return out


def compare(named: dict) -> pd.DataFrame:
    """Named scenarios side by side, baseline first."""
    rows = []
    for name, res in named.items():
        t = res["totals"]
        rows.append({"scenario": name,
                     "seats needed": t["incremental_need"],
                     "usable seats": t["usable"],
                     "of which released": t["released"],
                     "of which renovation": t["expansion_counted"],
                     "shortfall": t["shortfall"],
                     "sites short": t["sites_short"]})
    df = pd.DataFrame(rows)
    if len(df) > 1:
        base = df.iloc[0]
        df["vs baseline"] = df["shortfall"].apply(
            lambda v: 0 if v == base["shortfall"] else int(base["shortfall"] - v))
    return df


def describe(lv: dict) -> str:
    """One line naming what this scenario assumes, so a saved snapshot is
    self-explanatory three weeks later."""
    bits = [f"period {lv['period']}"]
    if lv["demand_uplift"]:
        bits.append(f"demand {lv['demand_uplift']:+.0%}")
    if lv["release_reasons"] and lv["release_fraction"]:
        bits.append(f"{lv['release_fraction']:.0%} of "
                    + "/".join(lv["release_reasons"]) + " released")
    if lv["include_expansion"]:
        bits.append(f"renovation within {lv['horizon_weeks']}w")
    else:
        bits.append("no renovation")
    if lv["pipeline_mode"] != "exclude":
        bits.append({"weighted": "likely deals counted",
                     "full": "all chased deals counted"}[lv["pipeline_mode"]])
    return " · ".join(bits)
