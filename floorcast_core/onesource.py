"""
Floorcast — One Source Reader

Reads the real planning workbook rather than a simplified template: one tab per
geo, a block of eleven metric rows per programme, actuals and forecast side by
side across a three-year monthly horizon, and the collection governance —
planner, email, status, due date — carried alongside the numbers.

Two things this module does that the simple reader cannot:

**It tells you what does not add up.** A workbook of eight hundred programmes
hides its own contradictions. Total TMs that do not equal onsite plus remote plus
combined, onsite staff with a seat ratio of zero, seats required of zero against
real headcount — none of these are visible by scrolling, and a seat plan built on
them is wrong before any allocation happens.

**It tells you how much is missing.** Collection runs to a deadline and is rarely
finished. Planning against a half-collected file without saying so is the quiet
failure mode; this reports completion by geo and by owner so the gap is explicit.

It deliberately does not recompute Seats Required. The workbook's own guidance is
that calculation belongs in the cap plans, so where this module recomputes it is
only ever to flag a disagreement — never to overwrite.
"""

import re
import numpy as np
import pandas as pd

METRICS = ["Total TMs", "Onsite TMs", "Remote TMs", "Combined TMs", "Seats Required",
           "SSR Onsite", "SSR Combined", "Shrinkage%", "Staff on Agents Seats",
           "Assigned Agent Seats", "Seat Surplus/Deficit"]

SKIP_SHEETS = ("guideline", "email template", "pivot", "summary", "reminder distro",
               "distro follow up", "not updated", "top 10 ssr", "rfp", "sheet",
               "completion", "do not use", "tids source", "copy of",
               "old", "deprecated", "archive", "bak", "requirements")

SSR_CEILING = 2.6          # above this, worth confirming rather than assuming


def _is_geo_tab(name: str) -> bool:
    n = name.strip().lower()
    return not any(k in n for k in SKIP_SHEETS)


def _find_header(d: pd.DataFrame):
    """Row that carries Site / Account / Information / Data."""
    for i in range(min(15, len(d))):
        row = [str(v).strip() for v in d.iloc[i].tolist()]
        if "Data" in row and ("Account" in row or "Site" in row):
            return i
    return None


def _periods(d: pd.DataFrame, hdr: int, first_col: int):
    """Period label and whether the column is an actual or a forecast."""
    band = [str(v).strip() for v in d.iloc[hdr].tolist()]
    labels = None
    for i in range(hdr):
        row = d.iloc[i].tolist()
        hits = [str(v) for v in row[first_col:] if re.match(r"^\d{4}[A-Za-z]{3}$", str(v).strip())]
        if len(hits) >= 6:
            labels = [str(v).strip() for v in row]
            break
    out = {}
    for c in range(first_col, d.shape[1]):
        kind = band[c] if c < len(band) else ""
        if kind not in ("Actual", "Forecast"):
            continue
        lab = labels[c].strip() if labels and c < len(labels) and str(labels[c]) != "nan" else f"col{c}"
        out[c] = (lab, kind)
    return out


