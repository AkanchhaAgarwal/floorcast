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
from floorcast_core import moves as mv
from floorcast_core import restrictions as rx

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


@st.cache_data(show_spinner=False)
def load_alloc(data):
    return pd.read_csv(io.BytesIO(data))


@st.cache_data(show_spinner=False)
def load_rules(data):
    return pd.read_csv(io.BytesIO(data))


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
    use_sample = st.toggle("Use sample data", value=True,
                           help="Turn this off to plan with your own files.")
    st.caption("Sample data is a fictional estate — nothing here is real.")
    st.divider()
    f_file = d_file = p_file = a_file = r_file = plan_file = None
    if not use_sample:
        f_file = st.file_uploader("1 · Floor inventory (CSV)", type="csv",
                                  help="geo, country, city, site, building, floor, "
                                       "total_seats, allocated, available, trapped, "
                                       "trapped_reason, expansion_space, expansion_eta_weeks")
        d_file = st.file_uploader("2 · Planning workbook (Excel)", type=["xlsx", "xls"],
                                      help="Account / LOB / Site with metric rows and one "
                                           "column per period")
        p_file = st.file_uploader("3 · Sales pipeline (CSV) — optional", type="csv",
                                          help="account, site, stage, probability, month, hc")
        r_file = st.file_uploader("5 · Restrictions (CSV) — optional", type="csv",
                                  help="rule, subject, object, note — frozen accounts, "
                                       "dedicated floors, no-colocation pairs, move ceiling")
        a_file = st.file_uploader("4 · Allocations by floor (CSV) — optional", type="csv",
                                  help="site, building, floor, account, lob, seats — who holds "
                                       "which seats. Unlocks consolidation and relocation options.")
        plan_file = st.file_uploader("5 · Floor plan (PDF) — optional",
                                     type=["pdf", "csv"],
                                     help="A vector PDF plotted from CAD. Seats are counted "
                                          "from the desk size annotations.")
        st.caption("Anything left empty falls back to the sample estate.")

floors_raw = load_floors(f_file.getvalue()) if f_file else \
    load_floors(open("data/sample_estate_floors.csv", "rb").read())
demand = load_demand(d_file.getvalue()) if d_file else \
    load_demand(open("data/sample_estate_demand.xlsx", "rb").read())
pipe = load_pipeline(p_file.getvalue()) if p_file else \
    load_pipeline(open("data/sample_pipeline.csv", "rb").read())
alloc_raw = load_alloc(a_file.getvalue()) if a_file else \
    load_alloc(open("data/sample_allocations.csv", "rb").read())
rules_raw = load_rules(r_file.getvalue()) if r_file else \
    load_rules(open("data/sample_restrictions.csv", "rb").read())

problems = es.validate_floors(floors_raw)
if problems:
    st.error("Floor inventory cannot be used:\n\n" + "\n".join(f"- {p}" for p in problems))
    st.stop()
floors = es.prepare(floors_raw)

alloc_problems = mv.validate_allocations(alloc_raw, floors)
alloc = None if alloc_problems else mv.prepare_moves(alloc_raw, floors)

rule_problems = rx.validate(rules_raw)
if rule_problems:
    st.warning("Restrictions file ignored:\n\n" + "\n".join(f"- {p}" for p in rule_problems))
    rules_raw = rx.empty()
if "answered_rules" not in st.session_state:
    st.session_state["answered_rules"] = rx.empty()
RULES = rx.Rules(pd.concat([rules_raw, st.session_state["answered_rules"]], ignore_index=True))

unknown = demand.attrs.get("unrecognised_metrics") or []
if unknown:
    st.warning("Metric rows not recognised and ignored: " + ", ".join(map(str, unknown)))

tab_estate, tab_demand, tab_ramp, tab_map, tab_scen = st.tabs(
    ["🏢 Estate", "📈 Demand", "🎯 Ramp plan", "🗺 Floor map", "🔮 Scenarios"])

