-- Floorcast — schema
--
-- Every table that holds customer data carries tenant_id, and row-level
-- security filters on it in the database rather than in application code.
-- This matters more here than in most products: the customers are competing
-- outsourcers, and the data is client floor plans and headcount. If isolation
-- depends on every query remembering a WHERE clause, one forgotten clause is a
-- contract-ending event. The database should refuse instead.
--
-- The app connects as floorcast_app, which is NOT the table owner and does NOT
-- have BYPASSRLS. It sets app.tenant_id per connection; policies read it.

-- ─────────────────────────────────────────── roles
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'floorcast_app') THEN
    CREATE ROLE floorcast_app LOGIN;
  END IF;
END$$;

-- ─────────────────────────────────────────── tenants and users
CREATE TABLE IF NOT EXISTS tenants (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL,
    slug          text NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    is_active     boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS app_users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email         text NOT NULL,
    display_name  text,
    role          text NOT NULL DEFAULT 'Operations',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz,
    UNIQUE (tenant_id, email)
);
COMMENT ON COLUMN app_users.role IS
  'Leadership | Operations | Facility | Security | WFM | IT | Client | PMO';

-- ─────────────────────────────────────────── the estate
CREATE TABLE IF NOT EXISTS floors (
    id                  bigserial PRIMARY KEY,
    tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    geo                 text,
    country             text,
    city                text,
    site                text NOT NULL,
    building            text NOT NULL,
    floor               text NOT NULL,
    total_seats         integer NOT NULL CHECK (total_seats >= 0),
    allocated           integer NOT NULL DEFAULT 0 CHECK (allocated >= 0),
    available           integer NOT NULL DEFAULT 0 CHECK (available >= 0),
    trapped             integer NOT NULL DEFAULT 0 CHECK (trapped >= 0),
    trapped_reason      text,
    expansion_space     integer NOT NULL DEFAULT 0 CHECK (expansion_space >= 0),
    expansion_eta_weeks numeric,
    programs            text,
    notes               text,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, site, building, floor),
    -- a floor cannot hold more people than it has seats
    CONSTRAINT floors_capacity_sane CHECK (allocated + available <= total_seats)
);

CREATE TABLE IF NOT EXISTS demand (
    id             bigserial PRIMARY KEY,
    tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    account        text NOT NULL,
    lob            text,
    country        text,
    city           text,
    site           text NOT NULL,
    period         text NOT NULL,
    kind           text NOT NULL DEFAULT 'Forecast',   -- Actual | Forecast
    hc             numeric,
    seat_ratio     numeric,
    shrinkage      numeric,
    seats_required numeric,
    seats          integer,
    owner          text,
    owner_email    text,
    status         text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account, lob, site, period)
);

CREATE TABLE IF NOT EXISTS allocations (
    id         bigserial PRIMARY KEY,
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site       text NOT NULL,
    building   text NOT NULL,
    floor      text NOT NULL,
    account    text NOT NULL,
    lob        text,
    seats      integer NOT NULL CHECK (seats >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, site, building, floor, account, lob)
);

CREATE TABLE IF NOT EXISTS restrictions (
    id         bigserial PRIMARY KEY,
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rule       text NOT NULL,
    subject    text NOT NULL,
    object     text,
    note       text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT restrictions_known_rule CHECK (
        rule IN ('frozen', 'dedicated', 'no_colocate', 'requires', 'max_moves'))
);

CREATE TABLE IF NOT EXISTS deals (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    account     text NOT NULL,
    opportunity text,
    country     text,
    city        text,
    site        text NOT NULL,
    stage       text,
    probability numeric CHECK (probability >= 0 AND probability <= 1),
    month       text,
    hc          numeric,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────── surveyed floors
CREATE TABLE IF NOT EXISTS floor_plans (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    site           text NOT NULL,
    building       text NOT NULL,
    floor          text NOT NULL,
    storage_key    text NOT NULL,          -- object storage, tenant-scoped
    original_name  text,
    scale_mm_per_pt numeric,
    seat_count     integer,
    uploaded_by    text,
    uploaded_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, site, building, floor)
);

CREATE TABLE IF NOT EXISTS plan_seats (
    id            bigserial PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    floor_plan_id uuid NOT NULL REFERENCES floor_plans(id) ON DELETE CASCADE,
    seat_id       text NOT NULL,
    zone          text,
    zone_type     text NOT NULL DEFAULT 'Production',
    x_mm          numeric,
    y_mm          numeric,
    desk_size_mm  text,
    UNIQUE (tenant_id, floor_plan_id, seat_id)
);

-- ─────────────────────────────────────────── saved work
CREATE TABLE IF NOT EXISTS scenarios (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name       text NOT NULL,
    levers     jsonb NOT NULL,
    result     jsonb,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- Who changed what. Append-only by policy: the app may insert but not update
-- or delete, so a tenant cannot quietly rewrite its own history.
CREATE TABLE IF NOT EXISTS audit_log (
    id        bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor     text,
    action    text NOT NULL,
    entity    text,
    detail    jsonb,
    at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS floors_tenant_site_idx      ON floors (tenant_id, site);
CREATE INDEX IF NOT EXISTS demand_tenant_period_idx    ON demand (tenant_id, period);
CREATE INDEX IF NOT EXISTS demand_tenant_site_idx      ON demand (tenant_id, site);
CREATE INDEX IF NOT EXISTS alloc_tenant_site_idx       ON allocations (tenant_id, site);
CREATE INDEX IF NOT EXISTS planseats_tenant_plan_idx   ON plan_seats (tenant_id, floor_plan_id);
CREATE INDEX IF NOT EXISTS audit_tenant_at_idx         ON audit_log (tenant_id, at DESC);

-- ─────────────────────────────────────────── row-level security
-- current_setting(..., true) returns NULL rather than erroring when unset, so
-- a connection that forgets to set the tenant sees nothing at all. Failing
-- closed is the whole point.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['app_users','floors','demand','allocations','restrictions',
                           'deals','floor_plans','plan_seats','scenarios','audit_log']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    $f$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO floorcast_app', t);
  END LOOP;

  -- audit trail is append-only for the application role
  EXECUTE 'REVOKE UPDATE, DELETE ON audit_log FROM floorcast_app';
END$$;

-- tenants table is readable but never writable by the app role
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self ON tenants;
CREATE POLICY tenant_self ON tenants
  USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
GRANT SELECT ON tenants TO floorcast_app;

GRANT USAGE ON SCHEMA public TO floorcast_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO floorcast_app;
