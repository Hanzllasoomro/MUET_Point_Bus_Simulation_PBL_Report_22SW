"""
MUET Point Bus Scheduling - Discrete Event Simulation (SimPy)
=============================================================
Course : Simulation & Modelling (22SW II & III), Mehran UET Jamshoro
Group  : 22SW06, 22SW042, 22SW154, 22SW160, 22-21SWx124

Models the afternoon peak (13:30 - 17:00) at the two campus terminals
(STC and Main Gate). Passengers arrive by a non-homogeneous Poisson
process, queue per route, and are carried by a finite fleet of buses whose
round-trip times are stochastic (lognormal + random traffic incidents).

Four dispatching policies are compared:
  BASE : static timetable, equal fleet split, fixed 15-min headways
  P1   : square-root fleet allocation  (N_r ~ sqrt(lambda_r * T_r))
  P2   : P1 + equal-load (Newell) timetable built from forecast demand
  P3   : dynamic, pooled-fleet, threshold/urgency dispatch (real-time)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import simpy

# --------------------------------------------------------------------------
# 1. System parameters (assumed / estimated, see report Sec. III)
# --------------------------------------------------------------------------
T0_CLOCK = 13 * 60 + 30          # t = 0  <=> 13:30
SERVICE_END = 15 * 60 + 50 - T0_CLOCK   # last scheduled departure 15:50
HORIZON = 17 * 60 - T0_CLOCK     # simulate until 17:00

@dataclass(frozen=True)
class Route:
    name: str
    terminal: str      # "STC" or "MG"
    share: float       # share of afternoon demand
    one_way: float     # mean one-way travel time (min) in peak traffic

ROUTES: List[Route] = [
    Route("R1 Qasimabad",      "STC", 0.30, 38.0),
    Route("R2 Latifabad",      "MG",  0.24, 46.0),
    Route("R3 Hyd City/Saddar","MG",  0.18, 42.0),
    Route("R4 Hala Naka/Wadhu","STC", 0.16, 34.0),
    Route("R5 Kotri/Jamshoro", "STC", 0.12, 16.0),
]

@dataclass
class Config:
    fleet: int = 30
    seat_cap: int = 65          # design (safe) capacity
    crush_cap: int = 85         # absolute max drivers actually load
    total_demand: float = 3400  # mean passengers in afternoon window
    demand_cv: float = 0.12     # day-to-day variation of total load
    travel_cv: float = 0.25     # lognormal CV of travel time
    incident_p: float = 0.12    # P(traffic incident on a one-way leg)
    incident_mean: float = 15.0 # mean extra delay of an incident (min)
    dwell_base: float = 2.0     # door/boarding overhead (min)
    dwell_per_pax: float = 0.025
    reposition: float = 3.0     # STC <-> Main Gate deadhead (min)
    # peak profile: two class-release waves + background
    wave1_t: float = 35.0       # 14:05
    wave2_t: float = 125.0      # 15:35
    wave_sd: float = 12.0
    w1: float = 0.55
    w2: float = 0.33
    stagger: float = 0.0        # extra sd added by staggered class release
    # dynamic policy (P3) parameters
    target_load: int = 65
    max_wait: float = 12.0      # dispatch if oldest pax waited this long
    min_load: int = 20
    route_shares: tuple = tuple(r.share for r in ROUTES)


# --------------------------------------------------------------------------
# 2. Demand model: non-homogeneous Poisson arrivals (thinning)
# --------------------------------------------------------------------------
def rate_profile(t: np.ndarray | float, cfg: Config, total: float):
    """Passenger arrival rate lambda(t) [pax/min] for all routes."""
    sd = math.sqrt(cfg.wave_sd ** 2 + cfg.stagger ** 2)
    g = lambda mu: np.exp(-0.5 * ((t - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))
    bg_window = 140.0  # background arrivals spread over 13:30-15:50
    w_bg = 1.0 - cfg.w1 - cfg.w2
    bg = np.where((np.asarray(t) >= 0) & (np.asarray(t) <= bg_window), w_bg / bg_window, 0.0)
    return total * (cfg.w1 * g(cfg.wave1_t) + cfg.w2 * g(cfg.wave2_t) + bg)


def cumulative_demand(cfg: Config, total: float, share: float, grid):
    lam = rate_profile(grid, cfg, total) * share
    return np.concatenate([[0.0], np.cumsum(0.5 * (lam[1:] + lam[:-1]) * np.diff(grid))])


# --------------------------------------------------------------------------
# 3. Analytical helpers (queueing theory, Sec. IV of report)
# --------------------------------------------------------------------------
def mean_cycle_time(r: Route, cfg: Config) -> float:
    """E[round trip] = 2*one-way*(1 + incident) * return factor + dwell."""
    leg = r.one_way + cfg.incident_p * cfg.incident_mean
    return leg + 0.85 * leg + cfg.dwell_base + cfg.dwell_per_pax * cfg.target_load


def sqrt_allocation(cfg: Config) -> List[int]:
    """Square-root rule: N_r proportional to sqrt(lambda_r * T_r).
    Derived by minimising sum(lambda_r * H_r / 2) s.t. sum(T_r/H_r) = N."""
    w = np.array([math.sqrt(s * mean_cycle_time(r, cfg)) for r, s in zip(ROUTES, cfg.route_shares)])
    raw = cfg.fleet * w / w.sum()
    n = np.floor(raw).astype(int)
    n = np.maximum(n, 1)
    while n.sum() < cfg.fleet:                     # largest remainder
        n[np.argmax(raw - n)] += 1
    while n.sum() > cfg.fleet:
        n[np.argmax(n - raw)] -= 1
    return n.tolist()


def equal_allocation(cfg: Config) -> List[int]:
    base = [cfg.fleet // len(ROUTES)] * len(ROUTES)
    for i in range(cfg.fleet - sum(base)):
        base[i] += 1
    return base


def static_timetable(headway: float = 15.0) -> List[float]:
    t, out = 30.0, []                # 14:00 .. 15:45
    while t <= SERVICE_END:
        out.append(t)
        t += headway
    return out


def newell_timetable(cfg: Config, share: float, n_bus: int, r: Route) -> List[float]:
    """Equal-load timetable: departures where forecast cumulative demand
    crosses multiples of the target load (Newell, 1971)."""
    grid = np.linspace(0, SERVICE_END, 2000)
    cum = cumulative_demand(cfg, cfg.total_demand, share, grid)
    k = max(1, int(math.ceil(cum[-1] / cfg.target_load)))
    targets = np.arange(1, k + 1) * cum[-1] / k
    times = np.interp(targets, cum, grid)
    # give the first wave a 3-min "fill" margin and keep within window
    return sorted(float(min(SERVICE_END, x + 3.0)) for x in times)


# --------------------------------------------------------------------------
# 4. Simulation model
# --------------------------------------------------------------------------
@dataclass
class Stats:
    waits: List[float] = field(default_factory=list)
    loads: List[int] = field(default_factory=list)
    trip_t: List[float] = field(default_factory=list)
    queue_trace: Dict[str, List[tuple]] = field(default_factory=dict)
    busy_time: float = 0.0
    arrivals: int = 0
    stranded: int = 0
    route_waits: Dict[int, List[float]] = field(default_factory=dict)


class TransitSystem:
    def __init__(self, cfg: Config, policy: str, seed: int):
        self.cfg, self.policy = cfg, policy
        self.rng = random.Random(seed)
        self.env = simpy.Environment()
        self.queues: List[List[float]] = [[] for _ in ROUTES]
        self.st = Stats(route_waits={i: [] for i in range(len(ROUTES))})
        self.st.queue_trace = {"STC": [], "MG": []}
        self.total = max(0.0, cfg.total_demand * self.rng.gauss(1.0, cfg.demand_cv))

        if policy == "BASE":
            alloc = equal_allocation(cfg)
            tables = [static_timetable(15.0) for _ in ROUTES]
        elif policy == "P1":
            alloc = sqrt_allocation(cfg)
            tables = [static_timetable(15.0) for _ in ROUTES]
        elif policy == "P2":
            alloc = sqrt_allocation(cfg)
            tables = [newell_timetable(cfg, s, n, r) for r, s, n in
                      zip(ROUTES, cfg.route_shares, alloc)]
        elif policy == "P3":
            alloc, tables = None, None
        else:
            raise ValueError(policy)

        for i, r in enumerate(ROUTES):
            self.env.process(self.passenger_source(i, r))
        self.env.process(self.monitor())

        if policy == "P3":
            self.idle = [ROUTES[i % len(ROUTES)].terminal for i in range(cfg.fleet)]
            self.incoming = [0] * len(ROUTES)
            self.env.process(self.dynamic_dispatcher())
        else:
            self.pools = []
            for i, n in enumerate(alloc):
                pool = simpy.Store(self.env)
                for _ in range(n):
                    pool.put(i)
                self.pools.append(pool)
                self.env.process(self.timetable_runner(i, tables[i]))
                self.env.process(self.sweeper(i))

    # ---------------- passengers ----------------
    def passenger_source(self, i: int, r: Route):
        share = self.cfg.route_shares[i]
        lam_max = float(np.max(rate_profile(np.linspace(0, 160, 800), self.cfg, self.total))) * share * 1.05
        t = 0.0
        while True:
            t += self.rng.expovariate(lam_max)
            if t > 160:
                return
            if self.rng.random() <= rate_profile(t, self.cfg, self.total) * share / lam_max:
                yield self.env.timeout(t - self.env.now)
                self.queues[i].append(self.env.now)
                self.st.arrivals += 1

    # ---------------- bus trip ----------------
    def leg_time(self, mean: float) -> float:
        cv = self.cfg.travel_cv
        s2 = math.log(1 + cv * cv)
        x = self.rng.lognormvariate(math.log(mean) - s2 / 2, math.sqrt(s2))
        if self.rng.random() < self.cfg.incident_p:
            x += self.rng.expovariate(1.0 / self.cfg.incident_mean)
        return x

    def board(self, i: int) -> int:
        cap = self.cfg.crush_cap
        q = self.queues[i]
        n = min(cap, len(q))
        now = self.env.now
        for a in q[:n]:
            self.st.waits.append(now - a)
            self.st.route_waits[i].append(now - a)
        del q[:n]
        self.st.loads.append(n)
        return n

    def trip(self, i: int, n: int):
        r = ROUTES[i]
        dwell = self.cfg.dwell_base + self.cfg.dwell_per_pax * n
        out = self.leg_time(r.one_way)
        back = 0.85 * self.leg_time(r.one_way)
        total = dwell + out + back
        self.st.trip_t.append(total)
        self.st.busy_time += total
        yield self.env.timeout(total)

    # ---------------- static policies ----------------
    def timetable_runner(self, i: int, table: List[float]):
        for slot in table:
            if slot > self.env.now:
                yield self.env.timeout(slot - self.env.now)
            bus = yield self.pools[i].get()          # late if no bus is back
            n = self.board(i)
            self.env.process(self.static_trip(i, n))

    def static_trip(self, i, n):
        yield from self.trip(i, n)
        yield self.pools[i].put(i)

    def sweeper(self, i: int):
        """After the timetable ends, returning buses clear leftover queues."""
        yield self.env.timeout(SERVICE_END + 1)
        while self.env.now < HORIZON:
            if self.queues[i]:
                bus = yield self.pools[i].get()
                if self.queues[i]:
                    n = self.board(i)
                    self.env.process(self.static_trip(i, n))
                else:
                    yield self.pools[i].put(bus)
            yield self.env.timeout(1.0)

    # ---------------- dynamic policy P3 ----------------
    def urgency(self, i: int) -> float:
        q = self.queues[i]
        if not q or self.incoming[i]:
            return -1.0
        oldest = self.env.now - q[0]
        n = len(q)
        late = self.env.now > SERVICE_END
        full = n >= self.cfg.target_load
        # partial (under-filled) departures are allowed only when the idle
        # fleet exceeds the reserve needed for forecast demand in the next
        # 30 min -> protects capacity for the class-release waves.
        partial_ok = (oldest >= self.cfg.max_wait and n >= self.cfg.min_load
                      and len(self.idle) > self.reserve())
        starving = oldest >= 3 * self.cfg.max_wait and n >= 10
        if not (full or partial_ok or starving or late):
            return -1.0
        # priority: passenger-minutes already waited (+ fullness bonus)
        return n * oldest + 10.0 * min(n, self.cfg.target_load)

    def reserve(self) -> int:
        """Buses to keep idle for forecast demand in the next 30 minutes."""
        grid = np.linspace(self.env.now, self.env.now + 30.0, 31)
        fut = float(np.trapezoid(rate_profile(grid, self.cfg, self.cfg.total_demand), grid))
        return int(fut // self.cfg.target_load)

    def dynamic_dispatcher(self):
        while self.env.now < HORIZON:
            while self.idle:
                scores = [self.urgency(i) for i in range(len(ROUTES))]
                best = int(np.argmax(scores))
                if scores[best] < 0:
                    break
                term = ROUTES[best].terminal
                if term in self.idle:          # bus already at terminal
                    self.idle.remove(term)
                    n = self.board(best)       # board immediately
                    self.env.process(self.dynamic_trip(best, 0.0, n))
                else:                           # reposition from other gate
                    self.idle.pop()
                    self.incoming[best] += 1   # reserve route for that bus
                    self.env.process(self.dynamic_trip(best, self.cfg.reposition))
            yield self.env.timeout(0.5)

    def dynamic_trip(self, i: int, delay: float, n: int | None = None):
        if delay:
            self.st.busy_time += delay
            yield self.env.timeout(delay)
            self.incoming[i] -= 1
            n = self.board(i)
        yield from self.trip(i, n)
        self.idle.append(ROUTES[i].terminal)

    # ---------------- monitor ----------------
    def monitor(self):
        while True:
            stc = sum(len(q) for q, r in zip(self.queues, ROUTES) if r.terminal == "STC")
            mg = sum(len(q) for q, r in zip(self.queues, ROUTES) if r.terminal == "MG")
            self.st.queue_trace["STC"].append((self.env.now, stc))
            self.st.queue_trace["MG"].append((self.env.now, mg))
            yield self.env.timeout(1.0)

    def run(self) -> Stats:
        self.env.run(until=HORIZON)
        self.st.stranded = sum(len(q) for q in self.queues)
        return self.st


# --------------------------------------------------------------------------
# 5. KPIs and replication driver
# --------------------------------------------------------------------------
def kpis(st: Stats, cfg: Config) -> Dict[str, float]:
    w = np.array(st.waits) if st.waits else np.array([0.0])
    loads = np.array(st.loads) if st.loads else np.array([0])
    served = len(st.waits)
    q_stc = max(q for _, q in st.queue_trace["STC"])
    q_mg = max(q for _, q in st.queue_trace["MG"])
    window = SERVICE_END + 60.0           # 13:30 - 16:50 operating window
    return {
        "mean_wait": float(w.mean()),
        "p95_wait": float(np.percentile(w, 95)),
        "pct_wait_gt15": float(100 * (w > 15).mean()),
        "max_q_stc": float(q_stc),
        "max_q_mg": float(q_mg),
        "trips": float(len(loads)),
        "mean_load": float(loads.mean()),
        "load_factor": float(100 * loads.mean() / cfg.seat_cap),
        "pct_overloaded": float(100 * (loads > cfg.seat_cap).mean()),
        "pct_underused": float(100 * (loads < 0.4 * cfg.seat_cap).mean()),
        "utilization": float(100 * st.busy_time / (cfg.fleet * window)),
        "stranded": float(st.stranded),
        "served": float(served),
    }


def replicate(cfg: Config, policy: str, reps: int = 30, seed0: int = 2206):
    rows = []
    for k in range(reps):
        st = TransitSystem(cfg, policy, seed0 + 1000 * k).run()
        rows.append(kpis(st, cfg))
    keys = rows[0].keys()
    out = {}
    for key in keys:
        v = np.array([r[key] for r in rows])
        half = 2.045 * v.std(ddof=1) / math.sqrt(len(v)) if len(v) > 1 else 0.0  # t(0.975,29)
        out[key] = (float(v.mean()), float(half))
    return out, rows


if __name__ == "__main__":
    cfg = Config()
    print("sqrt allocation:", sqrt_allocation(cfg), " equal:", equal_allocation(cfg))
    for pol in ["BASE", "P1", "P2", "P3"]:
        res, _ = replicate(cfg, pol, reps=30)
        print(f"{pol:5s}", "  ".join(f"{k}={m:.1f}±{h:.1f}" for k, (m, h) in res.items()))
