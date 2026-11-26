# MUET Point Bus Scheduling: Discrete Event Simulation (PBL)

**Course:** Simulation & Modelling, Batch 22SW II & III, Department of Software Engineering, MUET Jamshoro
**Instructor:** Engr. Umar Farooq
**Group (Roll Nos.):** 22SW06, 22SW042, 22SW154, 22SW160, 22-21SWx124

This project models, simulates and optimizes the MUET Point bus system during the afternoon peak
(13:30–17:00) at the **STC** and **Main Gate** terminals. Passengers arrive as a non-homogeneous
Poisson process with two class-release waves. Five routes are served by a 30-bus fleet whose round
trips are stochastic (lognormal travel times plus random traffic incidents).

## Policies compared

| Code | Policy | Idea |
|------|--------|------|
| BASE | Static timetable | Equal fleet split, a bus every 15 min per route (current practice) |
| P1 | Square-root allocation | N_r ∝ √(λ_r T_r), from a Lagrangian optimum |
| P2 | Equal-load (Newell) timetable | Departures where cumulative forecast demand hits multiples of 65 |
| P3 | Reserve-aware dynamic dispatch | Pooled fleet with real-time threshold and priority rule, keeping a forecast-based reserve |

## Headline results (30 replications)

| KPI | Baseline | P2 | P3 |
|-----|---------:|---:|---:|
| Mean wait (min) | 25.9 | 6.9 | 6.3 |
| 95th-percentile wait (min) | 62 | 30 | 23 |
| Fleet utilization | 68 % | 82 % | 84 % |

With 22 buses, P3 still beats the baseline with 34 buses. The bottleneck is the scheduling, not the fleet size.

## Folder layout

```
umer random/
├── MUET_Point_Bus_Simulation_PBL_Report_22SW.pdf   <- final IEEE-style report
├── README.md
├── requirements.txt
├── results.json            <- all numeric results (generated)
├── figures/                <- all graphs and diagrams (generated)
└── src/
    ├── muet_sim.py         <- SimPy model + 4 policies + KPIs
    ├── experiments.py      <- every experiment, figure, verification test
    └── build_report.py     <- builds the PDF report from results.json
```

## How to run

Requires Python 3.10 or newer.

```bash
pip install -r requirements.txt
python src/muet_sim.py          # quick comparison table in the console (~15 s)
python src/experiments.py       # all experiments + figures + results.json (~2 min)
python src/build_report.py      # regenerate the PDF report
```

`build_report.py` uses the Times New Roman and Consolas fonts from `C:/Windows/Fonts`. On
Linux or macOS, change the `F` path in that file to a folder that has equivalent TTF fonts.

## Calibrating with real data

All assumptions are in the `Config` dataclass and the `ROUTES` list in `src/muet_sim.py`: demand,
route shares, travel times, incident rate, capacity and fleet size. Replace them with gate counts and
GPS logs from the transport office, then re-run `experiments.py`.

## Verification

- **Little's law:** the integral of queue length matches the sum of passenger waits (1.6 % difference, from 1-minute sampling).
- **Analytical headway test:** the simulated wait is 8.95 ± 0.10 min against an exact value of 8.93 min.
