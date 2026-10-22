"""Simulate 0DTE option trades for every setup variant, then split train/test."""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

from .options import OptionCosts, bs_price
from .signals import SIGNALS, WINDOWS

LAST_MINUTE = 359          # exit by 3:29pm: Alpaca rejects new 0DTE ETF orders after 3:30
COOLDOWN = 5               # minutes between trades on a variant
MAX_PER_DAY = 3

GRID = {
    "signal": list(SIGNALS),
    "window": list(WINDOWS),
    "trend": [False, True],          # True: only calls above EMA50, only puts below
    "stop": [0.25, 0.40, 0.60],      # exit if option premium falls this much
    "target": [0.30, 0.60, 1.00],    # take profit at +30/60/100% premium
    "hold": [20, 45, 90],            # time stop, minutes
    "otm": [0, 1],                   # 0 = at the money, 1 = one strike out
}


@dataclass(frozen=True)
class Variant:
    signal: str
    window: str
    trend: bool
    stop: float
    target: float
    hold: int
    otm: int

    def label(self):
        return (f"{self.signal} | {self.window} | {'trend' if self.trend else 'any'} | "
                f"stop -{int(self.stop*100)}% | tgt +{int(self.target*100)}% | {self.hold}m | "
                f"{'ATM' if self.otm == 0 else '1 OTM'}")


def all_variants():
    keys = list(GRID)
    return [Variant(**dict(zip(keys, combo))) for combo in itertools.product(*GRID.values())]


class Simulator:
    def __init__(self, days, costs: OptionCosts | None = None):
        self.days = days
        self.costs = costs or OptionCosts()
        self.paths = {}     # (day, i, kind, otm) -> option path
        self.results = {}   # (day, i, kind, otm, stop, target, hold) -> trade
        self.entries = {name: [(di, i, k) for di, d in enumerate(days) for i, k in fn(d) if i + 1 < len(d.c)]
                        for name, fn in SIGNALS.items()}

    def path(self, di, i, kind, otm):
        key = (di, i, kind, otm)
        p = self.paths.get(key)
        if p is not None:
            return p
        d, c = self.days[di], self.costs
        j0 = i + 1
        S0 = d.o[j0]
        K = c.strike(S0, kind, otm)
        iv = c.iv(d.rv[i])
        left = lambda j: 390 - d.minute[j]
        mid0 = bs_price(S0, K, left(j0), iv, kind)
        hi, lo, cl = [], [], []
        for j in range(j0, min(len(d.c), j0 + 91)):
            if d.minute[j] > LAST_MINUTE:
                break
            up = bs_price(d.h[j], K, left(j) - 1, iv, kind)
            dn = bs_price(d.l[j], K, left(j) - 1, iv, kind)
            hi.append(max(up, dn)); lo.append(min(up, dn))
            cl.append(bs_price(d.c[j], K, left(j) - 1, iv, kind))
        p = (mid0, K, hi, lo, cl)
        self.paths[key] = p
        return p

    def trade(self, di, i, kind, v: Variant):
        key = (di, i, kind, v.otm, v.stop, v.target, v.hold)
        t = self.results.get(key)
        if t is not None:
            return t
        mid0, K, hi, lo, cl = self.path(di, i, kind, v.otm)
        c = self.costs
        if mid0 < 0.05 or not cl:  # nothing worth buying
            self.results[key] = None
            return None
        buy = mid0 + c.half_spread(mid0)
        stop_px, tgt_px = mid0 * (1 - v.stop), mid0 * (1 + v.target)
        exit_mid, why, n = cl[-1], "cutoff 3:29pm", len(cl) - 1
        for k in range(len(cl)):
            if lo[k] <= stop_px:          # conservative: stop checked first inside a bar
                exit_mid, why, n = stop_px, "stop", k; break
            if hi[k] >= tgt_px:
                exit_mid, why, n = tgt_px, "target", k; break
            if k + 1 >= v.hold:
                exit_mid, why, n = cl[k], "time", k; break
        sell = max(0.0, exit_mid - c.half_spread(exit_mid))
        pnl = (sell - buy) * 100 - 2 * c.fee_per_contract
        t = {"di": di, "i": i, "kind": kind, "exit_j": i + 1 + n, "pnl": pnl,
             "ret": pnl / (buy * 100), "cost": buy * 100, "why": why, "K": K}
        self.results[key] = t
        return t

    def run(self, v: Variant, entries=None):
        lo_m, hi_m = WINDOWS[v.window]
        trades, last_exit, day, count = [], -10**9, None, 0
        for di, i, kind in (entries if entries is not None else self.entries[v.signal]):
            d = self.days[di]
            if di != day:
                day, count, last_exit = di, 0, -10**9
            m = d.minute[i + 1]
            if not (lo_m <= m <= hi_m) or count >= MAX_PER_DAY or i + 1 <= last_exit + COOLDOWN:
                continue
            if v.trend and ((kind == "C") != (d.c[i] > d.e50[i])):
                continue
            t = self.trade(di, i, kind, v)
            if t is None:
                continue
            trades.append(t)
            last_exit, count = t["exit_j"], count + 1
        return trades

    def random_entries(self, signal, seed):
        """Same number of entries per day as the real signal, at random times and sides."""
        rng = random.Random(seed)
        per_day = {}
        for di, i, k in self.entries[signal]:
            per_day[di] = per_day.get(di, 0) + 1
        out = []
        for di, n in sorted(per_day.items()):
            L = len(self.days[di].c) - 2
            for i in sorted(rng.sample(range(1, L), min(n, L - 1))):
                out.append((di, i, rng.choice("CP")))
        return out


def stats(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0, "win": 0, "avg": 0, "net": 0, "pf": 0, "t": 0, "avg_ret": 0, "cost": 0}
    pnl = [t["pnl"] for t in trades]
    rets = [t["ret"] for t in trades]
    wins = [p for p in pnl if p > 0]
    gl = -sum(p for p in pnl if p <= 0)
    mu = sum(rets) / n
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / max(1, n - 1)) or 1e-9
    return {"n": n, "win": len(wins) / n, "avg": sum(pnl) / n, "net": sum(pnl),
            "pf": (sum(wins) / gl) if gl else (99.0 if wins else 0.0),
            "t": mu / sd * math.sqrt(n), "avg_ret": mu, "cost": sum(t["cost"] for t in trades) / n}


def split(trades, cut_day):
    return [t for t in trades if t["di"] < cut_day], [t for t in trades if t["di"] >= cut_day]
