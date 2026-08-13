"""
Floorcast — Floor Map Renderer

Draws the seat allocation on top of the real floor plan: the plan itself is the
background, and every seat is filled with the colour of the account (or LOB)
holding it. Unallocated seats stay grey so spare capacity is visible.

Output is a PNG for the screen and a PDF for circulation.
"""

import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

# distinguishable at small marker size, and safe on a white plan
PALETTE = ["#0B7A4B", "#E07B00", "#6D28D9", "#C2185B", "#1565C0", "#00838F",
           "#8D6E63", "#AD1457", "#2E7D32", "#EF6C00", "#4527A0", "#00695C"]
UNALLOCATED = "#DDDDDD"
TRAPPED = "#C9A227"        # present, paid for, not usable


def colour_map(labels):
    labels = [l for l in labels if l is not None]
    return {l: PALETTE[i % len(PALETTE)] for i, l in enumerate(sorted(set(labels)))}


def render_floor_map(seats, background=None, extent=None, level="account",
                     title="", subtitle="", desk_w=900, desk_d=700,
                     show_zone_names=True, figsize=(11.7, 16.5), dpi=140):
    """seats: DataFrame with x_mm, y_mm, zone, account, lob.

    level: 'account' colours by client account (the security boundary);
           'lob'     colours by line of business inside each account.
    Returns (png_bytes, pdf_bytes).
    """
    col = "account" if level == "account" else "lob"
    seats = seats.copy()
    if col not in seats.columns:
        seats[col] = None
    key = seats[col].where(seats[col].notna(), None)
    cmap = colour_map(key.tolist())

    fig, ax = plt.subplots(figsize=figsize)
    if background is not None and extent is not None:
        ax.imshow(background, extent=extent, origin="upper", zorder=0,
                  interpolation="bilinear")

    for _, s in seats.iterrows():
        v = s[col] if s[col] is not None and s[col] == s[col] else None
        c = cmap.get(v, UNALLOCATED)
        ax.add_patch(Rectangle((s["x_mm"] - desk_w / 2, s["y_mm"] - desk_d / 2),
                               desk_w, desk_d, facecolor=c,
                               edgecolor="white", linewidth=0.3,
                               alpha=0.95 if v else 0.75, zorder=3))

    if show_zone_names and "zone" in seats.columns:
        for z, g in seats.groupby("zone"):
            ax.text(g["x_mm"].mean(), g["y_mm"].max() + 1400, str(z),
                    ha="center", va="bottom", fontsize=7.5, color="#333333",
                    zorder=4,
                    bbox=dict(boxstyle="round,pad=0.18", fc="white",
                              ec="#BBBBBB", lw=0.4, alpha=0.85))

    counts = seats[seats[col].notna()].groupby(col).size().to_dict()
    n_free = int(seats[col].isna().sum())
    handles = [Line2D([0], [0], marker="s", linestyle="", markersize=9,
                      markerfacecolor=cmap[k], markeredgecolor="white",
                      label=f"{k} — {v} seats")
               for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    if n_free:
        handles.append(Line2D([0], [0], marker="s", linestyle="", markersize=9,
                              markerfacecolor=UNALLOCATED, markeredgecolor="#AAAAAA",
                              label=f"Unallocated — {n_free} seats"))
    ax.legend(handles=handles, loc="lower right", fontsize=8.5, frameon=True,
              framealpha=0.95, title=("Client account" if level == "account"
                                      else "Line of business"),
              title_fontsize=9)

    if background is not None and extent is not None:
        ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
    else:
        pad = 3000
        ax.set_xlim(seats["x_mm"].min() - pad, seats["x_mm"].max() + pad)
        ax.set_ylim(seats["y_mm"].min() - pad, seats["y_mm"].max() + pad)
    ax.set_aspect("equal"); ax.axis("off")
    if title:
        ax.set_title(title, fontsize=13, fontweight="bold", loc="left", pad=14)
    if subtitle:
        ax.text(0, 1.005, subtitle, transform=ax.transAxes, fontsize=9,
                color="#555555", va="bottom")
    fig.tight_layout()

    png, pdf = io.BytesIO(), io.BytesIO()
    fig.savefig(png, format="png", dpi=dpi, facecolor="white", bbox_inches="tight")
    fig.savefig(pdf, format="pdf", facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return png.getvalue(), pdf.getvalue()


# ────────────────────────────── live, interactive version
def plotly_map(seats, background=None, extent=None, level="account",
               title="", desk_w=900, desk_d=700):
    """An interactive floor map for the what-if dashboard.

    The static renderer above produces a picture to print. This one produces a
    figure that redraws in a fraction of a second when a lever moves, and lets
    the planner hover a seat to see who holds it. Same colours, same allocation —
    a different job.
    """
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go

    col = "account" if level == "account" else "lob"
    seats = seats.copy()
    if col not in seats.columns:
        seats[col] = None
    cmap = colour_map(seats[col].dropna().tolist())

    fig = go.Figure()
    if background is not None and extent is not None:
        from PIL import Image
        img = Image.fromarray(background.astype("uint8"))
        fig.add_layout_image(dict(source=img, xref="x", yref="y",
                                  x=extent[0], y=extent[3],
                                  sizex=extent[1] - extent[0],
                                  sizey=extent[3] - extent[2],
                                  sizing="stretch", opacity=1.0, layer="below"))

    trapped = seats[seats.get("seat_status", "usable").eq("trapped")] \
        if "seat_status" in seats.columns else seats.iloc[0:0]
    if not trapped.empty:
        why = trapped.get("trapped_reason", pd.Series("", index=trapped.index)).fillna("")
        fig.add_trace(go.Scatter(
            x=trapped["x_mm"], y=trapped["y_mm"], mode="markers",
            name=f"Unusable ({len(trapped)})",
            marker=dict(size=9, color=TRAPPED, symbol="square-open",
                        line=dict(width=1.6, color=TRAPPED)),
            customdata=np.stack([trapped["seat_id"], trapped["zone"], why], axis=-1),
            hovertemplate="%{customdata[0]}<br>Zone %{customdata[1]}"
                          "<br>Unusable — %{customdata[2]}<extra></extra>"))

    free = seats[seats[col].isna()]
    if "seat_status" in seats.columns:
        free = free[free["seat_status"].ne("trapped")]
    if not free.empty:
        fig.add_trace(go.Scatter(
            x=free["x_mm"], y=free["y_mm"], mode="markers", name="Unallocated",
            marker=dict(size=9, color=UNALLOCATED, symbol="square",
                        line=dict(width=0.6, color="#9E9E9E")),
            customdata=free[["seat_id", "zone"]].values,
            hovertemplate="%{customdata[0]}<br>Zone %{customdata[1]}"
                          "<br>Unallocated<extra></extra>"))

    for name, grp in seats[seats[col].notna()].groupby(col):
        fig.add_trace(go.Scatter(
            x=grp["x_mm"], y=grp["y_mm"], mode="markers",
            name=f"{name} ({len(grp)})",
            marker=dict(size=9, color=cmap.get(name, "#888888"), symbol="square",
                        line=dict(width=0.6, color="white")),
            customdata=grp[["seat_id", "zone"]].values,
            hovertemplate="%{customdata[0]}<br>Zone %{customdata[1]}<br>"
                          + str(name) + "<extra></extra>"))

    for z, g in seats.groupby("zone"):
        fig.add_annotation(x=g["x_mm"].mean(), y=g["y_mm"].max() + 1400, text=str(z),
                           showarrow=False, font=dict(size=10, color="#444444"),
                           bgcolor="rgba(255,255,255,0.75)")

    if extent:
        fig.update_xaxes(range=[extent[0], extent[1]])
        fig.update_yaxes(range=[extent[2], extent[3]])
    fig.update_layout(
        title=dict(text=title, font=dict(size=14)) if title else None,
        height=560, margin=dict(l=0, r=0, t=34 if title else 6, b=0),
        legend=dict(orientation="h", y=-0.04),
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False, visible=False,
                   scaleanchor="x", scaleratio=1),
        plot_bgcolor="white", dragmode="pan", uirevision="floormap")
    return fig
