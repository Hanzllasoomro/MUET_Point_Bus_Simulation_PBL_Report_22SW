"""
Runs every experiment used in the report and writes figures + results.json
Usage:  python experiments.py        (takes ~1-2 minutes)
"""
import json
import math
import os
from dataclasses import replace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np

import muet_sim as m

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "figures")
os.makedirs(OUT, exist_ok=True)
POL = ["BASE", "P1", "P2", "P3"]
NAMES = {"BASE": "Baseline (static)", "P1": "P1 sqrt-allocation",
         "P2": "P2 equal-load timetable", "P3": "P3 dynamic dispatch"}
COL = {"BASE": "#b23a48", "P1": "#e09f3e", "P2": "#3c6e71", "P3": "#284b8c"}
plt.rcParams.update({"font.size": 8, "font.family": "serif", "axes.grid": True,
                     "grid.alpha": .3, "figure.dpi": 200})
REPS = 30
cfg = m.Config()
results = {}


def clock(ax):
    ticks = np.arange(0, m.HORIZON + 1, 30)
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{(m.T0_CLOCK + t)//60}:{(m.T0_CLOCK + t)%60:02d}" for t in ticks])


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight")
    plt.close(fig)

# ------------------------------------------------------------ 1. baseline vs policies
main = {}
for p in POL:
    agg, rows = m.replicate(cfg, p, REPS)
    main[p] = agg
results["main"] = main
results["alloc"] = {"equal": m.equal_allocation(cfg), "sqrt": m.sqrt_allocation(cfg)}
results["cycle"] = {r.name: m.mean_cycle_time(r, cfg) for r in m.ROUTES}
results["newell_slots"] = {r.name: len(m.newell_timetable(cfg, s, 0, r))
                           for r, s in zip(m.ROUTES, cfg.route_shares)}

# ------------------------------------------------------------ 2. arrival profile figure
t = np.linspace(0, 160, 600)
fig, ax = plt.subplots(figsize=(3.4, 1.9))
ax.fill_between(t, m.rate_profile(t, cfg, cfg.total_demand), color="#3c6e71", alpha=.35)
ax.plot(t, m.rate_profile(t, cfg, cfg.total_demand), color="#3c6e71", lw=1.2, label=r"$\lambda(t)$ total")
st = m.rate_profile(t, replace(cfg, stagger=15), cfg.total_demand)
ax.plot(t, st, "--", color="#b23a48", lw=1, label="staggered release (+15 min)")
for s in m.newell_timetable(cfg, cfg.route_shares[0], 0, m.ROUTES[0]):
    ax.axvline(s, color="k", lw=.4, alpha=.5)
ax.set_ylabel("passengers / min"); clock(ax); ax.set_xlim(0, 160)
ax.legend(loc="upper right", fontsize=6)
ax.set_title("Afternoon demand profile (lines: P2 slots, R1)", fontsize=8)
save(fig, "fig_demand.png")

# ------------------------------------------------------------ 3. queue traces
fig, axs = plt.subplots(2, 1, figsize=(3.4, 3.2), sharex=True)
for p in POL:
    s = m.TransitSystem(cfg, p, 2206).run()
    for ax, term in zip(axs, ["STC", "MG"]):
        tt, q = zip(*s.queue_trace[term])
        ax.plot(tt, q, color=COL[p], lw=1, label=NAMES[p])
for ax, term in zip(axs, ["STC terminal", "Main Gate terminal"]):
    ax.set_ylabel("queue (pax)"); ax.set_title(term, fontsize=8)
clock(axs[1]); axs[0].legend(fontsize=6)
save(fig, "fig_queues.png")

# ------------------------------------------------------------ 4. wait CDF
fig, ax = plt.subplots(figsize=(3.4, 2.1))
for p in POL:
    w = []
    for k in range(10):
        w += m.TransitSystem(cfg, p, 2206 + 1000 * k).run().waits
    w = np.sort(w)
    ax.plot(w, np.arange(1, len(w) + 1) / len(w), color=COL[p], lw=1.2, label=NAMES[p])
ax.axvline(15, color="grey", ls=":", lw=1)
ax.set_xlabel("waiting time (min)"); ax.set_ylabel("ECDF"); ax.set_xlim(0, 90)
ax.legend(fontsize=6, loc="lower right")
save(fig, "fig_wait_cdf.png")

# ------------------------------------------------------------ 5. KPI bars
fig, axs = plt.subplots(2, 2, figsize=(3.4, 3.0))
for ax, key, lab in zip(axs.flat, ["mean_wait", "pct_wait_gt15", "pct_overloaded", "utilization"],
                        ["mean wait (min)", "% pax waiting > 15 min",
                         "% trips over 65 pax", "fleet utilization (%)"]):
    mu = [main[p][key][0] for p in POL]; ci = [main[p][key][1] for p in POL]
    ax.bar(POL, mu, yerr=ci, color=[COL[p] for p in POL], capsize=2)
    for i, v in enumerate(mu):
        ax.text(i, v + ci[i] + max(mu) * .03, f"{v:.1f}", ha="center", fontsize=5.5)
    ax.set_ylim(0, max(mu) * 1.3)
    ax.set_title(lab, fontsize=7); ax.tick_params(labelsize=6)
save(fig, "fig_kpis.png")

# ------------------------------------------------------------ 6. fleet sensitivity
fleets = [22, 24, 26, 28, 30, 32, 34]
sens = {p: [] for p in ["BASE", "P2", "P3"]}
for p in sens:
    for n in fleets:
        agg, _ = m.replicate(replace(cfg, fleet=n), p, 15)
        sens[p].append(agg["mean_wait"])
results["fleet_sens"] = {"fleets": fleets, **sens}
fig, ax = plt.subplots(figsize=(3.4, 2.0))
for p in sens:
    mu = [x[0] for x in sens[p]]; ci = [x[1] for x in sens[p]]
    ax.errorbar(fleets, mu, yerr=ci, color=COL[p], marker="o", ms=3, lw=1.2, capsize=2, label=NAMES[p])
ax.set_xlabel("fleet size (buses)"); ax.set_ylabel("mean wait (min)")
ax.legend(fontsize=6)
save(fig, "fig_fleet.png")

# ------------------------------------------------------------ 7. what-if scenarios
scen = {
    "Nominal": cfg,
    "Demand +20%": replace(cfg, total_demand=cfg.total_demand * 1.2),
    "Traffic incidents x2": replace(cfg, incident_p=cfg.incident_p * 2),
    "3 buses broken": replace(cfg, fleet=cfg.fleet - 3),
    "Safety cap 65": replace(cfg, crush_cap=65),
    "Staggered release": replace(cfg, stagger=15.0),
}
wi = {}
for sname, c in scen.items():
    wi[sname] = {p: m.replicate(c, p, 20)[0] for p in ["BASE", "P2", "P3"]}
results["whatif"] = wi
fig, ax = plt.subplots(figsize=(3.4, 2.7))
x = np.arange(len(scen))[::-1]; w = .27
for j, p in enumerate(["BASE", "P2", "P3"]):
    mu = [wi[s][p]["mean_wait"][0] for s in scen]; ci = [wi[s][p]["mean_wait"][1] for s in scen]
    ax.barh(x - (j - 1) * w, mu, w, xerr=ci, capsize=1.5, color=COL[p], label=NAMES[p])
ax.set_yticks(x); ax.set_yticklabels(list(scen.keys()), fontsize=6.5)
ax.set_xlabel("mean wait (min)"); ax.legend(fontsize=5.8, loc="lower center", bbox_to_anchor=(0.4, 1.0), ncol=3, frameon=False, handlelength=1)
save(fig, "fig_whatif.png")

# ------------------------------------------------------------ 8. verification
# (a) Little's law: integral of queue length == sum of waits
s = m.TransitSystem(cfg, "BASE", 7).run()
area = sum(q for _, q in s.queue_trace["STC"]) + sum(q for _, q in s.queue_trace["MG"])
results["little"] = {"area_pax_min": area, "sum_waits": float(sum(s.waits) + 0.0),
                     "stranded": s.stranded}
# (b) deterministic-headway check: uniform low demand, H = 15 -> analytic wait
vc = replace(cfg, w1=0.0, w2=0.0, total_demand=500, demand_cv=0.0,
             incident_p=0.0, travel_cv=0.05)
sim_w = m.replicate(vc, "BASE", 30)[0]["mean_wait"]
slots = np.array(m.static_timetable(15.0))
tt = np.linspace(0, 140, 140001)
nxt = np.array([slots[slots >= x][0] if (slots >= x).any() else 141.0 for x in tt[::100]])
analytic = float(np.mean(nxt - tt[::100]))
results["verify_headway"] = {"sim": sim_w, "analytic": analytic}

# ------------------------------------------------------------ 9. conceptual diagrams
def box(ax, xy, text, fc="#e8eef4", w=1.9, h=.55, style="round,pad=0.04"):
    ax.add_patch(FancyBboxPatch((xy[0] - w / 2, xy[1] - h / 2), w, h, boxstyle=style,
                                fc=fc, ec="#284b8c", lw=.8))
    ax.text(*xy, text, ha="center", va="center", fontsize=6.5)

def arrow(ax, a, b, text="", rad=0.0, off=(0, 0)):
    ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="-|>", lw=.8, color="#333",
                connectionstyle=f"arc3,rad={rad}"))
    if text:
        ax.text((a[0] + b[0]) / 2 + off[0], (a[1] + b[1]) / 2 + off[1], text, fontsize=5.8,
                ha="center", color="#7a1f2b")

