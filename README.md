# Floorcast 🏢

**From forecast to floor plan.**

Commercial seat tools (Tango, Robin, Condeco) treat offices as static real estate. WFM suites
stop at schedules. Floorcast chains the whole thing: give it a 12-month volume forecast and a
floor, and it returns FTE requirements, an optimized shift mix, interval-level seat demand,
and a movable-partition floor layout — month by month.

📄 Concept papers: [Floorcast](docs/Floorcast_Concept_Paper.pdf) · [SeatIQ (v1)](docs/SeatIQ_White_Paper.pdf)

## Two modes

**🪑 Seat Planner (roster mode)** — upload a roster, get interval seat demand, true per-program
seat-sharing ratios, and MIP-optimized bay allocation with what-if levers (WFH %, hiring batches).

**📈 Forecast → Floor (planning mode)** — the five-stage pipeline:

| Stage | Question | Technique |
|---|---|---|
| 1. Forecast intake | Contacts per interval? | Intraday profiles |
| 2. Capacity engine | Agents needed? | Erlang C + shrinkage gross-up |
| 3. Shift scheduler | Which shifts? | Set-covering integer program |
| 4. Seat demand | Bodies in building? | Interval concurrency, WFH-netted |
| 5. Layout engine | Where do partitions go? | Grid MIP minimizing partition length |

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Or run modules standalone:
```bash
python -m floorcast_core.generate_roster   # synthetic 1,200-agent roster
python -m floorcast_core.demand_engine     # per-program peaks + sharing ratios
python -m floorcast_core.optimizer         # bay allocation MIP
```

## Key findings from the reference case study

Rule-of-thumb seat methods miss in **both directions**: the one-seat-per-agent safe default
over-buys by 18 seats (~₹21.6 lakh/yr at ₹10k/seat/month), while a blanket 1.3 sharing ratio
under-buys by 11 seats — agents standing at peak. Floorcast provisions to the mathematically
exact peak, and its layout solver tells you *infeasible* before you promise a ramp will fit.

## Boundaries

Layouts are planning drafts, not construction drawings. Fire egress, travel distances,
occupancy loads, and accessibility must be verified and certified by a licensed architect
specialising in safety-exit design before implementation. All bundled data is synthetic.

## Author

**Akanchha Agarwal** — WFM professional (forecasting, capacity planning, operations analytics)
· Creator of [WFM Simplified](https://youtube.com/@WFMSimplified) · M.Tech, IIT Kanpur · LSSBB