# ═══════════════════════════════════════════════ 1 · ESTATE
with tab_estate:
    st.caption("**How much space do we have, and how much of it can we actually use?**")
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
    st.caption("Seats that physically exist and are paid for, but cannot be sold to a "
               "client — stranded inside another client's secure zone, waiting on IT, or "
               "in a block too small to allocate. Tracked by reason, because the reason "
               "decides whether it can be recovered.")
    tb = es.trapped_breakdown(floors)
    st.dataframe(tb, width="stretch", hide_index=True)
    rel = int(tb.loc[tb["releasable"], "seats"].sum())
    st.info(f"**{rel:,} of {t['trapped']:,} trapped seats** sit against reasons that a "
            f"partition move, an IT rollout or a relocation could address — "
            f"{rel / t['total_seats'] * 100:.1f}% of the estate. "
            "Model that in the Scenarios tab before committing to new space.")

# ═══════════════════════════════════════════════ 2 · DEMAND
with tab_demand:
    st.caption("**How many seats does each client need, and when?**")
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
    st.caption("**Will the ramp fit, and where does it break?**")
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
            st.error(f"{len(short)} site(s) short. Options, cheapest first: **1** release "
                     "trapped seats · **2** bring a renovation forward · **3** consolidate a "
                     "fragmented account · **4** relocate a small one · **5** move partitions · "
                     "**6** spread to another site or defer the ramp.")
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

        # ── moving people: consolidation and relocation
        st.markdown("##### Could moving people help?")
        if alloc_problems:
            st.info("Add an allocations file — who holds which seats on each floor — to see "
                    "consolidation and relocation options.\n\n"
                    + "\n".join(f"- {p}" for p in alloc_problems))
        elif alloc is None or alloc.empty:
            st.info("Add an allocations file to see consolidation and relocation options.")
        else:
            st.caption("Moving people **within a site does not create seats** — a seat freed on "
                       "one floor is consumed on another. What it creates is a block big enough "
                       "for one client to have to itself. Options are ranked by seats moved, "
                       "because every move is an IT task and a weekend.")
            mc1, mc2 = st.columns([1, 1])
            msite = mc1.selectbox("Site to rearrange", sorted(alloc["site"].unique()), key="mv_site")
            default_need = int(abs(summ.loc[summ["site"] == msite, "delta"].sum())) if msite in set(summ["site"]) else 0
            block = mc2.number_input("Contiguous block wanted (seats)", min_value=1, max_value=2000,
                                     value=max(default_need, 60), step=10, key="mv_block")

            frag = mv.fragmentation(alloc[alloc["site"] == msite])
            cap = RULES.max_moves
            opts = mv.relocation_options(alloc, int(block), msite, max_moves=cap, rules=RULES)
            cons = mv.consolidation_options(alloc, rules=RULES)
            cons = cons[cons["site"] == msite] if not cons.empty else cons
            if cap:
                st.caption(f"Options above the {cap}-seat move ceiling are hidden.")

            t_a, t_b, t_c, t_d = st.tabs(["Open a block (step 4)", "Consolidate (step 3)",
                                          "Fragmentation", "Restrictions"])
            with t_a:
                if opts.empty:
                    st.info("No floor at this site could open a block that size, even if it were "
                            "emptied. This is a capacity problem, not a layout one.")
                else:
                    best = mv.move_cost_summary(opts)
                    if best.get("workable"):
                        st.success(f"Cheapest option: move **{best['cheapest_moves']} seats** "
                                   f"({best['accounts']}) to open **{best['block_opened']} "
                                   f"contiguous seats** on {best['floor']}.")
                    else:
                        st.warning("Every option needs somewhere for the movers to go, and this "
                                   "site has no room elsewhere. Look at trapped seats or another site.")
                    st.dataframe(opts, width="stretch", hide_index=True)
            with t_b:
                if cons.empty:
                    st.info("No account at this site is split across floors.")
                else:
                    st.dataframe(cons, width="stretch", hide_index=True)
                    doable = cons[cons["action"] == "Consolidate"]
                    if not doable.empty:
                        r = doable.iloc[0]
                        st.success(f"**{r['account']}** can be consolidated onto {r['to']} by "
                                   f"moving {int(r['seats_moved'])} seats — freeing the same "
                                   "number as one contiguous block.")
            with t_c:
                if frag.empty:
                    st.success("No account at this site is spread across more than one floor.")
                else:
                    st.dataframe(frag, width="stretch", hide_index=True)
                    st.caption("An account on several floors is harder to grow and harder to "
                               "segregate. The smallest block is usually the cheapest to move.")
            with t_d:
                st.caption("Rules the options above obey. Anything the files already answer is "
                           "not asked again — and what you answer here can be saved as a file so "
                           "it becomes an input next time, reviewable by someone other than you.")
                have = RULES.summary()
                if have.empty:
                    st.info("No restrictions in force — every account is treated as movable and "
                            "every floor as shared.")
                else:
                    st.dataframe(have, width="stretch", hide_index=True)

                qs = rx.open_questions(alloc, floors, RULES)
                if not qs:
                    st.success("The files answer every question the planner needs.")
                else:
                    st.markdown(f"**{len(qs)} question(s) the files leave open**")
                    ans = {}
                    for q in qs[:8]:
                        cq, ca = st.columns([3, 1])
                        cq.markdown(f"{q['question']}  \n*{q['affects']}*")
                        if q["rule"] == "max_moves":
                            pick = ca.number_input("seats", min_value=0, max_value=2000, value=0,
                                                   step=10, key="q_" + q["key"],
                                                   label_visibility="collapsed")
                            ans[q["key"]] = pick
                        else:
                            ans[q["key"]] = ca.radio("answer", q["options"], horizontal=False,
                                                     key="q_" + q["key"],
                                                     label_visibility="collapsed")
                    new_rules = rx.answers_to_rules(ans, qs)
                    b1, b2 = st.columns(2)
                    if b1.button("Apply these answers", width="stretch"):
                        st.session_state["answered_rules"] = new_rules
                        st.rerun()
                    if not new_rules.empty:
                        b2.download_button("Save as restrictions.csv",
                                           new_rules.to_csv(index=False).encode(),
                                           file_name="restrictions.csv", mime="text/csv",
                                           width="stretch")

