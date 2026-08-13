"""
Floorcast — Demand Reader

Parses the planning input workbook: Account x LOB x Country x City x Site,
with a Metric row per combination and one column per week.

    Account | LOB | Country | City | Site | Metric         | w1 | w2 | ...
    Amazon  | ... | India   | ...  | ...  | HC Forecast    |102 |105 |
    Amazon  | ... | India   | ...  | ...  | Seat Ratio     |1.2 |1.2 |
    Amazon  | ... | India   | ...  | ...  | Seats Required | 85 |87.5|

The sheet carries two header rows (month band, then week-ending date), so the
real header is found rather than assumed.

Seats Required is taken from the file when present, and recomputed as
HC Forecast / Seat Ratio when it is missing — seat sharing is already netted
here, so the allocator never divides again.
"""

import math
import numpy as np
import pandas as pd

KEYS = ["Account", "LOB", "Country", "City", "Site", "Program", "Building", "Floor"]

# the planner's ONE SOURCE file names these differently from the simple template,
# so metrics are matched on meaning rather than an exact string
METRIC_ALIASES = {
    "hc": ["hc forecast", "total tms", "headcount", "hc"],
    "onsite_tms": ["onsite tms", "onsite tm"],
    "remote_tms": ["remote tms", "remote tm"],
    "combined_tms": ["combined tms"],
    "seat_ratio": ["seat ratio", "ssr onsite", "ssr", "seat sharing ratio"],
    "ssr_combined": ["ssr combined"],
    "shrinkage": ["shrinkage%", "shrinkage %", "shrinkage"],
    "seats_required": ["seats required", "seat required"],
    "assigned_seats": ["assigned agent seats", "assigned seats"],
    "surplus_deficit": ["seat surplus/deficit", "seat surplus / deficit", "surplus/deficit"],
    "staff_on_agent_seats": ["staff on agents seats", "staff on agent seats"],
}


def _canon(metric: str):
    m = str(metric).strip().lower()
    for canon, names in METRIC_ALIASES.items():
        if m in names:
            return canon
    return None
METRIC = "Metric"
HC, RATIO, SEATS = "HC Forecast", "Seat Ratio", "Seats Required"


