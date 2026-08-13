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
from floorcast_core import scenario as sc
from floorcast_core import onesource as os1
from floorcast_core import roles as rl

st.set_page_config(page_title="Floorcast", page_icon="🏬", layout="wide")

SAMPLES = [
    ("Floor inventory", "1 · required", "data/sample_estate_floors.csv",
     "floor_inventory.csv", "text/csv",
     "One row per floor — seats, allocated, available, trapped, expansion"),
    ("Planning workbook", "2 · required", "data/sample_estate_demand.xlsx",
     "planning_workbook.xlsx",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
     "Account x LOB x site, one column per period"),
    ("Deals not yet won", "3 · optional", "data/sample_pipeline.csv",
     "pipeline.csv", "text/csv",
     "New business sales is chasing, with how likely each is"),
    ("Allocations by floor", "4 · optional", "data/sample_allocations.csv",
     "allocations.csv", "text/csv",
     "Who holds which seats — unlocks consolidation and relocation"),
    ("Restrictions", "5 · optional", "data/sample_restrictions.csv",
     "restrictions.csv", "text/csv",
     "Frozen accounts, dedicated floors, no-colocation pairs"),
    ("Floor plan (PDF)", "6 · optional", "data/sample_floor_plan.pdf",
     "floor_plan.pdf", "application/pdf",
     "A vector CAD plot — unlocks the coloured floor map"),
]


