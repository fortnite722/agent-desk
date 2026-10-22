"""Weekly self-improve for the crypto desk.

1. Pull the last ~75 days of real bars for the desk's coins.
2. Try 144 settings on the first 50 days (TUNE).
3. Take the best one and test it on the last 25 days (EXAM), which tuning never saw.
4. Promote it only if it beats the current settings on the EXAM. Otherwise keep what we have.
Every decision is logged to state/improve_log.jsonl and docs/improve.json.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run  # noqa: E402
from desk.data import aggregate, alpaca_bars  # noqa: E402
from cloud.crypto_step import CFG_FILE, ROOT, load_cfg  # noqa: E402

GRID = {"stop_atr": [1.0, 1.5, 2.0], "tp1_r": [0.8, 1.0, 1.5], "trail_atr": [0.8, 1.2],
        "min_confidence": [0.45, 0.5, 0.55, 0.6], "min_edge_multiple": [1.5, 2.0]}
TUNE_DAYS, EXAM_DAYS = 50, 25


def tf_string(minutes):
    return "1Hour" if minutes == 60 else f"{minutes}Min"


def slice_bars(bars, start, end):
    return {s: [b for b in v if start <= b.ts < end] for s, v in bars.items()}


def score(cfg, bars):
    desk, sb, _ = run(cfg, bars, quiet=True)
    return sb


def main():
    base = load_cfg()
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=TUNE_DAYS + EXAM_DAYS)
    cut = now - timedelta(days=EXAM_DAYS)
    raw = alpaca_bars(base.symbols, t0, timeframe=tf_string(base.timeframe_min))
    bars = {s: aggregate(v, base.timeframe_min) for s, v in raw.items()}
    # warm-up overlap: the exam run starts 10 days early so indicators are ready on day 1
    tune = slice_bars(bars, t0, cut)
    exam = slice_bars(bars, cut - timedelta(days=10), now)

    current = {k: getattr(base, k) for k in GRID}
    results = []
    for combo in itertools.product(*GRID.values()):
        params = dict(zip(GRID, combo))
        cfg = load_cfg()
        for k, v in params.items():
            setattr(cfg, k, v)
        sb = score(cfg, tune)
        results.append((params, sb))
    ok = [r for r in results if r[1]["trades"] >= 8]
    ok.sort(key=lambda r: (r[1]["net_pnl"], r[1]["profit_factor"]), reverse=True)
    if not ok:
        decision = {"promoted": False, "why": "no setting made 8+ trades in the tune window"}
        best, best_exam = None, None
    else:
        best = ok[0][0]
        cfg_best = load_cfg()
        for k, v in best.items():
            setattr(cfg_best, k, v)
        best_exam = score(cfg_best, exam)
        cur_exam = score(load_cfg(), exam)
        better = best_exam["net_pnl"] > cur_exam["net_pnl"] and best_exam["trades"] >= 4 and best != current
        decision = {"promoted": better,
                    "why": ("new settings beat the current ones on unseen days" if better else
                            "current settings held up as well or better on unseen days; kept them"),
                    "current_exam": cur_exam, "candidate_exam": best_exam}
        if better:
            over = json.load(open(CFG_FILE)) if os.path.exists(CFG_FILE) else {}
            over.update(best)
            over["_last_improve"] = now.isoformat()
            json.dump(over, open(CFG_FILE, "w"), indent=1)

    entry = {"t": now.isoformat(), "current": current, "candidate": best,
             "tune_best": ok[0][1] if ok else None, **decision,
             "tested": len(results)}
    os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
    with open(os.path.join(ROOT, "state", "improve_log.jsonl"), "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    log = [json.loads(line) for line in open(os.path.join(ROOT, "state", "improve_log.jsonl"))][-12:]
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    json.dump({"updated": now.isoformat(), "history": log}, open(os.path.join(ROOT, "docs", "improve.json"), "w"), default=str)
    print(json.dumps({k: entry[k] for k in ("promoted", "why", "candidate", "tested")}, default=str))


if __name__ == "__main__":
    main()
