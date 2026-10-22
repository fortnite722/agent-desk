"""PATTERN LAB: find 0DTE QQQ setups that survive data they've never seen.

  python pattern_lab.py --source synthetic          # pipeline test on random data
  python pattern_lab.py --source alpaca --days 500  # REAL QQQ 1-min bars (needs paper keys)
  python pattern_lab.py --source data/QQQ_1min.csv  # your own CSV

How it avoids fooling you:
  1. Every setup variant (signal x time window x trend filter x stop x target x hold x strike)
     is simulated as a real 0DTE option trade: spread + fees on both sides.
  2. Days are split in time order: the first 60% is TRAIN, the last 40% is TEST.
     Setups are picked on TRAIN only. TEST is the exam they never studied for.
  3. A coin-flip control runs the same grid on RANDOM entries. If the "best" real
     setups don't beat the best coin-flips, there is no pattern, only luck.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time

from lab.engine import Simulator, all_variants, split, stats
from lab.market import build_days, load_bars
from desk.stats import monte_carlo

TRAIN_T = 2.0      # train t-stat needed to be a candidate
TEST_MIN_N = 30
TEST_PF = 1.15


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="synthetic")
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--start", help="YYYY-MM-DD start date for --source alpaca")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--train", type=float, default=0.6)
    ap.add_argument("--controls", type=int, default=24, help="coin-flip control runs")
    ap.add_argument("--out", default="lab_out")
    a = ap.parse_args()
    t0 = time.time()

    rows = load_bars(a.source, a.start, days=a.days, seed=a.seed)
    days = build_days(rows)
    if len(days) < 40:
        raise SystemExit(f"only {len(days)} full days of data; need 40+")
    cut = int(len(days) * a.train)
    print(f"{len(days)} trading days: TRAIN {days[0].date} -> {days[cut-1].date}, "
          f"TEST {days[cut].date} -> {days[-1].date}")

    sim = Simulator(days)
    variants = all_variants()
    print(f"testing {len(variants)} setup variants...")
    results, total_trades = [], 0
    for k, v in enumerate(variants):
        tr, te = split(sim.run(v), cut)
        total_trades += len(tr) + len(te)
        results.append((v, stats(tr), stats(te), te))
        if k % 500 == 0:
            print(f"  {k}/{len(variants)}  ({time.time()-t0:.0f}s)")

    # ---- selection on TRAIN only
    cands = [r for r in results if r[1]["n"] >= 50 and r[1]["t"] >= TRAIN_T]
    cands.sort(key=lambda r: -r[1]["t"])
    survivors = [r for r in cands if r[2]["n"] >= TEST_MIN_N and r[2]["net"] > 0
                 and r[2]["pf"] >= TEST_PF and r[2]["t"] >= 1.0]

    # ---- coin-flip control: same grid, random entries
    ctrl_pass, ctrl_surv, ctrl_tested = 0, 0, 0
    sigs = list(sim.entries)
    for s in range(a.controls):
        sig = sigs[s % len(sigs)]
        ents = sim.random_entries(sig, seed=1000 + s)
        for v in variants:
            if v.signal != sig:
                continue
            tr, te = split(sim.run(v, ents), cut)
            S1, S2 = stats(tr), stats(te)
            ctrl_tested += 1
            total_trades += S1["n"] + S2["n"]
            if S1["n"] >= 50 and S1["t"] >= TRAIN_T:
                ctrl_pass += 1
                if S2["n"] >= TEST_MIN_N and S2["net"] > 0 and S2["pf"] >= TEST_PF and S2["t"] >= 1.0:
                    ctrl_surv += 1

    real_rate = len(cands) / len(variants)
    ctrl_rate = ctrl_pass / max(1, ctrl_tested)
    real_surv = len(survivors) / len(variants)
    ctrl_surv_rate = ctrl_surv / max(1, ctrl_tested)

    os.makedirs(a.out, exist_ok=True)
    with open(f"{a.out}/all_variants.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["setup", "train_n", "train_win", "train_avg$", "train_pf", "train_t",
                    "test_n", "test_win", "test_avg$", "test_pf", "test_t", "avg_cost$"])
        for v, s1, s2, _ in sorted(results, key=lambda r: -r[1]["t"]):
            w.writerow([v.label(), s1["n"], f"{s1['win']:.2f}", f"{s1['avg']:.2f}", f"{s1['pf']:.2f}", f"{s1['t']:.2f}",
                        s2["n"], f"{s2['win']:.2f}", f"{s2['avg']:.2f}", f"{s2['pf']:.2f}", f"{s2['t']:.2f}",
                        f"{s1['cost']:.0f}"])

    def row(r):
        v, s1, s2, te = r
        return {"setup": v.label(), "variant": vars(v), "train": s1, "test": s2}

    best = survivors[0] if survivors else None
    mc = None
    curve = []
    if best:
        te = best[3]
        eq = 0.0
        for t in te:
            eq += t["pnl"]
            curve.append([str(days[t["di"]].date), round(eq, 2)])
        start = max(500.0, 3 * best[2]["cost"])
        mc = monte_carlo([{"pnl": t["pnl"], "equity_at_entry": start} for t in te], start, n_trades=100)
        mc["start_cash"] = start

    verdict = (
        "NO EDGE FOUND: real setups did no better than coin-flips. Don't trade any of them."
        if real_surv <= ctrl_surv_rate * 1.5 or not survivors else
        f"{len(survivors)} setups survived and beat the coin-flip control. Paper-trade the top one next.")

    report = {
        "source": a.source, "days": len(days), "train_days": cut, "test_days": len(days) - cut,
        "date_range": [str(days[0].date), str(days[-1].date)],
        "variants": len(variants), "trades_simulated": total_trades, "runtime_s": round(time.time() - t0),
        "train_pass_rate": real_rate, "control_train_pass_rate": ctrl_rate,
        "survivor_rate": real_surv, "control_survivor_rate": ctrl_surv_rate,
        "candidates": [row(r) for r in cands[:15]],
        "best_overall": [row(r) for r in sorted(results, key=lambda r: -r[1]["t"])[:10] if r[1]["n"] >= 30],
        "survivors": [row(r) for r in survivors[:15]],
        "best_curve": curve, "best_monte_carlo": mc, "verdict": verdict,
        "by_signal": _by_signal(results),
    }
    with open(f"{a.out}/report.json", "w") as f:
        json.dump(report, f, default=str, indent=1)

    line = "=" * 64
    print(f"\n{line}\n PATTERN LAB  ({a.source}, {len(days)} days)\n{line}")
    print(f" {len(variants)} setups x 2 periods, plus {ctrl_tested} coin-flip controls")
    print(f" {total_trades:,} simulated 0DTE trades in {report['runtime_s']}s")
    print(f" passed TRAIN (t>={TRAIN_T}):  real {real_rate:.1%}   coin-flip {ctrl_rate:.1%}")
    print(f" survived TEST too:          real {real_surv:.2%}   coin-flip {ctrl_surv_rate:.2%}")
    for r in survivors[:5]:
        v, s1, s2, _ = r
        print(f"  * {v.label()}\n      train {s1['n']} trades PF {s1['pf']:.2f} | test {s2['n']} trades "
              f"PF {s2['pf']:.2f}, win {s2['win']:.0%}, avg ${s2['avg']:+.2f}/contract")
    print(f"\n VERDICT: {verdict}\n{line}\n files: {a.out}/report.json, {a.out}/all_variants.csv")


def _by_signal(results):
    out = {}
    for v, s1, s2, _ in results:
        o = out.setdefault(v.signal, {"variants": 0, "train_pos": 0, "test_pos": 0, "best_train_t": -99})
        o["variants"] += 1
        o["train_pos"] += s1["net"] > 0
        o["test_pos"] += s2["net"] > 0
        o["best_train_t"] = max(o["best_train_t"], round(s1["t"], 2))
    return out


if __name__ == "__main__":
    main()
