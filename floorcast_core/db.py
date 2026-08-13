"""
Floorcast — Data Layer

Reads and writes the estate, scoped to one tenant.

The tenant is not a filter this module remembers to apply. It is set on the
connection with `SET LOCAL app.tenant_id`, and Postgres row-level security does
the filtering. A query that forgets the tenant returns nothing rather than
everything — which is the behaviour you want when the customers are competing
outsourcers and the data is their clients' floor plans.

Every call goes through `session()`, so there is no path to the database that
skips setting the tenant.

Without DATABASE_URL the module falls back to reading the bundled CSVs, so the
app still runs for a demo or offline. The fallback is read-only by design: a
half-persisted single-tenant mode would be worse than none.
"""

import json
import os
from contextlib import contextmanager

import pandas as pd

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def configured() -> bool:
    return bool(DATABASE_URL)


try:
    import psycopg
    from psycopg.rows import dict_row
    _DRIVER = True
except ImportError:                       # the app still runs on CSVs
    _DRIVER = False


class NotConfigured(RuntimeError):
    """Raised when a write is attempted with no database behind the app."""


@contextmanager
def session(tenant_id: str):
    """A connection pinned to one tenant for the life of the transaction.

    set_config(..., true) is the parameterised form of SET LOCAL: transaction
    scoped, so the setting cannot leak into the next user of a pooled
    connection, and the tenant id travels as a bound parameter rather than
    being interpolated into SQL.
    """
    if not (configured() and _DRIVER):
        raise NotConfigured("DATABASE_URL is not set, or the psycopg driver is missing.")
    if not tenant_id:
        raise ValueError("A tenant id is required — there is no global view.")
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)",
                         (str(tenant_id),))
            yield conn
    finally:
        conn.close()


def _df(conn, sql, params=None) -> pd.DataFrame:
    rows = conn.execute(sql, params or ()).fetchall()
    return pd.DataFrame(rows)


# ─────────────────────────────────────────── reads
FLOOR_COLS = ("geo, country, city, site, building, floor, total_seats, allocated, "
              "available, trapped, trapped_reason, expansion_space, "
              "expansion_eta_weeks, programs, notes")


def get_floors(tenant_id: str) -> pd.DataFrame:
    if not configured():
        return pd.read_csv("data/sample_estate_floors.csv")
    with session(tenant_id) as c:
        return _df(c, f"SELECT {FLOOR_COLS} FROM floors ORDER BY site, building, floor")


def get_demand(tenant_id: str) -> pd.DataFrame:
    if not configured():
        from . import demand_reader as dr
        return dr.read_demand("data/sample_estate_demand.xlsx")
    with session(tenant_id) as c:
        d = _df(c, "SELECT account, lob, country, city, site, period, kind, hc, "
                   "seat_ratio, shrinkage, seats_required, seats "
                   "FROM demand ORDER BY site, account, period")
    if d.empty:
        return d
    return d.rename(columns={"account": "Account", "lob": "LOB", "country": "Country",
                             "city": "City", "site": "Site", "period": "week"})


def get_allocations(tenant_id: str) -> pd.DataFrame:
    if not configured():
        return pd.read_csv("data/sample_allocations.csv")
    with session(tenant_id) as c:
        return _df(c, "SELECT site, building, floor, account, lob, seats FROM allocations")


def get_restrictions(tenant_id: str) -> pd.DataFrame:
    if not configured():
        return pd.read_csv("data/sample_restrictions.csv")
    with session(tenant_id) as c:
        return _df(c, "SELECT rule, subject, object, note FROM restrictions")


def get_deals(tenant_id: str) -> pd.DataFrame:
    if not configured():
        return pd.read_csv("data/sample_pipeline.csv")
    with session(tenant_id) as c:
        return _df(c, "SELECT account, opportunity, country, city, site, stage, "
                      "probability, month, hc FROM deals")


def get_plan_seats(tenant_id: str, site=None) -> pd.DataFrame:
    if not configured():
        return pd.read_csv("data/sample_seat_inventory.csv")
    q = ("SELECT p.site, p.building, p.floor, s.seat_id, s.zone, s.zone_type, "
         "s.x_mm, s.y_mm, s.desk_size_mm, p.storage_key, p.scale_mm_per_pt "
         "FROM plan_seats s JOIN floor_plans p ON p.id = s.floor_plan_id")
    with session(tenant_id) as c:
        return _df(c, q + (" WHERE p.site = %s" if site else ""), (site,) if site else ())


# ─────────────────────────────────────────── writes
def _replace(conn, table: str, tenant_id: str, df: pd.DataFrame, cols):
    """Replace this tenant's rows for a table. The delete is inside the same
    transaction and inside RLS, so it can only ever reach this tenant's data."""
    conn.execute(f"DELETE FROM {table}")
    if df is None or df.empty:
        return 0
    keep = [c for c in cols if c in df.columns]
    payload = df[keep].where(pd.notna(df[keep]), None)
    names = ", ".join(["tenant_id"] + keep)
    marks = ", ".join(["%s"] * (len(keep) + 1))
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO {table} ({names}) VALUES ({marks})",
                        [(tenant_id, *row) for row in payload.itertuples(index=False)])
    return len(payload)


