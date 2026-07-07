"""
Floorcast — from forecast to floor plan.

Tab 1: Seat Planner (SeatIQ) — roster-driven seat demand + bay allocation
Tab 2: Forecast → Floor — Erlang C capacity, shift-mix, and floor layout

Run:  streamlit run app.py
"""

import math
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from floorcast_core.generate_roster import generate_roster, PROGRAMS
from floorcast_core.demand_engine import compute_demand, demand_summary
from floorcast_core.optimizer import optimize_allocation, DEFAULT_BAYS
from floorcast_core.capacity_engine import interval_requirements, scheduled_headcount
from floorcast_core.shift_scheduler import solve_shift_mix
from floorcast_core.layout_engine import solve_layout

st.set_page_config(page_title="Floorcast", page_icon="🏢", layout="wide")

st.markdown(
    "<h1 style='color:#0F6B6B;margin-bottom:0'>Floorcast</h1>"
    "<p style='color:#C9962E;font-weight:600;margin-top:0'>From forecast to floor plan.</p>",
    unsafe_allow_html=True,
)

TIMES = [f"{s//2:02d}:{(s%2)*30:02d}" for s in range(48)]
PROG_COLORS = ["#0F6B6B", "#C9962E", "#7C3AED", "#DC2626", "#2563EB", "#059669"]

tab1, tab2 = st.tabs(["🪑 Seat Planner (roster)", "📈 Forecast → Floor"])

# ══════════════════════════════════════════════════════ TAB 1 — SeatIQ
with tab1:
    st.sidebar.header("Roster source")
    uploaded = st.sidebar.file_uploader("Upload roster CSV", type="csv",
        help="Columns: date, agent_id, program, channel, shift, work_mode, status")

    st.sidebar.header("What-if levers")
    wfh_delta = st.sidebar.slider("WFH shift (± pct points)", -30, 30, 0, 5)
    hiring = st.sidebar.slider("New-hire batch (agents)", 0, 300, 0, 25)
    buffer_pct = st.sidebar.slider("Seat buffer %", 0, 20, 5, 1) / 100
    with open("data/sample_roster.csv", "rb") as f:
        st.sidebar.download_button(
            "⬇ Download sample roster", f,
            file_name="sample_roster.csv", mime="text/csv",
            help="Try the upload feature with this 50-agent sample"
        )
    @st.cache_data
    def load_roster(file):
        return pd.read_csv(file) if file else generate_roster()

    roster = load_roster(uploaded).copy()
    rng = np.random.default_rng(7)

    if wfh_delta != 0:
        for prog in roster["program"].unique():
            mask = roster["program"] == prog
            agents = roster.loc[mask, "agent_id"].unique()
            movers = set(rng.choice(agents, size=min(int(len(agents) * abs(wfh_delta) / 100), len(agents)), replace=False))
            roster.loc[mask & roster["agent_id"].isin(movers), "work_mode"] = "WFH" if wfh_delta > 0 else "Office"

    if hiring > 0:
        big = roster.groupby("program")["agent_id"].nunique().idxmax()
        tmpl = roster[roster["program"] == big]
        shifts = tmpl["shift"].value_counts(normalize=True)
        new = [{"date": d, "agent_id": f"NH{i:04d}", "program": big,
                "channel": tmpl["channel"].iloc[0],
                "shift": rng.choice(shifts.index, p=shifts.values),
                "work_mode": "Office", "status": "Working"}
               for i in range(hiring) for d in sorted(roster["date"].unique())]
        roster = pd.concat([roster, pd.DataFrame(new)], ignore_index=True)

    demand = compute_demand(roster)
    summary = demand_summary(demand, roster)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rostered agents", f"{roster['agent_id'].nunique():,}")
    c2.metric("Peak seats needed", f"{int(summary['peak_seats'].sum()):,}")
    c3.metric("Total bay capacity", f"{sum(b[2] for b in DEFAULT_BAYS):,}")
    ratio = roster[roster.work_mode.eq('Office')]['agent_id'].nunique() / max(summary['peak_seats'].sum(), 1)
    c4.metric("Blended sharing ratio", f"{ratio:.2f}")

    st.subheader("Seat demand heatmap (program × time of day)")
    day = st.selectbox("Date", sorted(demand["date"].unique()))
    piv = demand[demand["date"] == day].pivot(index="program", columns="time", values="seats_needed")
    fig = px.imshow(piv, aspect="auto", color_continuous_scale="Teal", labels=dict(color="Seats"))
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Total seat demand vs capacity")
    tot = demand[demand["date"] == day].groupby("time")["seats_needed"].sum().reset_index()
    fig2 = px.area(tot, x="time", y="seats_needed")
    fig2.add_hline(y=sum(b[2] for b in DEFAULT_BAYS), line_dash="dash",
                   line_color="#C9962E", annotation_text="Building capacity")
    fig2.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig2, width="stretch")

    st.subheader("Per-program summary")
    st.dataframe(summary, width="stretch", hide_index=True)

    st.subheader("Optimized bay allocation")
    meta = roster.groupby("program").agg(channel=("channel", "first")).reset_index()
    meta["dedicated"] = meta["program"].map(lambda p: PROGRAMS.get(p, {}).get("dedicated", False))
    req = summary.merge(meta, on="program")
    status, alloc, diag = optimize_allocation(req, buffer_pct=buffer_pct)

    if status == "Optimal":
        st.success(f"Solver: {status} · {diag['bays_used']} bays used · "
                   f"{diag['total_seats_required']} seats placed of {diag['total_capacity']} capacity")
        fig3 = px.bar(alloc, x="bay", y="seats", color="program", text="seats",
                      color_discrete_sequence=PROG_COLORS)
        caps = pd.DataFrame(DEFAULT_BAYS, columns=["bay", "floor", "cap", "quiet"])
        fig3.add_scatter(x=caps["bay"], y=caps["cap"], mode="markers",
                         marker_symbol="line-ew", marker_line_width=2, marker_size=30,
                         marker_line_color="#C9962E", name="Bay capacity")
        fig3.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig3, width="stretch")
        st.dataframe(alloc, width="stretch", hide_index=True)
    else:
        st.error(f"Solver status: {status}. Demand exceeds feasible capacity — "
                 "raise WFH, reduce office headcount, or add bays.")
        st.json(diag["seats_needed_by_program"])

