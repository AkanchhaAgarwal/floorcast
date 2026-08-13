# Floorcast — Multi-tenant setup

What was built, how to run it, and what is deliberately not done yet.

---

## What these three pieces do

| | |
|---|---|
| `db/001_schema.sql` | Postgres schema, one `tenant_id` on every table, isolation enforced by row-level security |
| `floorcast_core/db.py` | All reads and writes, scoped to one tenant. Falls back to bundled CSVs when no database is configured |
| `floorcast_core/storage.py` | Files in object storage under a tenant-owned key prefix, with signed links |
| `floorcast_core/onboarding.py` | Readiness checklist and validation that names the row and the fix |

---

## Isolation, and how it was checked

Isolation is not a `WHERE` clause the application remembers to add. Each
connection sets `app.tenant_id` inside its transaction, and Postgres policies
filter on it. The application connects as `floorcast_app`, which is not the
table owner and does not have `BYPASSRLS`, so it cannot see around the policy.

Tested against a live Postgres with two tenants seeded:

| Attempt | Result |
|---|---|
| Query with no tenant set | 0 rows — fails closed |
| Tenant A reads its own data | Only its own |
| Tenant A queries B's site by name | 0 rows |
| Tenant A inserts a row owned by B | Rejected by policy |
| Tenant A edits its own audit trail | Permission denied |
| Tenant A deletes its audit trail | Permission denied |
| Tenant B fetches A's file key | Rejected |
| Filename `../../etc/passwd.pdf` | Reduced to a safe name inside A's prefix |
| Key forged with B's prefix | Rejected |
| Upload of `.exe` | Rejected |

The audit log is append-only for the application role, so a tenant cannot
quietly rewrite its own history.

**This is not a substitute for a security review.** These tests were written
against the same assumptions as the code, which is exactly the blind spot a
reviewer is for. Before a second paying customer, have someone else try to break
it.

---

## Running it

**1 · Database.** Create a Postgres (Supabase and Neon both have a free tier
that covers a pilot), then:

```bash
psql "$DATABASE_URL_ADMIN" -f db/001_schema.sql
```

Create the application login and give it a password. It must not own the tables
and must not have `BYPASSRLS`:

```sql
ALTER ROLE floorcast_app WITH PASSWORD '<strong password>';
```

**2 · Create the first tenant** (as the owner, not the app role):

```sql
INSERT INTO tenants (name, slug) VALUES ('Acme BPO', 'acme') RETURNING id;
INSERT INTO app_users (tenant_id, email, role)
VALUES ('<that id>', 'planner@acme.com', 'Operations');
```

**3 · Environment.**

```bash
export DATABASE_URL="postgresql://floorcast_app:<password>@<host>:5432/floorcast"
export S3_BUCKET="floorcast-files"        # omit for local files in development
export SIGNED_URL_TTL=900
export MAX_UPLOAD_BYTES=52428800
```

Without `DATABASE_URL` the app runs on bundled sample data, read-only. That is
the demo mode, and it is safe to leave that way.

**4 · Install and run.**

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Onboarding a customer

A new tenant lands on an empty account. `onboarding.checklist()` shows the six
inputs, which two are required, and what each unlocks. `next_step()` gives one
instruction rather than six.

Validation is written to be actionable. Instead of "invalid data":

- *Missing required column 'floor' — the column named 'flr' looks like it*
- *Row 2 (Acme 1F): allocated (250) plus available (120) is more than total_seats (300)*
- *Column 'total_seats' has a value that is not a number (row 2: 'three hundred')*
- *Some probabilities are above 1 — they look like percentages*

Warnings do not block. A file that is imperfect but usable should still get the
customer to a first answer.

---

## What is deliberately not done

**Client scoping is previewed, not enforced.** The Client role view cuts its data
to one account through `roles.client_safe_view()`, and no other client's rows or
names reach the page. But the account is chosen from a picker, not from who is
signed in. To enforce it, add a column to `app_users`:

```sql
ALTER TABLE app_users ADD COLUMN account_scope text;
COMMENT ON COLUMN app_users.account_scope IS
  'Null for internal staff. Set for a client user, who may then see only that account.';
```

Then the view reads `account_scope` instead of offering a choice, and the query
filters on it. Two rules matter: filter in the **query**, not the page — a hidden
picker over unfiltered data passes a demo and fails a review — and treat a null
scope as internal only after the role has been checked.

**Authentication.** There is no login. `db.py` takes a `tenant_id` from the
caller, and today nothing proves the caller is entitled to it. **Do not put this
in front of two customers until auth is wired in** — isolation is sound at the
database, and absent at the door.

**Connection pooling.** Each call opens a connection. Fine for a pilot, not for
load. `set_config(..., true)` is transaction-scoped precisely so a pooler can be
dropped in later without leaking tenant context.

**Migrations.** One schema file, no version tracking. Add Alembic before the
schema changes under a live customer.

**Backups.** Whatever the hosting provider gives you. Untested restores are not
backups.

---

## Suggested order from here

1. Auth (Supabase Auth or Clerk), mapping the signed-in user to `tenant_id`
2. A developer review of the isolation and the session handling
3. Hosting off Community Cloud, with secrets in the platform rather than the repo
4. One design partner, in production, before building for the second
