"""
Floorcast — Restrictions

Rules that constrain where an account may sit and whether it may be moved.

Where rules live
----------------
Durable rules belong in a **file**, not in a form. A restriction is usually
contractual — a client's floor is dedicated, two clients may not share a floor,
a team cannot move until its audit closes — and a plan justified by rules nobody
can see is a plan nobody can check. So the app asks only for what the files leave
open, then hands back a restrictions file so the same question is never asked
twice.

Rule types
----------
    frozen        account (optionally on one floor) must not be moved
    dedicated     floor belongs to one account; no other account may be placed
    no_colocate   two accounts must not share a floor
    requires      account needs a floor attribute (secure zone, an IT build)
    max_moves     ceiling on seats moved in a single plan

A rule with a reason is worth ten without one, so `note` is carried through to
every report.
"""

import pandas as pd

RULE_COLS = ["rule", "subject", "object", "note"]
RULE_TYPES = {
    "frozen": "Account must not be moved",
    "dedicated": "Floor is dedicated to one account",
    "no_colocate": "Two accounts must not share a floor",
    "requires": "Account needs a floor attribute",
    "max_moves": "Ceiling on seats moved in one plan",
}


def empty() -> pd.DataFrame:
    return pd.DataFrame(columns=RULE_COLS)


def validate(rules: pd.DataFrame):
    problems = []
    if rules is None or rules.empty:
        return problems
    missing = [c for c in RULE_COLS if c not in rules.columns]
    if missing:
        return [f"Missing column(s): {', '.join(missing)}"]
    bad = sorted(set(rules["rule"].astype(str)) - set(RULE_TYPES))
    if bad:
        problems.append("Unknown rule type(s): " + ", ".join(bad)
                        + ". Expected one of: " + ", ".join(RULE_TYPES))
    for _, r in rules.iterrows():
        if r["rule"] in ("no_colocate", "requires", "dedicated") and not str(r.get("object", "")).strip():
            problems.append(f"Rule '{r['rule']}' for {r['subject']} needs an object")
    return problems


class Rules:
    """Reads a rule table once and answers the questions the planner asks of it."""

    def __init__(self, rules: pd.DataFrame = None):
        self.df = empty() if rules is None or rules.empty else rules.copy()
        for c in RULE_COLS:
            if c not in self.df.columns:
                self.df[c] = ""
        self.df = self.df.fillna("")
        self._frozen = self._pairs("frozen")
        self._dedicated = {str(r["subject"]): str(r["object"])
                           for _, r in self.df[self.df["rule"] == "dedicated"].iterrows()}
        self._requires = {}
        for _, r in self.df[self.df["rule"] == "requires"].iterrows():
            self._requires.setdefault(str(r["subject"]), set()).add(str(r["object"]))
        self._nocolo = set()
        for _, r in self.df[self.df["rule"] == "no_colocate"].iterrows():
            self._nocolo.add(frozenset((str(r["subject"]), str(r["object"]))))
        mm = self.df[self.df["rule"] == "max_moves"]
        self.max_moves = int(float(mm.iloc[0]["object"])) if len(mm) and str(mm.iloc[0]["object"]).strip() else None

    def _pairs(self, kind):
        out = set()
        for _, r in self.df[self.df["rule"] == kind].iterrows():
            out.add((str(r["subject"]), str(r["object"]).strip()))
        return out

    # ── questions the mover asks
    def is_frozen(self, account, floor_id=""):
        for acc, scope in self._frozen:
            if acc != account:
                continue
            if not scope or scope == floor_id or scope == str(floor_id).split("|")[-1]:
                return True
        return False

    def frozen_reason(self, account):
        m = self.df[(self.df["rule"] == "frozen") & (self.df["subject"] == account)]
        return str(m.iloc[0]["note"]) if len(m) else ""

    def dedicated_to(self, floor_id):
        short = str(floor_id).split("|")[-1]
        return self._dedicated.get(str(floor_id)) or self._dedicated.get(short)

    def may_place(self, account, floor_id, occupants=(), floor_attrs=()):
        """Can this account be placed on this floor? Returns (ok, reason)."""
        ded = self.dedicated_to(floor_id)
        if ded and ded != account:
            return False, f"floor is dedicated to {ded}"
        need = self._requires.get(account, set())
        have = {str(a).strip().lower() for a in floor_attrs if str(a).strip()}
        missing = {n for n in need if n.strip().lower() not in have}
        if missing:
            return False, "floor lacks " + ", ".join(sorted(missing))
        for other in occupants:
            if other and other != account and frozenset((account, other)) in self._nocolo:
                return False, f"cannot share a floor with {other}"
        return True, ""

    def summary(self) -> pd.DataFrame:
        if self.df.empty:
            return empty()
        d = self.df.copy()
        d["meaning"] = d["rule"].map(RULE_TYPES)
        return d[["rule", "meaning", "subject", "object", "note"]]


