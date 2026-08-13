"""
Floorcast — Onboarding

The gap between "the tool works" and "a customer can use it" is mostly this
module. A new tenant arrives with an empty account, their own spreadsheets, and
no idea which of the six inputs matters first.

Two principles shape it:

**Say what is wrong, where, and how to fix it.** "Invalid data" costs a support
call. "Row 14: available (120) plus allocated (250) is more than total_seats
(300) — check the floor's numbers" does not.

**Never block on the optional.** Two files produce a working plan. The other
four add capability. A setup screen that demands all six before showing anything
loses people who would have stayed for the first answer.
"""

import numpy as np
import pandas as pd

# what each input unlocks, in the order a customer should supply it
INPUTS = [
    {"key": "floors", "name": "Floor inventory", "required": True,
     "unlocks": "The estate view — what you hold and how much is usable",
     "columns": ["site", "building", "floor", "total_seats", "allocated",
                 "available", "trapped"],
     "optional_columns": ["geo", "country", "city", "trapped_reason",
                          "expansion_space", "expansion_eta_weeks", "programs", "notes"]},
    {"key": "demand", "name": "Planning workbook", "required": True,
     "unlocks": "Demand, ramp matching and every what-if",
     "columns": ["Account", "Site", "Metric"], "optional_columns": ["LOB", "Country", "City"]},
    {"key": "allocations", "name": "Allocations by floor", "required": False,
     "unlocks": "Consolidation and relocation options, and the client view",
     "columns": ["site", "building", "floor", "account", "seats"],
     "optional_columns": ["lob"]},
    {"key": "restrictions", "name": "Restrictions", "required": False,
     "unlocks": "Options that respect frozen accounts and dedicated floors",
     "columns": ["rule", "subject"], "optional_columns": ["object", "note"]},
    {"key": "deals", "name": "Deals not yet won", "required": False,
     "unlocks": "Planning for business that has not been signed",
     "columns": ["account", "site", "probability", "month", "hc"],
     "optional_columns": ["opportunity", "stage", "country", "city"]},
    {"key": "plans", "name": "Floor plan (PDF)", "required": False,
     "unlocks": "The coloured floor map",
     "columns": [], "optional_columns": []},
]


def checklist(readiness: dict) -> pd.DataFrame:
    """One row per input: is it loaded, is it required, what does it unlock."""
    rows = []
    for spec in INPUTS:
        n = int(readiness.get(spec["key"], 0) or 0)
        rows.append({"Input": spec["name"],
                     "Loaded": "Yes" if n else "No",
                     "Rows": n,
                     "Needed": "Required" if spec["required"] else "Optional",
                     "Unlocks": spec["unlocks"]})
    return pd.DataFrame(rows)


def progress(readiness: dict) -> dict:
    req = [s for s in INPUTS if s["required"]]
    opt = [s for s in INPUTS if not s["required"]]
    have_req = sum(1 for s in req if readiness.get(s["key"]))
    have_opt = sum(1 for s in opt if readiness.get(s["key"]))
    return {"required_done": have_req, "required_total": len(req),
            "optional_done": have_opt, "optional_total": len(opt),
            "can_plan": have_req == len(req),
            "pct": round((have_req + have_opt) / len(INPUTS) * 100)}


def next_step(readiness: dict) -> str:
    for spec in INPUTS:
        if spec["required"] and not readiness.get(spec["key"]):
            return f"Start with the **{spec['name']}** — {spec['unlocks'].lower()}."
    for spec in INPUTS:
        if not readiness.get(spec["key"]):
            return f"Optional next: **{spec['name']}** — {spec['unlocks'].lower()}."
    return "Everything is loaded. Try a scenario on the What if tab."


# ─────────────────────────────────────────── validation
def _missing(df: pd.DataFrame, cols) -> list:
    return [c for c in cols if c not in df.columns]


def _near(name: str, candidates) -> str:
    """Suggest the column they probably meant. A missing column is nearly always
    a typo, a space, or a capital — naming the likely culprit turns a support
    call into a five-second fix."""
    import difflib
    n = str(name).strip().lower().replace(" ", "_")
    for c in candidates:
        if c.lower() == n or c.lower().replace("_", "") == n.replace("_", ""):
            return c
    hit = difflib.get_close_matches(n, [c.lower() for c in candidates], n=1, cutoff=0.7)
    if hit:
        return next(c for c in candidates if c.lower() == hit[0])
    return ""