# bus state-transition diagram
fig, ax = plt.subplots(figsize=(3.4, 2.7)); ax.axis("off"); ax.set_xlim(-0.2, 6.2); ax.set_ylim(0, 4.6)
S = {"IDLE": (1.2, 3.9), "BOARD": (4.8, 3.9), "OUT": (4.8, 2.2), "RET": (1.2, 2.2),
     "REPOS": (3.0, .6)}
box(ax, S["IDLE"], "IDLE at terminal\n(STC / Main Gate)", "#dfe7d6", w=2.4)
box(ax, S["BOARD"], "BOARDING\n(dwell 2 + 0.025n min)", w=2.4)
box(ax, S["OUT"], "IN SERVICE\n(outbound, lognormal)", w=2.4)
box(ax, S["RET"], "DEADHEAD RETURN\n(0.85 x outbound)", w=2.4)
box(ax, S["REPOS"], "REPOSITION\nSTC <-> MG (3 min)", "#f6e6d0", w=2.2)
arrow(ax, (2.4, 3.9), (3.6, 3.9), "dispatch", off=(0, .12))
arrow(ax, (4.8, 3.62), (4.8, 2.48), "doors close", off=(-.5, 0))
arrow(ax, (3.6, 2.2), (2.4, 2.2), "drop-off", off=(0, .12))
arrow(ax, (1.2, 2.48), (1.2, 3.62), "arrive\ncampus", off=(-.55, 0))
arrow(ax, (0.3, 3.62), (1.9, 0.6), "", rad=.45)
ax.text(-0.15, 1.0, "P3: bus needed\nat other gate", fontsize=5.8, color="#7a1f2b")
arrow(ax, (4.1, 0.6), (5.7, 3.62), "", rad=.45)
ax.text(5.1, 1.0, "arrived", fontsize=5.8, color="#7a1f2b")
ax.text(3.0, 2.95, "+ incident delay\nP = 0.12, Exp(15)", fontsize=5.8, ha="center", color="#555")
save(fig, "fig_state.png")

