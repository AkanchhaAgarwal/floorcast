"""
Floorcast — seat planning for a contact centre estate.

Built to mirror how a seat planner actually works: a portfolio of floors across
several countries, seats in four states (allocated, available, trapped,
expansion), demand assembled per site and per period, ramps matched floor by
floor, and only then a coloured floor map.

Run:  streamlit run app.py
"""

import io
import numpy as np
import pandas as pd
import streamlit as st

from floorcast_core import estate as es
from floorcast_core import demand_reader as dr
from floorcast_core import seatmap_engine as sm
from floorcast_core import floor_render as fr

st.set_page_config(page_title="Floorcast", page_icon="🏬", layout="wide")

def _xlsx(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for nm, df_ in sheets.items():
            if df_ is not None and not df_.empty:
                df_.to_excel(xw, sheet_name=nm[:31], index=False)
    return buf.getvalue()


st.markdown(
    "<h1 style='color:#0F6B6B;margin-bottom:0'>Floorcast</h1>"
    "<p style='color:#C9962E;font-weight:600;margin-top:0'>"
    "Seat planning across the estate — capacity, ramps and floor maps in one place.</p>",
    unsafe_allow_html=True)


# ─────────────────────────────────────────────── loaders
@st.cache_data(show_spinner=False)
def load_floors(data):
    return pd.read_csv(io.BytesIO(data))


@st.cache_data(show_spinner=False)
def load_demand(data):
    return dr.read_demand(io.BytesIO(data))


@st.cache_data(show_spinner=False)
def load_pipeline(data):
    return dr.read_pipeline(io.BytesIO(data))


@st.cache_data(show_spinner="Reading the floor plan…")
def load_plan(data, name):
    if name.lower().endswith(".csv"):
        return {"seats": pd.read_csv(io.BytesIO(data)), "background": None,
                "extent": None, "scale_mm_per_pt": None, "source": "csv"}
    from floorcast_core import plan_ingest
    out = plan_ingest.read_plan(data)
    out["source"] = "pdf"
    return out


with st.sidebar:
    st.header("Inputs")
    f_file = st.file_uploader("Floor inventory (CSV)", type="csv",
                              help="geo, country, city, site, building, floor, total_seats, "
                                   "allocated, available, trapped, trapped_reason, "
                                   "expansion_space, expansion_eta_weeks")
    d_file = st.file_uploader("Planning workbook (Excel)", type=["xlsx", "xls"],
                              help="Account / LOB / Site with metric rows and one column "
                                   "per period")
    p_file = st.file_uploader("Sales pipeline (CSV, optional)", type="csv",
                              help="account, site, stage, probability, month, hc")
    plan_file = st.file_uploader("Floor plan (PDF) or seat inventory (CSV), optional",
                                 type=["pdf", "csv"])
    st.caption("Anything not uploaded falls back to the bundled sample estate.")

floors_raw = load_floors(f_file.getvalue()) if f_file else \
    load_floors(open("data/sample_estate_floors.csv", "rb").read())
demand = load_demand(d_file.getvalue()) if d_file else \
    load_demand(open("data/sample_estate_demand.xlsx", "rb").read())
pipe = load_pipeline(p_file.getvalue()) if p_file else \
    load_pipeline(open("data/sample_pipeline.csv", "rb").read())

problems = es.validate_floors(floors_raw)
if problems:
    st.error("Floor inventory cannot be used:\n\n" + "\n".join(f"- {p}" for p in problems))
    st.stop()
floors = es.prepare(floors_raw)

unknown = demand.attrs.get("unrecognised_metrics") or []
if unknown:
    st.warning("Metric rows not recognised and ignored: " + ", ".join(map(str, unknown)))

tab_estate, tab_demand, tab_ramp, tab_map, tab_scen = st.tabs(
    ["🏢 Estate", "📈 Demand", "🎯 Ramp plan", "🗺 Floor map", "🔮 Scenarios"])

# ═══════════════════════════════════════════════ 1 · ESTATE
with tab_estate:
    t = es.estate_totals(floors)
    c = st.columns(6)
    c[0].metric("Total seats", f"{t['total_seats']:,}")
    c[1].metric("Allocated", f"{t['allocated']:,}")
    c[2].metric("Available", f"{t['available']:,}")
    c[3].metric("Trapped", f"{t['trapped']:,}", delta=f"{t['trapped_%']}% of estate",
                delta_color="inverse")
    c[4].metric("Occupancy", f"{t['occupancy_%']}%")
    c[5].metric("Expansion space", f"{t['expansion_space']:,}",
                help="Not built yet — unlocked by renovation")

    lvl = st.radio("Roll up by", ["geo", "country", "city", "site", "floor_id"],
                   horizontal=True, index=0)
    st.dataframe(es.rollup(floors, lvl), width="stretch", hide_index=True)

    st.markdown("#### Trapped seats")
    st.caption("Seats that physically exist but cannot be used. This is usually the "
               "largest single pool of recoverable capacity in the estate, so it is "
               "tracked by reason rather than lumped into one number.")
    tb = es.trapped_breakdown(floors)
    st.dataframe(tb, width="stretch", hide_index=True)
    rel = int(tb.loc[tb["releasable"], "seats"].sum())
    st.info(f"**{rel:,} of {t['trapped']:,} trapped seats** sit against reasons that a "
            f"partition move, an IT rollout or a relocation could address — "
            f"{rel / t['total_seats'] * 100:.1f}% of the estate. "
            "Model that in the Scenarios tab before committing to new space.")

# ═══════════════════════════════════════════════ 2 · DEMAND
with tab_demand:
    periods = dr.week_options(demand)
    labels, sites = dr.site_options(demand)
    cc = st.columns(2)
    site_pick = cc[0].selectbox("Site", ["All sites"] + labels, key="dem_site")
    site = None if site_pick == "All sites" else sites[labels.index(site_pick)]
    pk = dr.peak_week(demand, site)
    period = cc[1].select_slider("Period", options=periods,
                                 value=pk if pk in periods else periods[-1])
    if period == pk:
        cc[1].caption("Peak period for this selection.")

    d = demand if site is None else demand[demand["Site"] == site]
    m = st.columns(4)
    cur = d[d["week"] == period]
    m[0].metric("Seats required", f"{int(cur['seats'].sum()):,}")
    if cur["hc"].notna().any():
        m[1].metric("Headcount", f"{int(cur['hc'].sum()):,}")
    if cur["seat_ratio"].notna().any():
        m[2].metric("Avg seat ratio", f"{cur['seat_ratio'].mean():.2f}")
    m[3].metric("Periods in file", len(periods))

    piv = d.pivot_table(index="week", columns="Account", values="seats",
                        aggfunc="sum").fillna(0)
    st.line_chart(piv)
    st.caption("Seats required per period by account.")
    st.dataframe(cur, width="stretch", hide_index=True)

# ═══════════════════════════════════════════════ 3 · RAMP PLAN
with tab_ramp:
    st.caption("Requirement against each site's floors — what fits today, what a "
               "renovation would add, and the shortfall left over.")
    r1, r2, r3 = st.columns(3)
    rperiod = r1.select_slider("Period", options=dr.week_options(demand),
                               value=dr.peak_week(demand), key="ramp_period")
    use_exp = r2.checkbox("Count expansion space", value=True,
                          help="Capacity unlocked by renovation, subject to its lead time")
    horizon = r3.number_input("Only renovations within (weeks)", min_value=0, max_value=52,
                              value=12, step=1, disabled=not use_exp)

    basis = st.radio(
        "Requirement basis", ["Incremental (net of seats already allocated)", "Gross demand"],
        horizontal=True,
        help="The planner's ramp need is the extra seats a programme needs on top of what "
             "it already holds. Gross demand re-plans the site from empty.")
    need_gross = dr.need_by_site(demand, rperiod)
    if basis.startswith("Incremental"):
        held = floors.groupby("site")["allocated"].sum().to_dict()
        need = {s: max(v - held.get(s, 0), 0) for s, v in need_gross.items()}
        st.caption("Ramp need = seats required for the period, less the seats that site "
                   "already holds.")
    else:
        need = need_gross
    known = {s: n for s, n in need.items() if s in set(floors["site"]) and n > 0}
    unknown_sites = {s: n for s, n in need.items() if s not in set(floors["site"])}

    if unknown_sites:
        st.warning("These sites have demand but no floors in the inventory, so they cannot "
                   "be matched: " + ", ".join(f"{k} ({int(v)} seats)"
                                              for k, v in unknown_sites.items()))
        st.caption("Map them to a site in the floor inventory, or add the floors.")

    if not known:
        st.info("No site in the workbook matches the floor inventory. "
                "Showing every site's spare capacity instead.")
        spare = es.rollup(floors, "site")
        st.dataframe(spare, width="stretch", hide_index=True)
    else:
        summ = es.ramp_summary(floors, known, include_expansion=use_exp)
        if use_exp:
            summ.loc[summ["expansion_eta_weeks"] > horizon, "from_expansion"] = 0
            summ["delta"] = -(summ["ramp_need"] - summ["from_available"]
                              - summ["from_expansion"])
        short = summ[summ["delta"] < 0]
        k = st.columns(3)
        k[0].metric("Seats needed", f"{int(summ['ramp_need'].sum()):,}")
        k[1].metric("Placeable", f"{int(summ['from_available'].sum() + summ['from_expansion'].sum()):,}")
        k[2].metric("Shortfall", f"{int(summ['delta'].sum()):,}",
                    delta_color="inverse" if summ["delta"].sum() < 0 else "normal")
        st.dataframe(summ, width="stretch", hide_index=True)

        if not short.empty:
            st.error(f"{len(short)} site(s) short. Options, cheapest first: release trapped "
                     "seats on site, bring a renovation forward, spread to another site, "
                     "or defer the ramp.")
            for _, r in short.iterrows():
                st.markdown(f"- **{r['site']}** short {int(-r['delta'])} — "
                            f"{int(r['trapped_on_site'])} trapped seats on site, "
                            + (f"expansion of {int(r['expansion_space'])} in "
                               f"{r['expansion_eta_weeks']:.0f} weeks"
                               if r["expansion_space"] else "no expansion space"))
        st.markdown("##### Floor by floor")
        st.dataframe(es.match_ramp(floors, known, include_expansion=use_exp,
                                   within_weeks=horizon if use_exp else None),
                     width="stretch", hide_index=True)

# ═══════════════════════════════════════════════ 4 · FLOOR MAP
with tab_map:
    if plan_file is not None:
        plan = load_plan(plan_file.getvalue(), plan_file.name)
    else:
        plan = load_plan(open("data/sample_seat_inventory.csv", "rb").read(),
                         "sample_seat_inventory.csv")
        st.caption("No plan uploaded — using the bundled surveyed floor.")

    seats = plan["seats"].copy()
    if "zone_type" not in seats.columns:
        seats["zone_type"] = "Production"
    for cname, v in [("country", "—"), ("city", "—"), ("site", "SITE"),
                     ("building", "B1"), ("tower", "T1"), ("floor", "F1")]:
        if cname not in seats.columns:
            seats[cname] = v
    sp = sm.validate_seats(seats)
    if sp:
        st.error("Seat inventory cannot be used:\n\n" + "\n".join(f"- {x}" for x in sp))
    else:
        zsum = (seats.groupby("zone").agg(seats=("seat_id", "count"),
                                          suggested=("zone_type", "first")).reset_index()
                .sort_values("seats", ascending=False))
        zsum["Allocate"] = zsum["suggested"].str.lower().eq("production")
        with st.expander("Zones on this floor — untick any that should not take production demand"):
            edited = st.data_editor(zsum[["zone", "seats", "Allocate"]], hide_index=True,
                                    width="stretch", disabled=["zone", "seats"],
                                    key="zone_ed")
        allocatable = edited.loc[edited["Allocate"], "zone"].tolist()
        prod_cap = int(edited.loc[edited["Allocate"], "seats"].sum())

        g1, g2, g3 = st.columns(3)
        level = g1.radio("Colour by", ["Account", "LOB"], horizontal=True)
        labels2, sites2 = dr.site_options(demand)
        spick = g2.selectbox("Demand for", ["All sites"] + labels2, key="map_site")
        msite = None if spick == "All sites" else sites2[labels2.index(spick)]
        mperiod = g3.select_slider("Period", options=dr.week_options(demand),
                                   value=dr.peak_week(demand, msite), key="map_period")

        sl = dr.slice_week(demand, mperiod, msite)
        if sl.empty:
            st.info("No demand for that selection.")
        else:
            sl["site"] = seats["site"].iloc[0]
            sl["building"] = seats["building"].iloc[0]
            sl["tower"] = seats["tower"].iloc[0]
            sl["floor"] = None
            assigned, blocks, unplaced = sm.allocate_seats(sl, seats,
                                                           allocatable_zones=allocatable)
            need_here = int(sl["seats"].sum())
            q = st.columns(4)
            q[0].metric("Seats required", f"{need_here:,}")
            q[1].metric("Production seats", f"{prod_cap:,}")
            q[2].metric("Allocated", f"{int(assigned['account'].notna().sum()):,}")
            q[3].metric("Spare", f"{prod_cap - int(assigned['account'].notna().sum()):,}")

            lvl2 = "account" if level == "Account" else "lob"
            png, pdf = fr.render_floor_map(
                assigned, background=plan.get("background"), extent=plan.get("extent"),
                level=lvl2,
                title=f"Seat allocation by {'client account' if lvl2 == 'account' else 'line of business'}",
                subtitle=f"{mperiod} · {spick} · {need_here:,} seats required · "
                         f"{prod_cap:,} production seats")
            st.image(png, width="stretch")
            e1, e2 = st.columns(2)
            e1.download_button("🖼 Floor map (PNG)", png,
                               file_name=f"Floorcast_FloorMap_{mperiod}_{lvl2}.png",
                               mime="image/png")
            e2.download_button("📄 Floor map (PDF)", pdf,
                               file_name=f"Floorcast_FloorMap_{mperiod}_{lvl2}.pdf",
                               mime="application/pdf")

            seg = sm.zone_security_report(assigned)
            if seg.empty:
                st.success("Segregation clean — every zone held by a single account.")
            else:
                st.warning(f"{len(seg)} zone(s) shared by more than one account.")
                st.dataframe(seg, width="stretch", hide_index=True)

            st.markdown("##### Where a new requirement would fit")
            st.caption("Contiguous blocks big enough for a requirement — replacing the "
                       "select-and-count step done by hand on the sheet.")
            ask = st.number_input("Seats to place", min_value=1, max_value=5000, value=60, step=10)
            fit, short = es.fit_blocks(assigned, int(ask))
            if fit.empty:
                st.info("No free blocks on this floor.")
            else:
                st.dataframe(fit, width="stretch", hide_index=True)
                if short:
                    st.warning(f"{short} seat(s) would not fit on this floor.")
                elif (~fit["whole_block"]).any():
                    st.caption("One block is split — that block would end up shared "
                               "between two accounts unless a partition moves.")

            st.download_button("📊 Seat register (Excel)",
                               _xlsx({"Seat Register": assigned, "Seat Blocks": blocks,
                                      "Segregation": seg}),
                               file_name=f"Floorcast_SeatPlan_{mperiod}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument."
                                    "spreadsheetml.sheet")

# ═══════════════════════════════════════════════ 5 · SCENARIOS
with tab_scen:
    st.markdown("#### Release trapped seats")
    st.caption("What the estate looks like if trapped seats are recovered — a partition "
               "move, an IT rollout, a relocation, or finishing a renovation.")
    tb = es.trapped_breakdown(floors)
    default = tb.loc[tb["releasable"], "trapped_reason"].tolist()
    s1, s2 = st.columns([2, 1])
    reasons = s1.multiselect("Reasons to release", tb["trapped_reason"].tolist(),
                             default=default)
    frac = s2.slider("Proportion recovered", 0.0, 1.0, 0.5, 0.05)
    after = es.release_trapped(floors, reasons, frac)
    b, a = es.estate_totals(floors), es.estate_totals(after)
    k = st.columns(4)
    k[0].metric("Available before", f"{b['available']:,}")
    k[1].metric("Available after", f"{a['available']:,}",
                delta=f"{a['available'] - b['available']:+,}")
    k[2].metric("Trapped after", f"{a['trapped']:,}",
                delta=f"{a['trapped'] - b['trapped']:+,}", delta_color="inverse")
    k[3].metric("Equivalent to", f"{(a['available'] - b['available']) / max(b['total_seats'], 1) * 100:.1f}%",
                help="of the total estate, recovered without new space")
    st.dataframe(after.loc[after["released"] > 0,
                           ["site", "floor_id", "trapped_reason", "released",
                            "available", "trapped"]],
                 width="stretch", hide_index=True)

    st.markdown("#### Sales pipeline")
    st.caption("Demand that has not closed yet. Weighted is the planning view; full is the "
               "stress test — the estate should have an answer if everything lands.")
    p1, p2 = st.columns([2, 1])
    mode = p1.radio("Treat pipeline as", ["exclude", "weighted", "full"], index=1,
                    horizontal=True,
                    format_func=lambda m: {"exclude": "Committed only",
                                           "weighted": "Weighted by probability",
                                           "full": "Full pipeline"}[m])
    ratio = p2.number_input("Seat ratio for pipeline", 1.0, 3.0, 1.2, 0.1)
    ps = dr.pipeline_seats(pipe, mode, ratio)
    pv = ps.groupby("site")["seats"].sum().rename("pipeline_seats").reset_index()
    cap = es.rollup(floors, "site")[["site", "available", "trapped", "expansion_space"]]
    comp = cap.merge(pv, left_on="site", right_on="site", how="outer").fillna(0)
    comp["gap_vs_available"] = comp["available"] - comp["pipeline_seats"]
    st.dataframe(comp, width="stretch", hide_index=True)
    tight = comp[comp["gap_vs_available"] < 0]
    if not tight.empty:
        st.warning("Pipeline exceeds available seats at: "
                   + ", ".join(f"{r['site']} (short {int(-r['gap_vs_available'])})"
                               for _, r in tight.iterrows())
                   + ". Trapped seats and expansion space are the first places to look.")
    else:
        st.success("Every site can absorb the pipeline on today's available seats.")

st.caption("Floorcast · layouts are planning drafts — fire egress, travel distances and "
           "occupancy limits must be verified and certified by a licensed architect.")