# ────────────────────────────── what the files leave open
def open_questions(alloc: pd.DataFrame, floors: pd.DataFrame, rules: Rules):
    """Only ask what the data cannot answer. Each question names the decision it
    affects, so nobody is answering a form for its own sake."""
    qs = []
    if alloc is None or alloc.empty:
        return qs
    accounts = sorted(alloc["account"].dropna().unique())

    covered = {a for a, _ in rules._frozen}
    for a in accounts:
        if a not in covered:
            qs.append({"key": f"frozen::{a}", "rule": "frozen", "subject": a, "object": "",
                       "question": f"Can {a} be moved between floors?",
                       "affects": "Whether relocation may propose moving this account",
                       "options": ["Movable", "Frozen"], "default": "Movable"})

    multi = (alloc.groupby("floor_id")["account"].nunique())
    for fid, n in multi.items():
        short = fid.split("|")[-1]
        if rules.dedicated_to(fid):
            continue
        if n == 1:
            only = alloc.loc[alloc["floor_id"] == fid, "account"].iloc[0]
            qs.append({"key": f"dedicated::{fid}", "rule": "dedicated", "subject": short,
                       "object": only,
                       "question": f"{fid.split('|')[0]} {short} holds only {only}. "
                                   "Is that floor contractually dedicated to them?",
                       "affects": "Whether another account may be placed there",
                       "options": ["Shared", "Dedicated"], "default": "Shared"})

    if len(accounts) > 1 and not rules._nocolo:
        qs.append({"key": "nocolo::any", "rule": "no_colocate", "subject": "", "object": "",
                   "question": "Are there any two clients that must not share a floor?",
                   "affects": "Which floors a relocation may send people to",
                   "options": ["No", "Yes — I will list them"], "default": "No"})

    if rules.max_moves is None:
        qs.append({"key": "max_moves::plan", "rule": "max_moves", "subject": "plan", "object": "",
                   "question": "Is there a ceiling on how many seats may move in one plan?",
                   "affects": "Options above the ceiling are hidden rather than ranked",
                   "options": ["No ceiling", "Set a ceiling"], "default": "No ceiling"})
    return qs


def answers_to_rules(answers: dict, questions: list) -> pd.DataFrame:
    """Turn the answered questions into rule rows — the file the user downloads
    so the same questions are not asked again."""
    rows = []
    by_key = {q["key"]: q for q in questions}
    for key, val in answers.items():
        q = by_key.get(key)
        if not q or val in (None, "", q["default"]):
            continue
        if q["rule"] == "frozen" and val == "Frozen":
            rows.append({"rule": "frozen", "subject": q["subject"], "object": "",
                         "note": "Answered in app"})
        elif q["rule"] == "dedicated" and val == "Dedicated":
            rows.append({"rule": "dedicated", "subject": q["subject"], "object": q["object"],
                         "note": "Answered in app"})
        elif q["rule"] == "max_moves" and isinstance(val, (int, float)) and val > 0:
            rows.append({"rule": "max_moves", "subject": "plan", "object": int(val),
                         "note": "Answered in app"})
    return pd.DataFrame(rows, columns=RULE_COLS) if rows else empty()