# P3 dispatch flowchart
fig, ax = plt.subplots(figsize=(3.4, 3.8)); ax.axis("off"); ax.set_xlim(-0.3, 6.6); ax.set_ylim(0, 7.4)
box(ax, (3, 7.0), "Every 0.5 min (or bus returns)", "#dfe7d6", w=3.4)
box(ax, (3, 6.0), "Idle bus available?", "#f6e6d0", w=2.6)
box(ax, (3, 5.0), "For each route r: queue n_r, oldest wait a_r", w=4.6)
box(ax, (3, 3.95), "ready_r = n_r >= L*  OR\n(a_r >= W* AND n_r >= 20 AND idle > reserve)\nOR a_r >= 3W*  OR  t > 15:50", "#f6e6d0", w=5.4, h=.85)
box(ax, (3, 2.75), "r* = argmax  n_r a_r + 10 min(n_r, L*)", w=4.6)
box(ax, (3, 1.8), "Bus at terminal of r*? else reposition 3 min", "#f6e6d0", w=4.6)
box(ax, (3, .8), "Board min(n, C) pax, start trip,\nloop for next idle bus", "#dfe7d6", w=3.6)
for a, b in [((3, 6.72), (3, 6.28)), ((3, 5.72), (3, 5.28)), ((3, 4.72), (3, 4.4)),
             ((3, 3.5), (3, 3.03)), ((3, 2.47), (3, 2.08)), ((3, 1.52), (3, 1.12))]:
    arrow(ax, a, b)
ax.text(4.5, 6.0, "no -> wait", fontsize=6, color="#7a1f2b")
ax.text(3.15, 3.27, "none ready -> wait", fontsize=5.6, color="#7a1f2b")
save(fig, "fig_flow.png")

with open(os.path.join(OUT, "..", "results.json"), "w") as f:
    json.dump(results, f, indent=1)
print(json.dumps({k: results[k] for k in ["little", "verify_headway", "alloc", "newell_slots"]}, indent=1))