def save_floors(tenant_id, df, actor=None):
    cols = ["geo", "country", "city", "site", "building", "floor", "total_seats",
            "allocated", "available", "trapped", "trapped_reason",
            "expansion_space", "expansion_eta_weeks", "programs", "notes"]
    with session(tenant_id) as c:
        n = _replace(c, "floors", tenant_id, df, cols)
        _audit(c, tenant_id, actor, "replace", "floors", {"rows": n})
    return n


def save_demand(tenant_id, df, actor=None):
    d = df.rename(columns={"Account": "account", "LOB": "lob", "Country": "country",
                           "City": "city", "Site": "site", "week": "period"})
    cols = ["account", "lob", "country", "city", "site", "period", "kind", "hc",
            "seat_ratio", "shrinkage", "seats_required", "seats"]
    with session(tenant_id) as c:
        n = _replace(c, "demand", tenant_id, d, cols)
        _audit(c, tenant_id, actor, "replace", "demand", {"rows": n})
    return n


def save_allocations(tenant_id, df, actor=None):
    cols = ["site", "building", "floor", "account", "lob", "seats"]
    with session(tenant_id) as c:
        n = _replace(c, "allocations", tenant_id, df, cols)
        _audit(c, tenant_id, actor, "replace", "allocations", {"rows": n})
    return n


def save_restrictions(tenant_id, df, actor=None):
    cols = ["rule", "subject", "object", "note"]
    with session(tenant_id) as c:
        n = _replace(c, "restrictions", tenant_id, df, cols)
        _audit(c, tenant_id, actor, "replace", "restrictions", {"rows": n})
    return n


def save_deals(tenant_id, df, actor=None):
    cols = ["account", "opportunity", "country", "city", "site", "stage",
            "probability", "month", "hc"]
    with session(tenant_id) as c:
        n = _replace(c, "deals", tenant_id, df, cols)
        _audit(c, tenant_id, actor, "replace", "deals", {"rows": n})
    return n


def save_floor_plan(tenant_id, site, building, floor, storage_key, seats_df,
                    scale=None, original_name=None, actor=None):
    """Record an uploaded plan and the seats read from it."""
    with session(tenant_id) as c:
        c.execute("DELETE FROM floor_plans WHERE site=%s AND building=%s AND floor=%s",
                  (site, building, floor))
        row = c.execute(
            "INSERT INTO floor_plans (tenant_id, site, building, floor, storage_key, "
            "original_name, scale_mm_per_pt, seat_count, uploaded_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (tenant_id, site, building, floor, storage_key, original_name, scale,
             len(seats_df) if seats_df is not None else 0, actor)).fetchone()
        plan_id = row["id"]
        if seats_df is not None and not seats_df.empty:
            cols = ["seat_id", "zone", "zone_type", "x_mm", "y_mm", "desk_size_mm"]
            keep = [c2 for c2 in cols if c2 in seats_df.columns]
            payload = seats_df[keep].where(pd.notna(seats_df[keep]), None)
            names = ", ".join(["tenant_id", "floor_plan_id"] + keep)
            marks = ", ".join(["%s"] * (len(keep) + 2))
            with c.cursor() as cur:
                cur.executemany(f"INSERT INTO plan_seats ({names}) VALUES ({marks})",
                                [(tenant_id, plan_id, *r)
                                 for r in payload.itertuples(index=False)])
        _audit(c, tenant_id, actor, "upload", "floor_plan",
               {"site": site, "floor": floor, "seats": int(len(seats_df or []))})
    return plan_id


# ─────────────────────────────────────────── scenarios and audit
def save_scenario(tenant_id, name, levers, result=None, actor=None):
    with session(tenant_id) as c:
        c.execute("DELETE FROM scenarios WHERE name = %s", (name,))
        c.execute("INSERT INTO scenarios (tenant_id, name, levers, result, created_by) "
                  "VALUES (%s,%s,%s,%s,%s)",
                  (tenant_id, name, json.dumps(levers),
                   json.dumps(result) if result else None, actor))
        _audit(c, tenant_id, actor, "save", "scenario", {"name": name})


def list_scenarios(tenant_id) -> pd.DataFrame:
    if not configured():
        return pd.DataFrame()
    with session(tenant_id) as c:
        return _df(c, "SELECT name, levers, created_by, created_at "
                      "FROM scenarios ORDER BY created_at DESC")


def _audit(conn, tenant_id, actor, action, entity, detail=None):
    conn.execute("INSERT INTO audit_log (tenant_id, actor, action, entity, detail) "
                 "VALUES (%s,%s,%s,%s,%s)",
                 (tenant_id, actor or "unknown", action, entity,
                  json.dumps(detail) if detail else None))


def recent_activity(tenant_id, limit=50) -> pd.DataFrame:
    if not configured():
        return pd.DataFrame()
    with session(tenant_id) as c:
        return _df(c, "SELECT at, actor, action, entity, detail FROM audit_log "
                      "ORDER BY at DESC LIMIT %s", (limit,))


# ─────────────────────────────────────────── onboarding state
def readiness(tenant_id: str) -> dict:
    """What this tenant has loaded, and what is still missing. Drives the
    setup checklist a new customer sees on first login."""
    if not configured():
        return {"mode": "sample", "floors": 0, "demand": 0, "allocations": 0,
                "restrictions": 0, "deals": 0, "plans": 0}
    with session(tenant_id) as c:
        out = {"mode": "database"}
        for t in ("floors", "demand", "allocations", "restrictions", "deals"):
            out[t] = c.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
        out["plans"] = c.execute("SELECT count(*) AS n FROM floor_plans").fetchone()["n"]
    return out