def _find_header(raw: pd.DataFrame):
    """Return the index of the row holding 'Account' ... 'Metric'."""
    for i in range(min(10, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[i].tolist()]
        if "Account" in vals and "Metric" in vals:
            return i
    raise ValueError("Could not find a header row containing 'Account' and 'Metric'.")


def read_demand(path_or_buf, sheet=0) -> pd.DataFrame:
    """Returns a long frame: Account, LOB, Country, City, Site, week, hc,
    seat_ratio, seats_required."""
    raw = pd.read_excel(path_or_buf, sheet_name=sheet, header=None)
    h = _find_header(raw)
    hdr = [str(v).strip() for v in raw.iloc[h].tolist()]
    body = raw.iloc[h + 1:].reset_index(drop=True)
    body.columns = hdr

    key_cols = [c for c in KEYS if c in body.columns]
    if METRIC not in body.columns:
        raise ValueError("No 'Metric' column found.")
    week_cols = [c for c in body.columns
                 if c not in key_cols + [METRIC] and str(c).lower() != "nan"]

    # week labels come from the header row itself (week-ending dates)
    weeks = {}
    for c in week_cols:
        lab = c
        try:
            lab = pd.to_datetime(c).date().isoformat()
        except Exception:
            lab = str(c)
        weeks[c] = lab

    body = body.dropna(subset=key_cols, how="all")
    body[METRIC] = body[METRIC].astype(str).str.strip()

    long = body.melt(id_vars=key_cols + [METRIC], value_vars=week_cols,
                     var_name="_wcol", value_name="value")
    long["week"] = long["_wcol"].map(weeks)
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.drop(columns=["_wcol"])

    long["_metric"] = long[METRIC].map(_canon)
    unknown = sorted(set(long.loc[long["_metric"].isna(), METRIC].dropna()))
    long = long.dropna(subset=["_metric"])
    if long.empty:
        raise ValueError("No recognised metric rows. Expected HC Forecast / Total TMs, "
                         "Seat Ratio / SSR, or Seats Required.")

    wide = (long.pivot_table(index=key_cols + ["week"], columns="_metric",
                             values="value", aggfunc="first")
            .reset_index())
    wide.columns.name = None
    for col in ("hc", "seat_ratio", "seats_required", "shrinkage",
                "assigned_seats", "onsite_tms"):
        if col not in wide.columns:
            wide[col] = np.nan

    # trust the file; fall back to headcount / ratio, then apply shrinkage
    base = wide["hc"].fillna(wide["onsite_tms"])
    calc = base / wide["seat_ratio"].replace(0, np.nan)
    shr = wide["shrinkage"].fillna(0)
    shr = np.where(shr > 1, shr / 100.0, shr)          # accepts 9 or 0.09
    calc = calc * (1 - shr)
    wide["seats_required"] = wide["seats_required"].fillna(calc)
    wide.attrs["unrecognised_metrics"] = unknown
    wide = wide.dropna(subset=["seats_required"])
    wide["seats_required"] = wide["seats_required"].astype(float)
    wide["seats"] = np.ceil(wide["seats_required"]).astype(int)   # you cannot buy half a seat

    keep = key_cols + ["week", "hc", "seat_ratio", "shrinkage", "seats_required",
                       "seats", "assigned_seats"]
    out = wide[[c for c in keep if c in wide.columns]]
    out = out.sort_values(key_cols + ["week"]).reset_index(drop=True)
    out.attrs["unrecognised_metrics"] = wide.attrs.get("unrecognised_metrics", [])
    return out


def week_options(demand: pd.DataFrame):
    return sorted(demand["week"].unique())


def peak_week(demand: pd.DataFrame, site=None) -> str:
    d = demand if site is None else demand[demand["Site"] == site]
    if d.empty:
        return None
    return d.groupby("week")["seats"].sum().idxmax()


def slice_week(demand: pd.DataFrame, week: str, site=None) -> pd.DataFrame:
    """Demand for one week, shaped for the allocator."""
    d = demand[demand["week"] == week]
    if site is not None:
        d = d[d["Site"] == site]
    out = d.rename(columns={"Account": "account", "LOB": "lob", "Country": "country",
                            "City": "city", "Site": "site"})
    return out[["account", "lob", "country", "city", "site", "seats",
                "hc", "seat_ratio"]].reset_index(drop=True)


def site_options(demand: pd.DataFrame):
    cols = [c for c in ("Country", "City", "Site") if c in demand.columns]
    s = demand[cols].drop_duplicates()
    labels = [" / ".join(str(v) for v in row) for row in s.itertuples(index=False)]
    return labels, s["Site"].tolist() if "Site" in s.columns else labels


def need_by_site(demand: pd.DataFrame, week: str) -> dict:
    """Seats needed per site for one period — the input to ramp matching."""
    d = demand[demand["week"] == week]
    if "Site" not in d.columns:
        return {}
    return d.groupby("Site")["seats"].sum().to_dict()


# ────────────────────────────────────────────── sales pipeline (Phase 7)
PIPELINE_COLS = ["account", "site", "probability", "month", "hc"]


def read_pipeline(path_or_buf) -> pd.DataFrame:
    """Deals sales is chasing but has not signed — demand that may or may not arrive."""
    p = pd.read_csv(path_or_buf) if not hasattr(path_or_buf, "read") else pd.read_csv(path_or_buf)
    p.columns = [str(c).strip().lower() for c in p.columns]
    missing = [c for c in PIPELINE_COLS if c not in p.columns]
    if missing:
        raise ValueError(f"Pipeline file missing column(s): {', '.join(missing)}")
    p["probability"] = pd.to_numeric(p["probability"], errors="coerce").fillna(0)
    p["hc"] = pd.to_numeric(p["hc"], errors="coerce").fillna(0)
    return p


def pipeline_seats(pipe: pd.DataFrame, mode="weighted", seat_ratio=1.2) -> pd.DataFrame:
    """mode: 'exclude' (signed business only) | 'weighted' (hc x how likely) |
    'full' (as if every deal is won).

    Weighted is what you plan against; full is the stress test — the estate needs
    an answer to 'what if we win everything'."""
    p = pipe.copy()
    if mode == "exclude":
        p["hc_scenario"] = 0.0
    elif mode == "full":
        p["hc_scenario"] = p["hc"]
    else:
        p["hc_scenario"] = p["hc"] * p["probability"]
    p["seats"] = np.ceil(p["hc_scenario"] / float(seat_ratio)).astype(int)
    return p
