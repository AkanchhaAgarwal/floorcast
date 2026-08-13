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

## What if

The files are loaded once. After that the **What if** tab is a live dashboard: move a lever
and the whole picture recomputes, with the change against baseline shown on every number.

Levers compose, which is the point — "release half the trapped seats **and** assume volumes
land 15% above plan **and** count the pipeline" is one question, not three.

| Lever | What it asks |
|---|---|
| Period | Which month or week are we planning against |
| Demand vs plan | What if volumes land above or below forecast |
| Trapped seats | Which reasons do we assume recovered, and how much of the pool |
| Renovation | Does expansion space count, and how soon must it land |
| Pipeline | Ignore unclosed demand, weight it by probability, or take all of it |

Scenarios can be named and saved, then compared side by side against the same baseline, so
the difference between two answers is real rather than an artefact of what was loaded when.

## When it does not fit

Options are ranked by cost, cheapest first:

1. **Release trapped seats** — nobody moves
2. **Bring a renovation forward** — nobody moves
3. **Consolidate a fragmented account** — few moves
4. **Relocate a small account** — moderate moves
5. **Move partitions** — capex plus moves
6. **Another site, or defer the ramp**

Steps 3 and 4 need the optional allocations file (`site, building, floor, account, lob,
seats`). One caveat worth stating plainly: **moving people within a site does not create
seats** — a seat freed on one floor is consumed on another. What a move creates is
*contiguity*: a block big enough for one client to have to itself. Where a site is
genuinely short of seats, the answer is still steps 1, 2 or 6.

The objective is **seats moved, minimised** — never utilisation. A plan that packs the
estate perfectly by moving four hundred people is a worse answer than one that leaves
forty seats idle and moves nobody. Options that would split an account across floors are
flagged rather than silently taken.

## Restrictions

Rules that constrain where an account may sit and whether it may be moved. Supplied as
`restrictions.csv` — `rule, subject, object, note`:

| rule | means |
|---|---|
| `frozen` | This account must not be moved (optionally only on one floor) |
| `dedicated` | This floor belongs to one account; no other may be placed there |
| `no_colocate` | These two accounts must not share a floor |
| `requires` | This account needs a floor attribute — a secure zone, an IT build |
| `max_moves` | Ceiling on seats moved in a single plan |

**Rules live in a file, not a form.** A restriction is usually contractual, and a plan
justified by rules nobody can see is a plan nobody can check. The app therefore asks only
the questions the files leave open — naming, for each one, the decision it affects — and
then hands back a `restrictions.csv` so the same question is never asked twice. Answers
given in the app apply to that session only until they are saved.

Blocked options are reported with their reason rather than hidden: *"Blocked — Nimbus is
frozen (client audit closes in March)"* is an answer a planner needs, and a silent
omission is not.

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

## Sample data

Bundled sample data is synthetic.

## Author

**Akanchha Agarwal** — WFM & facilities analytics · Creator of
[WFM Simplified](https://youtube.com/@WFMSimplified) · M.Tech, IIT Kanpur · LSSBB
