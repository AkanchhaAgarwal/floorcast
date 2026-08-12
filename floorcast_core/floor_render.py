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
