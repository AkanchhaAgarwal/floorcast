"""
Floorcast — facility-management seat planning for contact centers.

Seat forecast (account × LOB × geography) + floor inventory in →
secure floor maps, seat plan rollups, named seat assignment, and DXF out.

Run:  streamlit run app.py
"""

import io
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from floorcast_core.facility_engine import allocate, rollups, assign_employees, export_dxf

st.set_page_config(page_title="Floorcast", page_icon="🏬", layout="wide")

ACC_COLORS = ["#0B7A4B", "#E07B00", "#6D28D9", "#DC2626", "#2563EB", "#0F766E",
              "#B45309", "#9D174D"]

st.markdown(
    "<h1 style='color:#0F6B6B;margin-bottom:0'>Floorcast</h1>"
    "<p style='color:#C9962E;font-weight:600;margin-top:0'>"
    "Facility-management seat planning — from seat forecast to floor map.</p>",
    unsafe_allow_html=True,
)
st.markdown("Give a **seat forecast** (client account × LOB × geography) and a **floor inventory**; "
            "get seat plan rollups, security-zoned floor maps, named seat assignment, and a CAD (DXF) draft. "
            "Accounts are **security boundaries** — dedicated contiguous zones; LOBs are open within their account.")

# ────────────────────────────────────────────── inputs
st.subheader("1 · Inputs")
c1, c2, c3 = st.columns(3)
fc_file = c1.file_uploader("Seat forecast CSV", type="csv",
    help="account, lob, country, city, site, building, tower, floor(optional), seats")
fl_file = c2.file_uploader("Floor inventory CSV", type="csv",
    help="country, city, site, building, tower, floor, seat_rows, seat_cols")
em_file = c3.file_uploader("Employee roster CSV (optional)", type="csv",
    help="employee_id, employee_name, account, lob")

fac_fc = pd.read_csv(fc_file) if fc_file else pd.read_csv("data/sample_seat_forecast.csv")
fac_fl = pd.read_csv(fl_file) if fl_file else pd.read_csv("data/sample_floor_inventory.csv")
fac_em = pd.read_csv(em_file) if em_file else pd.read_csv("data/sample_employee_roster.csv")
if not (fc_file and fl_file):
    st.caption("Running on bundled sample data — upload your own files to replace it.")

with st.expander("⬇ Download sample input files"):
    s1, s2, s3 = st.columns(3)
    for col, path, label in [(s1, "data/sample_seat_forecast.csv", "Seat forecast"),
                             (s2, "data/sample_floor_inventory.csv", "Floor inventory"),
                             (s3, "data/sample_employee_roster.csv", "Employee roster")]:
        with open(path, "rb") as f:
            col.download_button(label, f, file_name=path.split("/")[-1], mime="text/csv")

# ────────────────────────────────────────────── allocation
floor_maps, blocks, unplaced = allocate(fac_fc, fac_fl)
roll = rollups(blocks, fac_fl)
asg = assign_employees(blocks, floor_maps, fac_em)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Seats forecast", f"{int(fac_fc['seats'].sum()):,}")
m2.metric("Portfolio capacity", f"{int((fac_fl['seat_rows']*fac_fl['seat_cols']).sum()):,}")
m3.metric("Floors in plan", len(floor_maps))
m4.metric("Employees seated", f"{int((asg['status']=='Assigned').sum()):,}")

if not unplaced.empty:
    st.error(f"{len(unplaced)} demand line(s) could not be placed — capacity exhausted. "
             "This is Floorcast saying 'impossible' before you promise it.")
    st.dataframe(unplaced, width="stretch", hide_index=True)

# ────────────────────────────────────────────── rollups
st.subheader("2 · Seat plan rollups")
lvl = st.radio("Level", ["city", "site", "floor"], horizontal=True)
if lvl in roll:
    st.dataframe(roll[lvl], width="stretch", hide_index=True)

# ────────────────────────────────────────────── floor maps
st.subheader("3 · Floor maps (security-zoned)")
accounts = sorted(blocks["account"].unique()) if not blocks.empty else []
acc_cmap = {a: ACC_COLORS[i % len(ACC_COLORS)] for i, a in enumerate(accounts)}
fkey = st.selectbox("Floor", list(floor_maps.keys()),
                    format_func=lambda k: k.replace("|", " / "))
grid = floor_maps[fkey]
R, C = grid.shape
emp_lookup = {a["seat_id"]: f"{a['employee_name']} ({a['employee_id']})"
              for _, a in asg[asg["status"] == "Assigned"].iterrows()}

z = np.zeros((R, C)); txt = np.full((R, C), "", dtype=object)
hover = np.full((R, C), "", dtype=object)
colorscale = [[0, "#FFFFFF"]] + [[(i + 1) / max(len(accounts), 1), acc_cmap[a]]
                                 for i, a in enumerate(accounts)]
for r in range(R):
    for c in range(C):
        cell = grid[r][c]
        if cell:
            z[r][c] = accounts.index(cell["account"]) + 1
            txt[r][c] = cell["lob"][:3]
            hover[r][c] = (f"{cell['seat_id']}<br>{cell['account']} / {cell['lob']}<br>"
                           f"{emp_lookup.get(cell['seat_id'], 'Unassigned')}")
        else:
            hover[r][c] = "Empty"
figf = go.Figure(go.Heatmap(z=z, text=txt, texttemplate="%{text}",
                            customdata=hover, hovertemplate="%{customdata}<extra></extra>",
                            colorscale=colorscale, showscale=False,
                            xgap=2, ygap=2, textfont=dict(size=8, color="white")))
figf.update_layout(height=60 + R * 30, margin=dict(l=0, r=0, t=5, b=0),
                   yaxis=dict(autorange="reversed", showticklabels=False),
                   xaxis=dict(showticklabels=False))
st.plotly_chart(figf, width="stretch")
st.caption("Color = client account (security zone) · label = LOB · hover a seat for its ID and occupant. "
           + " · ".join(f"{a}" for a in accounts))

# ────────────────────────────────────────────── assignment
st.subheader("4 · Named seat assignment")
st.dataframe(asg, width="stretch", hide_index=True, height=260)
n_no = int((asg["status"] != "Assigned").sum())
if n_no:
    st.warning(f"{n_no} employee(s) without a seat — add capacity or increase forecast seats.")

# ────────────────────────────────────────────── exports
st.subheader("5 · Exports")
d1, d2 = st.columns(2)
xbuf = io.BytesIO()
with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
    for name, df_ in [("Rollup City", roll.get("city")), ("Rollup Site", roll.get("site")),
                      ("Rollup Floor", roll.get("floor")), ("Seat Blocks", blocks),
                      ("Seat Assignment", asg)]:
        if df_ is not None and not df_.empty:
            df_.to_excel(xw, sheet_name=name, index=False)
d1.download_button("📊 Seat plan report (Excel)", xbuf.getvalue(),
    file_name="Floorcast_SeatPlan.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    help="City/site/floor rollups · seat blocks · assignment register")
export_dxf(floor_maps, "/tmp/floorcast_plan.dxf")
with open("/tmp/floorcast_plan.dxf", "rb") as fdxf:
    d2.download_button("📐 CAD draft (DXF)", fdxf,
        file_name="Floorcast_FloorPlan.dxf", mime="application/dxf",
        help="Opens in AutoCAD — draft only")

st.caption("Floorcast · layouts are planning drafts — fire egress, travel distances, and occupancy "
           "limits must be verified and certified by a licensed architect before implementation. "
           "Bundled data is synthetic; no client or employee data.")
