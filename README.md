# Floorcast 🏬

**Facility-management seat planning for contact centers — from seat forecast to floor map.**

Booking-first workplace tools (Tango, Robin, Condeco) manage seats one reservation at a
time. Floorcast plans them: give it a seat forecast at **client account × LOB × geography**
level (country → city → site → building → tower → floor) and a floor inventory, and it
returns the complete seat plan — security-zoned floor maps, capacity rollups, named
employee seat assignment, and a CAD (DXF) draft that opens in AutoCAD.

## The security model

- **Client account = security boundary.** Every account gets a dedicated, contiguous zone;
  accounts never interleave on a floor (contractual segregation, enforced by construction).
- **LOB = open within its account.** Lines of business sit contiguously inside their
  account's zone with no partition requirement.

## What it does

Floorcast plans seats across a contact centre **estate**, not one floor at a time.

**Seats have four states, not two** — `allocated`, `available`, `trapped` (physically
present but unusable), and `expansion` (not built yet, unlocked by a renovation with a
lead time). Trapped seats are typically the largest pool of recoverable capacity in an
estate, so they carry a reason code and are never quietly folded into "available".

**Five views**

| Tab | What it answers |
|---|---|
| Estate | How many seats do we have, where, and how many are trapped? |
| Demand | How many seats does each account need, by period? |
| Ramp plan | Does this ramp fit, on which floors, and what is the shortfall? |
| Floor map | Which seat does each account get, drawn on the real plan? |
| Scenarios | What if we release trapped seats, or the pipeline lands? |

## Inputs

**Floor inventory (CSV)** — one row per floor:
`geo, country, city, site, building, floor, total_seats, allocated, available, trapped,
trapped_reason, expansion_space, expansion_eta_weeks, programs, notes`

`trapped_reason` is one of `segregation`, `layout`, `it_not_ready`, `under_renovation`,
`contractual_hold`, `condition`, `other`. The first four are treated as recoverable by
default — the tool never infers *why* a seat is trapped, because that definition differs
by site.

**Planning workbook (Excel)** — Account / LOB / Site with a metric row per combination and
one column per period. Metrics are matched on meaning, so both `HC Forecast` / `Seat Ratio`
and the planner's `Total TMs` / `SSR Onsite` / `Shrinkage%` / `Seats Required` are
understood. Where `Seats Required` is given it is trusted; otherwise it is computed as
headcount ÷ ratio, less shrinkage, rounded up.

**Sales pipeline (CSV, optional)** — `account, site, stage, probability, month, hc`.
Opportunities that have not closed. Weighted by probability for planning, or taken in full
as a stress test.

**Floor plan (PDF, optional)** — a vector CAD plot for any floor that has been surveyed.
Seats are counted from the desk-size annotations; scale is derived from the plotted desk
footprint and cross-checked against the drawing's dimension text.

## Ramp matching

Ramp need is **incremental** by default — seats required for the period, less what the site
already holds — which is how a planner reads it. Gross demand is available for re-planning a
site from empty. Expansion space counts only if its renovation lands inside the horizon you
set.

## Zones and security

The floor's own zones are the security boundaries. An account takes whole zones
largest-first, and a zone is split only when the remainder is smaller than every zone still
free; any zone shared by two accounts is reported as a segregation exception. Support space
— manager cabins, counselling rooms, IT and HR rooms — is held out of the allocatable pool,
because filling the tail of an account's demand with one seat in a counselling room is
arithmetically valid and operationally wrong.

## Outputs

- **Seat plan rollups** — requirement vs capacity vs utilization at city / site / floor level
- **Floor maps** — color = account security zone, label = LOB, hover = seat ID + occupant
- **Named seat assignment** — employee → seat ID register, with overflow flagged
- **Excel report** — rollups, seat blocks, and the assignment register
- **DXF export** — seat-level CAD draft (floor boundary, seats, IDs, account labels on layers). In
  real-floor mode seats are written at their true millimetre coordinates, so the export overlays
  the original floor plan instead of a synthetic grid.
- **Segregation exceptions** — any zone shared by more than one client account, flagged for review

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Sample data is bundled; the app runs instantly and sample CSVs are downloadable in-app.

## Boundaries

All layouts are planning drafts, not construction drawings. Fire and safety exits, egress
travel distances, occupancy loads, and accessibility clearances must be verified and
certified by a licensed architect before implementation. Bundled data is synthetic.

## Author

**Akanchha Agarwal** — WFM & facilities analytics · Creator of
[WFM Simplified](https://youtube.com/@WFMSimplified) · M.Tech, IIT Kanpur · LSSBB