def validate(kind: str, df: pd.DataFrame) -> dict:
    """Returns {'ok': bool, 'errors': [...], 'warnings': [...], 'summary': str}.

    Errors stop the load. Warnings do not — a file that is imperfect but usable
    should still get the customer to their first answer.
    """
    spec = next((s for s in INPUTS if s["key"] == kind), None)
    if spec is None:
        return {"ok": False, "errors": [f"Unknown input type '{kind}'."],
                "warnings": [], "summary": ""}
    errors, warnings = [], []

    if df is None or df.empty:
        return {"ok": False, "errors": ["That file has no rows in it."],
                "warnings": [], "summary": ""}

    miss = _missing(df, spec["columns"])
    spare = [c for c in df.columns
             if c not in spec["columns"] + spec["optional_columns"]]
    for m in miss:
        guess = ""
        for existing in spare:
            if _near(existing, [m]):
                guess = f" — the column named '{existing}' looks like it"
                break
        errors.append(f"Missing required column '{m}'{guess}")
    if errors:
        errors.append("Columns found: " + ", ".join(map(str, df.columns[:12])))
        return {"ok": False, "errors": errors, "warnings": [], "summary": ""}

    unused = [c for c in df.columns
              if c not in spec["columns"] + spec["optional_columns"]]
    if unused:
        warnings.append("Ignored column(s): " + ", ".join(map(str, unused[:8])))

    if kind == "floors":
        errors += _check_floors(df)
        if "trapped_reason" not in df.columns:
            warnings.append("No trapped_reason column — unusable seats will all be "
                            "grouped as 'other', and the recovery scenario will not "
                            "be meaningful.")
    elif kind == "allocations":
        errors += _numeric(df, ["seats"])
    elif kind == "deals":
        errors += _numeric(df, ["probability", "hc"])
        if "probability" in df.columns:
            bad = pd.to_numeric(df["probability"], errors="coerce")
            if (bad > 1).any():
                warnings.append("Some probabilities are above 1 — they look like "
                                "percentages. Divide by 100 or they will be treated "
                                "as certainties.")
    elif kind == "restrictions":
        allowed = {"frozen", "dedicated", "no_colocate", "requires", "max_moves"}
        bad = sorted(set(df["rule"].astype(str)) - allowed)
        if bad:
            errors.append("Unknown rule(s): " + ", ".join(bad)
                          + ". Allowed: " + ", ".join(sorted(allowed)))

    summary = f"{len(df):,} row(s)"
    if "site" in df.columns:
        summary += f" across {df['site'].nunique()} site(s)"
    elif "Site" in df.columns:
        summary += f" across {df['Site'].nunique()} site(s)"
    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": summary}


def _numeric(df, cols) -> list:
    out = []
    for c in cols:
        if c not in df.columns:
            continue
        bad = df[pd.to_numeric(df[c], errors="coerce").isna() & df[c].notna()]
        if not bad.empty:
            r = int(bad.index[0]) + 2          # +2: header row and zero index
            out.append(f"Column '{c}' has a value that is not a number "
                       f"(row {r}: '{bad.iloc[0][c]}')")
    return out


def _check_floors(df) -> list:
    out = _numeric(df, ["total_seats", "allocated", "available", "trapped"])
    if out:
        return out
    n = df.copy()
    for c in ("total_seats", "allocated", "available", "trapped"):
        n[c] = pd.to_numeric(n[c], errors="coerce").fillna(0)
    over = n[n["allocated"] + n["available"] > n["total_seats"]]
    for i, r in over.head(3).iterrows():
        out.append(f"Row {int(i) + 2} ({r.get('site', '?')} {r.get('floor', '?')}): "
                   f"allocated ({int(r['allocated'])}) plus available "
                   f"({int(r['available'])}) is more than total_seats "
                   f"({int(r['total_seats'])})")
    if len(over) > 3:
        out.append(f"...and {len(over) - 3} more row(s) with the same problem")
    neg = n[(n[["total_seats", "allocated", "available", "trapped"]] < 0).any(axis=1)]
    if not neg.empty:
        out.append(f"{len(neg)} row(s) contain a negative seat count")
    dup = df.duplicated(subset=[c for c in ("site", "building", "floor")
                                if c in df.columns], keep=False)
    if dup.any():
        d = df[dup].head(1)
        out.append(f"The same floor appears more than once "
                   f"(e.g. {d.iloc[0].get('site', '?')} {d.iloc[0].get('floor', '?')})")
    return out


def sample_for(kind: str) -> str:
    """The bundled file a customer can start from."""
    return {"floors": "data/sample_estate_floors.csv",
            "demand": "data/sample_estate_demand.xlsx",
            "allocations": "data/sample_allocations.csv",
            "restrictions": "data/sample_restrictions.csv",
            "deals": "data/sample_pipeline.csv",
            "plans": "data/sample_floor_plan.pdf"}.get(kind, "")