# ══════════════════════════════════════════════════════ TAB 2 — Forecast → Floor
with tab2:
    st.markdown("Give a **12-month forecast** and a **floor**; get FTE, shift mix, seat demand, "
                "and a movable-partition layout — per month.")

    st.subheader("1 · Forecast & program inputs")
    default_programs = pd.DataFrame([
        {"program": "CareVoice",  "daily_volume": 5200, "aht_sec": 380, "growth_pct_12m": 60, "channel": "voice",      "wfh_pct": 15},
        {"program": "ClaimsChat", "daily_volume": 2600, "aht_sec": 540, "growth_pct_12m": 10, "channel": "chat",       "wfh_pct": 35},
        {"program": "BillingOps", "daily_volume": 1400, "aht_sec": 420, "growth_pct_12m": 0,  "channel": "backoffice", "wfh_pct": 50},
    ])
    prog_df = st.data_editor(default_programs, num_rows="dynamic", width="stretch",
        column_config={"channel": st.column_config.SelectboxColumn(options=["voice", "chat", "backoffice"])})

    colA, colB, colC, colD = st.columns(4)
    month = colA.slider("Month of horizon", 1, 12, 1)
    sl_pct = colB.slider("Service level %", 60, 95, 80, 5) / 100
    shrink = colC.slider("Shrinkage %", 10, 45, 30, 5) / 100
    pod_seats = colD.slider("Seats per pod", 4, 8, 6, 1)

    st.subheader("2 · Floor definition")
    colE, colF, colG = st.columns(3)
    rows = colE.slider("Pod rows", 4, 12, 8)
    cols = colF.slider("Pod columns", 4, 14, 10)
    quiet_n = colG.slider("Quiet columns (right side)", 0, 4, 2)
    sqft = rows * cols * pod_seats * 30
    st.caption(f"Floor ≈ {sqft:,} sq ft usable ({rows}×{cols} pods × {pod_seats} seats × ~30 sq ft/seat incl. circulation)")

    def profile(channel):
        x = np.arange(48)
        if channel == "backoffice":
            p = np.where((x >= 18) & (x < 36), 1.0, 0.02)
        else:
            p = np.exp(-0.5 * ((x - 21) / 5) ** 2) + 0.85 * np.exp(-0.5 * ((x - 31) / 5) ** 2) + 0.10
        return p / p.sum()

    @st.cache_data(show_spinner="Running Erlang C + shift optimizer...")
    def run_pipeline(prog_records, month, sl_pct, shrink):
        results = {}
        for r in prog_records:
            vol = r["daily_volume"] * (1 + r["growth_pct_12m"] / 100 * (month - 1) / 11)
            reqs = interval_requirements(vol, list(profile(r["channel"])), r["aht_sec"], sl_target=sl_pct)
            mix, coverage = solve_shift_mix(reqs)
            onsite = np.ceil(coverage * (1 - r["wfh_pct"] / 100)).astype(int)
            results[r["program"]] = dict(
                required=np.array(reqs), coverage=coverage, onsite=onsite, mix=mix,
                scheduled=int(sum(mix.values())),
                total_hc=scheduled_headcount(int(sum(mix.values())), shrink),
                peak_seats=int(onsite.max()), channel=r["channel"])
        return results

    res = run_pipeline(prog_df.to_dict("records"), month, sl_pct, shrink)

    st.subheader(f"3 · Capacity & seats — month {month}")
    mcols = st.columns(len(res))
    for i, (p, d) in enumerate(res.items()):
        mcols[i].metric(p, f"{d['peak_seats']} seats",
                        f"{d['scheduled']} sched · {d['total_hc']} HC", delta_color="off")

    sel = st.selectbox("Coverage detail for program", list(res.keys()))
    d = res[sel]
    figc = go.Figure()
    figc.add_scatter(x=TIMES, y=d["coverage"], fill="tozeroy", name="Scheduled (shift mix)",
                     line=dict(color="#0F6B6B", width=0), fillcolor="rgba(15,107,107,0.25)")
    figc.add_scatter(x=TIMES, y=d["required"], name="Erlang C requirement",
                     line=dict(color="#C9962E", width=2.5, shape="hv"))
    figc.add_scatter(x=TIMES, y=d["onsite"], name="On-site (after WFH)",
                     line=dict(color="#0F6B6B", width=2, dash="dash"))
    figc.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0),
                       legend=dict(orientation="h", y=1.1))
    st.plotly_chart(figc, width="stretch")

    st.subheader("4 · Optimized floor layout")
    pods_needed = {p: math.ceil(d["peak_seats"] / pod_seats) for p, d in res.items()}
    quiet_cols_ix = list(range(cols - quiet_n, cols)) if quiet_n else []
    voice_progs = {p for p, d in res.items() if d["channel"] == "voice"}

    grid, part_ft = solve_layout(rows, cols, pods_needed, quiet_cols_ix, voice_progs)

    if grid is None:
        st.error(f"Infeasible: {sum(pods_needed.values())} pods needed but the floor has "
                 f"{rows*cols} — enlarge the floor, raise WFH, or reduce volumes. "
                 "This is Floorcast telling you 'impossible' before you promise it.")
    else:
        progs = list(res.keys())
        cmap = {p: PROG_COLORS[i % len(PROG_COLORS)] for i, p in enumerate(progs)}
        z = np.zeros((rows, cols)); txt = np.full((rows, cols), "", dtype=object)
        colorscale = [[0, "#E5E7EB"]]
        for i, p in enumerate(progs, start=1):
            colorscale.append([i / len(progs), cmap[p]])
        for r in range(rows):
            for c in range(cols):
                p = grid[r][c]
                z[r][c] = progs.index(p) + 1 if p else 0
                txt[r][c] = p[:4] if p else ""
        figl = go.Figure(go.Heatmap(z=z, text=txt, texttemplate="%{text}",
                                    colorscale=colorscale, showscale=False,
                                    xgap=3, ygap=3, textfont=dict(size=9, color="white")))
        for qc in quiet_cols_ix:
            figl.add_vrect(x0=qc - 0.5, x1=qc + 0.5, line_width=2, line_dash="dot",
                           line_color="#C9962E")
        figl.update_layout(height=90 + rows * 42, margin=dict(l=0, r=0, t=10, b=0),
                           yaxis=dict(autorange="reversed", showticklabels=False),
                           xaxis=dict(showticklabels=False))
        st.plotly_chart(figl, width="stretch")

        lc1, lc2, lc3 = st.columns(3)
        lc1.metric("Pods used", f"{sum(pods_needed.values())} / {rows*cols}")
        lc2.metric("Seats provisioned", f"{sum(math.ceil(d['peak_seats']/pod_seats)*pod_seats for d in res.values()):,}")
        lc3.metric("Partition length", f"{part_ft:,.0f} linear ft")
        st.caption("Gold dotted columns = quiet zone (voice excluded). Layouts are planning drafts — "
                   "fire egress, travel distances, and occupancy limits must be verified and certified "
                   "by a licensed architect before implementation.")

st.caption("Floorcast · reference implementation · synthetic data only — no client or employee data")