# ═══════════════════════════════════════════════ 4 · FLOOR MAP
with tab_map:
    st.caption("**Which client sits in which seat?**")
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
        n_prod = int(zsum.loc[zsum["Allocate"], "seats"].sum())
        n_supp = int(zsum.loc[~zsum["Allocate"], "seats"].sum())
        supp_names = ", ".join(zsum.loc[~zsum["Allocate"], "zone"].head(4))
        with st.expander(f"Zones — {n_prod} production seats"
                         + (f", {n_supp} held back as support ({supp_names})" if n_supp else "")):
            st.caption("Meeting rooms, IT and HR rooms and manager cabins are recognised from "
                       "their names and held out of the pool. Tick or untick to override.")
            edited = st.data_editor(zsum[["zone", "seats", "Allocate"]], hide_index=True,
                                    width="stretch", disabled=["zone", "seats"],
                                    key="zone_ed")
        allocatable = edited.loc[edited["Allocate"], "zone"].tolist()
        prod_cap = int(edited.loc[edited["Allocate"], "seats"].sum())

        g1, g2, g3 = st.columns(3)
        level = g1.radio("Colour by", ["Account", "LOB"], horizontal=True)
        labels2, sites2 = dr.site_options(demand)
        # start on the site whose demand best matches this floor, so the first
        # thing the user sees is a map that makes sense
        pk_all = dr.peak_week(demand)
        fit = {}
        for lb, sname in zip(labels2, sites2):
            n = int(dr.slice_week(demand, pk_all, sname)["seats"].sum())
            fit[lb] = abs(n - prod_cap)
        best = min(fit, key=fit.get) if fit else None
        opts = labels2 + ["All sites"]
        spick = g2.selectbox("This floor holds demand for", opts,
                             index=opts.index(best) if best in opts else 0,
                             key="map_site")
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
            if need_here > prod_cap * 1.2:
                st.warning(
                    f"**{spick} needs {need_here:,} seats but this floor holds {prod_cap:,}.** "
                    "Demand is for the whole site, and a site usually has several floors, so "
                    "the first client fills the plan and the rest have nowhere to go — which "
                    "is why the map comes out one colour. Choose an earlier period, or a site "
                    "sized to this floor. The Ramp plan tab is where the shortfall belongs.")
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
    st.caption("**What are our options when it does not fit?**")
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