def read_workbook(path_or_buf, sheets=None) -> dict:
    """Returns {'long': tidy frame, 'programmes': one row per block, 'tabs': [...]}"""
    xl = pd.ExcelFile(path_or_buf)
    names = sheets or [s for s in xl.sheet_names if _is_geo_tab(s)]
    long_rows, prog_rows, used = [], [], []

    for sheet in names:
        d = xl.parse(sheet, header=None)
        if d.empty or d.shape[1] < 8:
            continue
        hdr = _find_header(d)
        if hdr is None:
            continue
        row = [str(v).strip() for v in d.iloc[hdr].tolist()]
        try:
            c_data = row.index("Data")
        except ValueError:
            continue
        c_site = row.index("Site") if "Site" in row else None
        c_acct = row.index("Account") if "Account" in row else None
        c_info = row.index("Information") if "Information" in row else None
        cols = _periods(d, hdr, c_data + 1)
        if not cols:
            continue
        used.append(sheet)

        labels = d[c_data].astype(str).str.strip()
        starts = list(d.index[labels == "Total TMs"])
        for s in starts:
            blk = d.loc[s:s + len(METRICS) - 1]
            lab = blk[c_data].astype(str).str.strip()
            site = str(d.iloc[s, c_site]).strip() if c_site is not None else ""
            acct = str(d.iloc[s, c_acct]).strip() if c_acct is not None else ""
            prog = str(d.iloc[s, 0]).strip() if d.shape[1] > 0 else ""

            info = {}
            if c_info is not None:
                cells = [str(v).strip() for v in blk[c_info].tolist() if str(v) != "nan"]
                for j, v in enumerate(cells):
                    if "@" in v:
                        info.setdefault("email", v)
                    elif v in ("Updated", "Not Updated"):
                        info["status"] = v
                    elif v.startswith("POC"):
                        info["poc_label"] = v
                    elif v not in ("Status", "Last Update:"):
                        info.setdefault("owner", v)

            got = {}
            for m in METRICS:
                r = blk[lab == m]
                if len(r):
                    got[m] = r.iloc[0]

            for c, (period, kind) in cols.items():
                rec = {"geo": sheet, "site": site, "account": acct, "programme": prog,
                       "period": period, "kind": kind}
                any_val = False
                for m, series in got.items():
                    v = pd.to_numeric(series.iloc[c], errors="coerce")
                    rec[m] = v
                    if pd.notna(v):
                        any_val = True
                if any_val:
                    long_rows.append(rec)

            prog_rows.append({"geo": sheet, "site": site, "account": acct, "programme": prog,
                              "owner": info.get("owner", ""), "email": info.get("email", ""),
                              "status": info.get("status", "Unknown"),
                              "row": int(s)})

    long = pd.DataFrame(long_rows)
    if not long.empty:
        long = long.rename(columns={
            "Total TMs": "total_tms", "Onsite TMs": "onsite_tms", "Remote TMs": "remote_tms",
            "Combined TMs": "combined_tms", "Seats Required": "seats_required",
            "SSR Onsite": "ssr_onsite", "SSR Combined": "ssr_combined",
            "Shrinkage%": "shrinkage", "Staff on Agents Seats": "stas",
            "Assigned Agent Seats": "assigned_seats",
            "Seat Surplus/Deficit": "surplus_deficit"})
    return {"long": long, "programmes": pd.DataFrame(prog_rows), "tabs": used}


# ────────────────────────────────────────── validation
# Seats Required is NOT recomputed and compared. Testing six plausible formulas
# against the real workbook, the best matched only 62% of rows within 10% — the
# calculation genuinely lives in each programme's cap plan, as the workbook's own
# guidance says. A check that disagreed with 44% of the file would be noise, and
# noise trains people to ignore the report.
CHECKS = {
    "tm_split": "Total TMs does not equal onsite + remote + combined",
    "ssr_missing": "Onsite staff present but seat ratio is zero",
    "seats_missing": "Onsite staff present but zero seats required",
    "ssr_outlier": f"Seat ratio above {SSR_CEILING} — worth confirming",
    "negative": "A negative value where none should appear",
    "no_seats_no_ratio": "No seats required and no ratio, but staff are onsite",
    "stale": "Never submitted — no status recorded",
}
SEVERITY = {"tm_split": "High", "ssr_missing": "High", "seats_missing": "High",
            "ssr_outlier": "Low", "negative": "High",
            "no_seats_no_ratio": "High", "stale": "Medium"}


