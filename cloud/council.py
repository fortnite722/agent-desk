"""SENTINEL: the council that decides which strategies may trade.

Runs every night after research. For each strategy it weighs two kinds of evidence:
  1. BACKTEST on real history (docs/research.json): full period AND the most recent 40%
  2. LIVE paper record (docs/<desk>.json): once a desk has 10+ live trades, it counts

Verdicts
  ACTIVE     full size. Proven on history, still working recently, not failing live.
  PROBATION  half size. Roughly break-even or thin evidence; allowed to prove itself small.
  BENCHED    no new trades. Open positions are still managed to their exits.
No proof, no trade. If every strategy is benched, the firm does nothing.
Writes docs/council.json, which every desk reads before it acts.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desks.common import ROOT  # noqa: E402

RULES = {
    "active":    {"full_pf": 1.15, "recent_pf": 1.00, "min_trades": 20, "max_dd": 0.25},
    "probation": {"full_pf": 1.00, "recent_pf": 0.90, "min_trades": 10, "max_dd": 0.35},
    "live_min_trades": 10, "live_bench_pf": 0.80,
}
STRATEGIES = {
    "crypto":    {"name": "Crypto Desk (6 agents)", "research": "crypto", "live": "crypto.json"},
    "turtle":    {"name": "Turtle Desk", "research": "turtle", "live": "turtle.json"},
    "reversion": {"name": "Reversion Desk", "research": "reversion", "live": "reversion.json"},
    "options":   {"name": "QQQ 0DTE Desk", "research": None, "live": "options.json"},
}


def _load(name):
    p = os.path.join(ROOT, "docs", name)
    try:
        return json.load(open(p))
    except Exception:
        return None


def live_record(key, live):
    if not live:
        return None
    if key == "options":
        trades = [float(t["pnl"]) for t in live.get("trades", [])]
    else:
        trades = [float(t["pnl"]) for t in live.get("trades", [])]
    if not trades:
        return {"trades": 0}
    wins = sum(p for p in trades if p > 0)
    losses = -sum(p for p in trades if p <= 0)
    return {"trades": len(trades), "net": round(sum(trades), 2),
            "pf": round(wins / losses, 2) if losses else (99.0 if wins else 0.0)}


def judge_backtest(key, res, live):
    reasons, ev = [], {}
    if not res:
        return "BENCHED", ["no research on real data yet; it runs every weeknight"], ev
    if res.get("error"):
        return "BENCHED", ["research failed: " + res["error"][:120]], ev
    full, recent = res["full"], res["last_40"]
    ev = {"full": full, "recent": recent, "range": res.get("range"), "buy_hold": res.get("buy_hold_return")}
    status = "BENCHED"
    for level in ("active", "probation"):
        r = RULES[level]
        ok = (full["trades"] >= r["min_trades"] and full["profit_factor"] >= r["full_pf"]
              and recent["profit_factor"] >= r["recent_pf"] and full["max_drawdown"] <= r["max_dd"])
        if ok:
            status = level.upper()
            break
    reasons.append(f"history: {full['trades']} trades, PF {full['profit_factor']}, return {full['return_pct']:+.1%}, "
                   f"max drawdown {full['max_drawdown']:.0%}")
    reasons.append(f"recent 40%: PF {recent['profit_factor']}, return {recent['return_pct']:+.1%}")
    if status == "BENCHED":
        if full["trades"] < RULES["probation"]["min_trades"]:
            reasons.append(f"too few trades to trust (need {RULES['probation']['min_trades']}+)")
        elif full["profit_factor"] < RULES["probation"]["full_pf"]:
            reasons.append("loses money over the full history")
        elif recent["profit_factor"] < RULES["probation"]["recent_pf"]:
            reasons.append("stopped working recently")
        else:
            reasons.append("drawdown too deep")
    lv = live_record(key, live)
    ev["live"] = lv
    if lv and lv.get("trades", 0) >= RULES["live_min_trades"] and lv["pf"] < RULES["live_bench_pf"] and status != "BENCHED":
        status = "BENCHED"
        reasons.append(f"failing live: {lv['trades']} paper trades, PF {lv['pf']}")
    return status, reasons, ev


def judge_options(lab, live):
    if not lab:
        return "BENCHED", ["the pattern lab hasn't run on real QQQ data yet"], {}
    ev = {"verdict": lab.get("verdict"), "survivor_rate": lab.get("survivor_rate"),
          "control_rate": lab.get("control_survivor_rate"), "live": live_record("options", live)}
    if lab.get("survivors") and not str(lab.get("verdict", "")).startswith("NO EDGE"):
        top = lab["survivors"][0]
        st = "ACTIVE" if top["test"]["pf"] >= 1.3 else "PROBATION"
        reasons = [f"lab survivor: {top['setup']}", f"unseen-data PF {top['test']['pf']:.2f} over {top['test']['n']} trades"]
        lv = ev["live"]
        if lv and lv.get("trades", 0) >= RULES["live_min_trades"] and lv["pf"] < RULES["live_bench_pf"]:
            return "BENCHED", reasons + [f"failing live: PF {lv['pf']}"], ev
        return st, reasons, ev
    return "BENCHED", ["no 0DTE setup beat the coin-flip control on unseen QQQ data"], ev


def main():
    research = _load("research.json") or {}
    lab = None
    try:
        lab = json.load(open(os.path.join(ROOT, "lab_out", "report.json")))
    except Exception:
        pass
    out = {"updated": datetime.now(timezone.utc).isoformat(), "rules": RULES, "strategies": {}}
    for key, meta in STRATEGIES.items():
        live = _load(meta["live"])
        if key == "options":
            status, reasons, ev = judge_options(lab, live)
        else:
            status, reasons, ev = judge_backtest(key, (research.get("desks") or {}).get(meta["research"]), live)
        out["strategies"][key] = {"name": meta["name"], "status": status,
                                  "size_mult": {"ACTIVE": 1.0, "PROBATION": 0.5, "BENCHED": 0.0}[status],
                                  "reasons": reasons, "evidence": ev}
        print(f"{meta['name']:24} {status:10} {' | '.join(reasons)}")
    active = [k for k, v in out["strategies"].items() if v["status"] != "BENCHED"]
    out["summary"] = (f"{len(active)} of {len(STRATEGIES)} strategies cleared to trade" if active
                      else "Every strategy is benched. The firm is in cash until something proves itself.")
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "docs", "council.json"), "w"), default=str, indent=1)
    print(out["summary"])


def status(key):
    """(status, size_mult, reason) for a desk. Missing council file = BENCHED: no proof, no trade."""
    c = _load("council.json")
    s = (c or {}).get("strategies", {}).get(key)
    if not s:
        return "BENCHED", 0.0, "the council hasn't ruled on this desk yet"
    return s["status"], s["size_mult"], "; ".join(s["reasons"][-1:])


if __name__ == "__main__":
    main()