@st.cache_data(show_spinner=False)
def sample_bundle() -> bytes:
    """Every sample in one zip, so a new user gets a working set in one click."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for _, _, path, fname, _, _ in SAMPLES:
            try:
                z.write(path, arcname=fname)
            except FileNotFoundError:
                pass
        z.writestr("README.txt",
                   "Floorcast sample input files\n"
                   "============================\n\n"
                   "Use these as templates: replace the contents, keep the column names.\n\n"
                   + "\n".join(f"{n:22} {fn:24} {d}" for n, _, _, fn, _, d in SAMPLES))
    return buf.getvalue()



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
        p_file = st.file_uploader("3 · Deals not yet won (CSV) — optional", type="csv",
                                  help="account, site, stage, probability, month, hc — one row per deal")
        a_file = st.file_uploader("4 · Allocations by floor (CSV) — optional", type="csv",
                                  help="site, building, floor, account, lob, seats — who holds "
                                       "which seats. Unlocks consolidation and relocation options.")
        r_file = st.file_uploader("5 · Restrictions (CSV) — optional", type="csv",
                                  help="rule, subject, object, note — frozen accounts, "
                                       "dedicated floors, no-colocation pairs, move ceiling")
        plan_file = st.file_uploader("6 · Floor plan (PDF) — optional",
                                     type=["pdf", "csv"],
                                     help="A vector PDF plotted from CAD. Seats are counted "
                                          "from the desk size annotations.")
        st.caption("Anything left empty falls back to the sample estate.")

    st.divider()
    with st.expander("⬇ Sample input files", expanded=True):
        st.caption("Templates — replace the contents, keep the column names.")
        st.download_button("📦 All six files (.zip)", sample_bundle(),
                           file_name="floorcast_sample_inputs.zip", mime="application/zip",
                           width="stretch", key="dl_bundle_side")
        for label, tag, path, fname, mime, _ in SAMPLES:
            try:
                with open(path, "rb") as fh:
                    st.download_button(f"{label}", fh.read(), file_name=fname, mime=mime,
                                       width="stretch", key="dl_" + fname)
            except FileNotFoundError:
                pass

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
@st.cache_data(show_spinner="Reading the planning workbook…")
def load_os_cached(data):
    return os1.read_workbook(io.BytesIO(data))


try:
    os_src = load_os_cached(open("data/sample_one_source.xlsx", "rb").read())
except Exception:
    os_src = None

RULES = rx.Rules(pd.concat([rules_raw, st.session_state["answered_rules"]], ignore_index=True))

unknown = demand.attrs.get("unrecognised_metrics") or []
if unknown:
    st.warning("Metric rows not recognised and ignored: " + ", ".join(map(str, unknown)))

with st.expander("⬇ Sample input files — templates you can download and edit", expanded=False):
    st.caption("The app is running on these right now. Replace the contents, keep the column "
               "names, and upload them from the sidebar.")
    st.download_button("📦 Download all six as a zip", sample_bundle(),
                       file_name="floorcast_sample_inputs.zip", mime="application/zip",
                       key="dl_bundle_main")
    dcols = st.columns(3)
    for i, (label, tag, path, fname, mime, desc) in enumerate(SAMPLES):
        c = dcols[i % 3]
        c.markdown(f"**{label}**  \n<span style='color:#5B6B6B;font-size:0.82em'>{tag} — {desc}"
                   "</span>", unsafe_allow_html=True)
        try:
            with open(path, "rb") as fh:
                c.download_button("Download", fh.read(), file_name=fname, mime=mime,
                                  width="stretch", key="dlm_" + fname)
        except FileNotFoundError:
            c.caption("not bundled")

tab_role, tab_what, tab_estate, tab_demand, tab_ramp, tab_map, tab_scen, tab_qa = st.tabs(
    ["👥 My view", "🎛 What if", "🏢 Estate", "📈 Demand", "🎯 Ramp plan", "🗺 Floor map",
     "🔮 Scenarios", "🔍 Data quality"])



# ═══════════════════════════════════════════════ 0 · MY VIEW
with tab_role:
    who = st.radio("I work in", list(rl.ROLES),
                   horizontal=True, key="role_pick",
                   format_func=lambda r: f"{rl.ROLES[r]['icon']} {r}")
    meta = rl.ROLES[who]
    st.markdown(f"#### {meta['question']}")
    st.caption(f"**What this view holds:** {meta['cares']}  \n"
               f"**The step you take:** {meta['action']}")
    st.divider()

    r_period = st.select_slider("Period", options=dr.week_options(demand),
                                value=dr.peak_week(demand), key="role_period")
    r_res = sc.compute(floors, demand, pipe, sc.levers(period=r_period))
    r_tot = r_res["totals"]

    if who == "Leadership":
        vals = rl.leadership(floors, {**es.estate_totals(floors), **{}})
        cols = st.columns(len(vals))
        for c, (k, v) in zip(cols, vals.items()):
            c.metric(k, v)
        st.markdown("##### Where the money is stuck")
        tb = es.trapped_breakdown(floors)
        st.dataframe(tb[["trapped_reason", "definition", "seats", "share_%", "releasable"]],
                     width="stretch", hide_index=True)
        rec = int(tb.loc[tb["releasable"], "seats"].sum())
        st.info(f"**{rec:,} seats** sit against reasons that could be recovered without new "
                f"space — {rec / max(es.estate_totals(floors)['total_seats'], 1) * 100:.0f}% of "
                "the estate. That is the cheapest capacity available, and the first question "
                "to ask before approving a lease.")

    elif who == "Operations":
        k = st.columns(3)
        k[0].metric("Seats needed", f"{r_tot['incremental_need']:,}")
        k[1].metric("Seats usable", f"{r_tot['usable']:,}")
        k[2].metric("Short", f"{r_tot['shortfall']:,}", delta_color="inverse",
                    delta=f"{r_tot['sites_short']} site(s)" if r_tot['sites_short'] else None)
        st.dataframe(rl.operations(r_res["sites"]), width="stretch", hide_index=True)
        if r_tot["shortfall"]:
            st.error(f"Escalate now for {r_tot['sites_short_names']} — a ramp that is short at "
                     "plan stage is far cheaper to fix than one short at go-live.")
        else:
            st.success("Every site lands. This is the evidence to commit the ramp on.")

    elif who == "Facility":
        fac = rl.facility(floors)
        k = st.columns(3)
        k[0].metric("Renovation in pipeline", f"{int(floors['expansion_space'].sum()):,} seats")
        k[1].metric("Floors with idle space", int((fac.get('idle_%', pd.Series(dtype=float)) > 25).sum()))
        k[2].metric("Unusable seats", f"{int(floors['trapped'].sum()):,}")
        st.dataframe(fac, width="stretch", hide_index=True)
        st.caption("Sorted by what a renovation would unlock. The lead time column is the one "
                   "that decides whether it helps this ramp or the next one.")

    elif who == "Security":
        st.metric("Seats stranded by segregation",
                  f"{int(floors.loc[floors['trapped_reason'].eq('segregation'), 'trapped'].sum()):,}")
        st.markdown("##### Rules in force")
        rs = RULES.summary()
        st.dataframe(rs if not rs.empty else pd.DataFrame({"note": ["No restrictions loaded"]}),
                     width="stretch", hide_index=True)
        st.info("Zone-level segregation is checked on the Floor map tab for any floor with a "
                "plan loaded. A plan that fits on capacity can still breach a segregation "
                "clause — the two are separate tests.")

    elif who == "WFM":
        if os_src is None or os_src["long"].empty:
            st.info("Load the planning workbook on the Data quality tab to see ratio behaviour.")
        else:
            w = rl.wfm(os_src["long"])
            if not w:
                st.info("No usable seat ratios found in the workbook.")
            else:
                k = st.columns(2)
                k[0].metric("Programmes with a ratio", f"{int(w['by_geo']['programmes'].sum()):,}")
                k[1].metric("Ratios above the ceiling", len(w["above_ceiling"]))
                st.markdown("##### Ratios that cannot be right")
                st.dataframe(w["above_ceiling"], width="stretch", hide_index=True)
                st.caption("A ratio is people per seat. Anything above about four is worth "
                           "treating as a data entry error rather than a plan.")
                st.markdown("##### Ratio by geo")
                st.dataframe(w["by_geo"], width="stretch", hide_index=True)

    elif who == "IT":
        q = rl.it(floors)
        st.metric("Seats waiting on provisioning", f"{q['seats']:,}")
        if q["queue"].empty:
            st.success("Nothing waiting on IT.")
        else:
            st.dataframe(q["queue"], width="stretch", hide_index=True)
            st.info("These seats exist and are empty for want of a build. Provisioning them is "
                    "usually the cheapest capacity in the estate — no lease, no construction.")

    elif who == "Client":
        if alloc is None or alloc.empty:
            st.info("Add the allocations file to see a client footprint.")
        else:
            acct = st.selectbox("Account", sorted(alloc["account"].unique()), key="role_acct")
            c = rl.client(alloc, floors, acct)
            k = st.columns(4)
            k[0].metric("Seats held", f"{c['seats']:,}")
            k[1].metric("Floors", c["floors"])
            k[2].metric("Sites", c["sites"])
            k[3].metric("Headroom beside them", f"{c['headroom']:,}")
            st.dataframe(c["detail"], width="stretch", hide_index=True)
            if c["shares_with"]:
                st.warning("Shares a floor with: " + ", ".join(c["shares_with"])
                           + ". Confirm this is permitted under the contract.")
            else:
                st.success("Sits on floors held by no other client.")

    else:  # PMO
        if os_src is None or os_src["programmes"].empty:
            st.info("Load the planning workbook on the Data quality tab to see collection status.")
        else:
            pr = os_src["programmes"]
            iss = os1.quality_report(os_src["long"])
            k = st.columns(4)
            done = int(pr["status"].eq("Updated").sum())
            k[0].metric("Programmes", f"{len(pr):,}")
            k[1].metric("Collected", f"{done / max(len(pr), 1) * 100:.0f}%")
            k[2].metric("Outstanding", f"{len(pr) - done:,}")
            k[3].metric("Flagged to check", f"{iss['programme'].nunique() if not iss.empty else 0:,}")
            st.markdown("##### Who to chase")
            ch = os1.chase_list(pr)
            st.dataframe(ch.head(25), width="stretch", hide_index=True)
            st.download_button("📋 Chase list (CSV)", ch.to_csv(index=False).encode(),
                               file_name="not_updated.csv", mime="text/csv", key="pmo_chase")

# ═══════════════════════════════════════════════ 0 · WHAT IF
with tab_what:
    st.caption("**Move the levers and watch the answer change.** The files are loaded — nothing "
               "needs uploading again. Save a scenario to put it beside another one.")

    periods_w = dr.week_options(demand)
    pk_w = dr.peak_week(demand)
    if "saved_scenarios" not in st.session_state:
        st.session_state["saved_scenarios"] = {}

    lev, res = st.columns([1, 2.35], gap="large")

    with lev:
        st.markdown("##### Levers")
        w_period = st.select_slider("Period", options=periods_w,
                                    value=pk_w if pk_w in periods_w else periods_w[-1],
                                    key="w_period")
        w_uplift = st.slider("Demand vs plan", -0.25, 0.50, 0.0, 0.05,
                             format="%+.0f%%", key="w_uplift",
                             help="What if volumes land above or below forecast")
        st.markdown("**Trapped seats**")
        tb_w = es.trapped_breakdown(floors)
        rec = tb_w.loc[tb_w["releasable"], "trapped_reason"].tolist()
        w_reasons = st.multiselect("Assume recovered", tb_w["trapped_reason"].tolist(),
                                   default=rec, key="w_reasons",
                                   label_visibility="collapsed")
        w_frac = st.slider("How much comes back", 0.0, 1.0, 0.0, 0.05,
                           format="%.0f%%", key="w_frac")
        st.markdown("**Renovation**")
        w_exp = st.toggle("Count expansion space", value=True, key="w_exp")
        w_hor = st.slider("Landing within (weeks)", 0, 52, 12, 2, key="w_hor",
                          disabled=not w_exp)
        st.markdown("**Deals not yet won**")
        w_pipe = st.radio("Deals not yet won", ["exclude", "weighted", "full"], index=0,
                          key="w_pipe", horizontal=True,
                          format_func=lambda m: {"exclude": "Ignore them",
                                                 "weighted": "Likely ones",
                                                 "full": "All of them"}[m],
                          label_visibility="collapsed")

    lv = sc.levers(period=w_period, demand_uplift=w_uplift, release_reasons=w_reasons,
                   release_fraction=w_frac, include_expansion=w_exp, horizon_weeks=w_hor,
                   pipeline_mode=w_pipe)
    now = sc.compute(floors, demand, pipe, lv)
    base_lv = sc.levers(period=w_period)
    base = sc.compute(floors, demand, pipe, base_lv)
    T, BT = now["totals"], base["totals"]

    with res:
        k = st.columns(4)
        k[0].metric("Seats needed", f"{T['incremental_need']:,}",
                    delta=f"{T['incremental_need'] - BT['incremental_need']:+,}" if T['incremental_need'] != BT['incremental_need'] else None,
                    delta_color="inverse")
        k[1].metric("Usable seats", f"{T['usable']:,}",
                    delta=f"{T['usable'] - BT['usable']:+,}" if T['usable'] != BT['usable'] else None)
        k[2].metric("Shortfall", f"{T['shortfall']:,}",
                    delta=f"{T['shortfall'] - BT['shortfall']:+,}" if T['shortfall'] != BT['shortfall'] else None,
                    delta_color="inverse")
        k[3].metric("Sites short", T["sites_short"],
                    delta=f"{T['sites_short'] - BT['sites_short']:+d}" if T['sites_short'] != BT['sites_short'] else None,
                    delta_color="inverse")

        if T["shortfall"] == 0:
            st.success(f"**Everything fits.** {T['usable']:,} usable seats against "
                       f"{T['incremental_need']:,} needed.")
        else:
            st.error(f"**{T['shortfall']:,} seats short** at {T['sites_short_names']}. "
                     f"{T['trapped_left']:,} trapped seats are still not counted.")

        wf = sc.waterfall(now)
        st.markdown("###### How the scenario gets there")
        st.bar_chart(wf.set_index("step")["seats"], height=210)

        st.markdown("###### Site by site")
        show = now["sites"][["site", "allocated", "incremental_need", "available",
                             "released", "expansion_counted", "usable", "gap", "status"]]
        st.dataframe(show, width="stretch", hide_index=True)

        st.caption(f"This scenario: {sc.describe(lv)}")
        sv1, sv2, sv3 = st.columns([2, 1, 1])
        nm = sv1.text_input("Name this scenario", value="", placeholder="e.g. Release half, no renovation",
                            label_visibility="collapsed")
        if sv2.button("Save scenario", width="stretch"):
            st.session_state["saved_scenarios"][nm or f"Scenario {len(st.session_state['saved_scenarios']) + 1}"] = now
            st.rerun()
        if sv3.button("Clear saved", width="stretch"):
            st.session_state["saved_scenarios"] = {}
            st.rerun()

        # ── the floor, under this scenario
        st.markdown("###### The floor, under this scenario")
        try:
            wf_plan = load_plan(plan_file.getvalue(), plan_file.name) if plan_file \
                else load_plan(open("data/sample_seat_inventory.csv", "rb").read(),
                               "sample_seat_inventory.csv")
        except Exception:
            wf_plan = None

        if wf_plan is None or wf_plan["seats"].empty:
            st.info("Load a floor plan to see the map respond to these levers.")
        else:
            wseats = wf_plan["seats"].copy()
            if "zone_type" not in wseats.columns:
                wseats["zone_type"] = "Production"
            for cn, vv in [("country", "-"), ("city", "-"), ("site", "SITE"),
                           ("building", "B1"), ("tower", "T1"), ("floor", "F1")]:
                if cn not in wseats.columns:
                    wseats[cn] = vv
            allocz = sorted(wseats.loc[wseats["zone_type"].astype(str).str.lower()
                                       == "production", "zone"].unique())
            cap_w = int(wseats["zone"].isin(allocz).sum())
            labs_w, sites_w = dr.site_options(demand)
            fitw = {lb: abs(int(dr.slice_week(demand, w_period, sn)["seats"].sum()) - cap_w)
                    for lb, sn in zip(labs_w, sites_w)}
            best_w = min(fitw, key=fitw.get) if fitw else None
            mc1, mc2 = st.columns([1, 1])
            wsite_lab = mc1.selectbox("Floor holds demand for", labs_w,
                                      index=labs_w.index(best_w) if best_w in labs_w else 0,
                                      key="wf_site")
            wlevel = mc2.radio("Colour by", ["Account", "LOB"], horizontal=True, key="wf_lvl")
            wsite = sites_w[labs_w.index(wsite_lab)]

            wsl = dr.slice_week(demand, w_period, wsite)
            if wsl.empty:
                st.info("No demand for that site in this period.")
            else:
                wsl = wsl.copy()
                wsl["seats"] = np.ceil(wsl["seats"] * (1 + w_uplift)).astype(int)
                if w_pipe != "exclude" and pipe is not None and not pipe.empty:
                    pw = dr.pipeline_seats(pipe, w_pipe, 1.2)
                    pw = pw[pw["site"] == wsite]
                    for _, prow in pw.iterrows():
                        if int(prow["seats"]) > 0:
                            wsl = pd.concat([wsl, pd.DataFrame([{
                                "account": f"{prow['account']} (not yet won)",
                                "lob": "New business", "seats": int(prow["seats"])}])],
                                ignore_index=True)
                wsl["site"] = wseats["site"].iloc[0]
                wsl["building"] = wseats["building"].iloc[0]
                wsl["tower"] = wseats["tower"].iloc[0]
                wsl["floor"] = None
                wassigned, _, wunplaced = sm.allocate_seats(wsl, wseats,
                                                            allocatable_zones=allocz)
                placed = int(wassigned["account"].notna().sum())
                mm = st.columns(3)
                mm[0].metric("Seats wanted here", f"{int(wsl['seats'].sum()):,}")
                mm[1].metric("Seats on this floor", f"{cap_w:,}")
                mm[2].metric("Left empty", f"{cap_w - placed:,}")
                st.plotly_chart(
                    fr.plotly_map(wassigned, background=wf_plan.get("background"),
                                  extent=wf_plan.get("extent"),
                                  level="account" if wlevel == "Account" else "lob"),
                    width="stretch", key="wf_map")
                st.caption("Move a lever above and the colours here follow. Hover a seat for "
                           "its id, zone and who holds it.")
                if not wunplaced.empty:
                    st.warning(f"{int(wunplaced['short_by'].sum()):,} seat(s) wanted here have "
                               "nowhere to sit on this floor under this scenario.")

    saved = st.session_state["saved_scenarios"]
    if saved:
        st.markdown("##### Saved scenarios")
        st.dataframe(sc.compare({"Baseline": base, **saved}), width="stretch", hide_index=True)
        st.caption("Baseline is the same period with no levers applied. "
                   "**vs baseline** is seats of shortfall removed — higher is better.")

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

    st.markdown("#### Deals not yet won")
    st.caption("Business sales is chasing but has not signed. The likely view is what you plan "
               "against; all of them is the stress test — the estate should have an answer if "
               "every deal comes in.")
    p1, p2 = st.columns([2, 1])
    mode = p1.radio("Count these deals as", ["exclude", "weighted", "full"], index=1,
                    horizontal=True,
                    format_func=lambda m: {"exclude": "Signed business only",
                                           "weighted": "Weighted by how likely",
                                           "full": "Every deal we are chasing"}[m])
    ratio = p2.number_input("Seat ratio for these deals", 1.0, 3.0, 1.2, 0.1)
    ps = dr.pipeline_seats(pipe, mode, ratio)
    pv = ps.groupby("site")["seats"].sum().rename("seats_if_won").reset_index()
    cap = es.rollup(floors, "site")[["site", "available", "trapped", "expansion_space"]]
    comp = cap.merge(pv, left_on="site", right_on="site", how="outer").fillna(0)
    comp["gap_vs_available"] = comp["available"] - comp["seats_if_won"]
    st.dataframe(comp, width="stretch", hide_index=True)
    tight = comp[comp["gap_vs_available"] < 0]
    if not tight.empty:
        st.warning("These deals would need more seats than are free at: "
                   + ", ".join(f"{r['site']} (short {int(-r['gap_vs_available'])})"
                               for _, r in tight.iterrows())
                   + ". Trapped seats and expansion space are the first places to look.")
    else:
        st.success("Every site could absorb these deals on the seats free today.")

# ═══════════════════════════════════════════════ 7 · DATA QUALITY
with tab_qa:
    st.caption("**Is the plan built on numbers that add up, and how much of it has arrived?** "
               "Point this at the full planning workbook — one tab per geo — rather than a "
               "single-sheet extract.")
    q_file = st.file_uploader("Planning workbook (multi-tab Excel)", type=["xlsx", "xls"],
                              key="qa_file",
                              help="The collection file with a tab per country and a block of "
                                   "metric rows per programme")

    @st.cache_data(show_spinner="Reading the workbook…")
    def load_os(data):
        return os1.read_workbook(io.BytesIO(data))

    try:
        src = load_os(q_file.getvalue()) if q_file else \
            load_os(open("data/sample_one_source.xlsx", "rb").read())
    except Exception as exc:
        st.error(f"Could not read that workbook: {exc}")
        src = None

    if src is None or src["programmes"].empty:
        st.info("No programme blocks found. This view expects the collection format — "
                "Site / Account / Information / Data columns, with a block of metric rows "
                "starting at 'Total TMs' for each programme.")
    else:
        progs, longd = src["programmes"], src["long"]
        if not q_file:
            st.caption("Running on a bundled sample in the collection format.")
        issues = os1.quality_report(longd)
        comp = os1.completion(progs)

        k = st.columns(4)
        k[0].metric("Programmes", f"{len(progs):,}")
        k[1].metric("Geos", progs["geo"].nunique())
        done = int(progs["status"].eq("Updated").sum())
        k[2].metric("Collected", f"{done / max(len(progs), 1) * 100:.0f}%",
                    delta=f"{len(progs) - done} outstanding", delta_color="inverse")
        n_high = int((issues["severity"] == "High").sum()) if not issues.empty else 0
        k[3].metric("Programmes to check", f"{issues['programme'].nunique() if not issues.empty else 0:,}",
                    delta=f"{n_high} high severity" if n_high else None, delta_color="inverse")

        st.warning(os1.coverage_note(progs, longd))

        qa1, qa2, qa3 = st.tabs(["What does not add up", "Collection status", "Who to chase"])
        with qa1:
            if issues.empty:
                st.success("Every programme passes the checks.")
            else:
                st.dataframe(os1.quality_summary(issues, progs), width="stretch", hide_index=True)
                st.caption("Seats Required is taken from the workbook as authoritative. Where a "
                           "recomputation disagrees it is flagged, never overwritten — the "
                           "guidance is that the calculation belongs in the cap plans.")
                sev = st.multiselect("Severity", ["High", "Medium", "Low"],
                                     default=["High", "Medium"], key="qa_sev")
                view = issues[issues["severity"].isin(sev)]
                st.dataframe(view[["geo", "site", "account", "programme", "issue",
                                   "severity", "worst_period", "detail"]],
                             width="stretch", hide_index=True, height=320)
                st.download_button("📊 Issue list (Excel)",
                                   _xlsx({"Issues": issues,
                                          "Summary": os1.quality_summary(issues, progs)}),
                                   file_name="Floorcast_DataQuality.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument."
                                        "spreadsheetml.sheet")
        with qa2:
            st.dataframe(comp, width="stretch", hide_index=True)
            if not comp.empty:
                st.bar_chart(comp.set_index("geo")["completion_%"], height=240)
                worst = comp.iloc[0]
                st.caption(f"Lowest is {worst['geo']} at {worst['completion_%']}% "
                           f"({int(worst['not_updated'])} of {int(worst['programmes'])} "
                           "programmes outstanding).")
        with qa3:
            chase = os1.chase_list(progs)
            if chase.empty:
                st.success("Nothing outstanding.")
            else:
                st.dataframe(chase, width="stretch", hide_index=True)
                st.download_button("📋 Chase list (CSV)", chase.to_csv(index=False).encode(),
                                   file_name="not_updated.csv", mime="text/csv")
                st.caption("Generated from the workbook itself, so it cannot drift out of step "
                           "with the data the way a maintained list does.")

st.caption("Floorcast · layouts are planning drafts — fire egress, travel distances and "
           "occupancy limits must be verified and certified by a licensed architect.")
