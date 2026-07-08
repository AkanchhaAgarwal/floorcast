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

## Inputs (CSV)

| File | Columns |
|---|---|
| Seat forecast | account, lob, country, city, site, building, tower, floor *(optional)*, seats |
| Floor inventory | country, city, site, building, tower, floor, seat_rows, seat_cols |
| Employee roster *(optional)* | employee_id, employee_name, account, lob |

Demand rows pinned to a floor stay there; site-level rows are placed largest-first,
keeping each account on as few floors as possible.

## Outputs

- **Seat plan rollups** — requirement vs capacity vs utilization at city / site / floor level
- **Floor maps** — color = account security zone, label = LOB, hover = seat ID + occupant
- **Named seat assignment** — employee → seat ID register, with overflow flagged
- **Excel report** — rollups, seat blocks, and the assignment register
- **DXF export** — seat-level CAD draft (floor boundary, seats, IDs, account labels on layers)

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