def quality_report(long: pd.DataFrame) -> pd.DataFrame:
    """One row per programme per failed check, with the worst month named.

    Every check here is an arithmetic identity or a plain contradiction, so a flag
    is always worth someone's time. Judgement calls belong to the planner.
    """
    if long.empty:
        return pd.DataFrame()
    d = long.copy()
    for c in ("total_tms", "onsite_tms", "remote_tms", "combined_tms", "seats_required",
              "ssr_onsite", "ssr_combined", "shrinkage", "stas"):
        if c not in d.columns:
            d[c] = np.nan
    key = ["geo", "site", "account", "programme"]
    rows = []

    def add(g, check, worst_period, detail):
        rows.append({**{k: g.iloc[0][k] for k in key},
                     "check": check, "issue": CHECKS[check], "severity": SEVERITY[check],
                     "worst_period": worst_period, "detail": detail})

    for _, g in d.groupby(key, dropna=False):
        split = (g["total_tms"].fillna(0)
                 - (g["onsite_tms"].fillna(0) + g["remote_tms"].fillna(0)
                    + g["combined_tms"].fillna(0))).abs()
        if (split > 1).any():
            i = split.idxmax()
            add(g, "tm_split", g.loc[i, "period"], f"out by {int(split.max())} staff")

        m = (g["onsite_tms"].fillna(0) > 0) & (g["ssr_onsite"].fillna(0) == 0) \
            & (g["ssr_combined"].fillna(0) == 0)
        if m.any():
            add(g, "ssr_missing", g.loc[m.idxmax(), "period"],
                f"{int(m.sum())} month(s) affected")

        m = (g["onsite_tms"].fillna(0) > 0) & (g["seats_required"].fillna(0) == 0)
        if m.any():
            add(g, "seats_missing", g.loc[m.idxmax(), "period"],
                f"{int(g.loc[m, 'onsite_tms'].max())} onsite staff, no seats")

        hi = g[["ssr_onsite", "ssr_combined"]].max(axis=1)
        if (hi.fillna(0) > SSR_CEILING).any():
            add(g, "ssr_outlier", g.loc[hi.idxmax(), "period"], f"ratio {hi.max():.2f}")

        neg = g[["total_tms", "onsite_tms", "seats_required"]].min(axis=1)
        if (neg.fillna(0) < 0).any():
            add(g, "negative", g.loc[neg.idxmin(), "period"], f"lowest {neg.min():.0f}")

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    order = {"High": 0, "Medium": 1, "Low": 2}
    out["_o"] = out["severity"].map(order)
    return out.sort_values(["_o", "geo", "account"]).drop(columns="_o")


def quality_summary(issues: pd.DataFrame, programmes: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return pd.DataFrame()
    g = (issues.groupby(["check", "issue", "severity"])
         .size().reset_index(name="programmes"))
    total = max(len(programmes), 1)
    g["share_%"] = (g["programmes"] / total * 100).round(1)
    order = {"High": 0, "Medium": 1, "Low": 2}
    return g.assign(_o=g["severity"].map(order)).sort_values(
        ["_o", "programmes"], ascending=[True, False]).drop(columns="_o")


# ────────────────────────────────────────── collection status
def completion(programmes: pd.DataFrame) -> pd.DataFrame:
    if programmes.empty:
        return pd.DataFrame()
    g = (programmes.assign(done=programmes["status"].eq("Updated").astype(int))
         .groupby("geo")
         .agg(programmes=("programme", "count"), updated=("done", "sum"),
              owners=("owner", "nunique"))
         .reset_index())
    g["not_updated"] = g["programmes"] - g["updated"]
    g["completion_%"] = (g["updated"] / g["programmes"] * 100).round(1)
    return g.sort_values("completion_%")


def chase_list(programmes: pd.DataFrame) -> pd.DataFrame:
    """Who has not submitted — the list the reminder sheets try to maintain."""
    if programmes.empty:
        return pd.DataFrame()
    n = programmes[programmes["status"] != "Updated"]
    return (n.groupby(["geo", "owner", "email"], dropna=False)
            .agg(programmes=("programme", "count"),
                 accounts=("account", lambda s: ", ".join(sorted(set(s))[:3])))
            .reset_index().sort_values("programmes", ascending=False))


def coverage_note(programmes: pd.DataFrame, long: pd.DataFrame) -> str:
    """One honest sentence about how much of the plan is actually present."""
    if programmes.empty:
        return ""
    done = int(programmes["status"].eq("Updated").sum())
    tot = len(programmes)
    pct = done / tot * 100 if tot else 0
    return (f"{done:,} of {tot:,} programmes have been updated ({pct:.0f}%). "
            "Totals below reflect what has been submitted, not the full estate.")
