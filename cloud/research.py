"""Nightly research: backtest the strategy desks on REAL history and publish the results.

  Turtle    : ~2 years of 4-hour crypto bars
  Reversion : ~10 years of daily SPY / QQQ / IWM bars
  Crypto    : ~1 year of the 6-agent desk on its live settings
Writes docs/research.json (the Research room monitors in the office read it).
Each desk is also split in time: first 60% vs last 40%, so you can see if it held up.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk.data import aggregate, alpaca_bars  # noqa: E402
from desks import reversion, turtle  # noqa: E402
from desks.common import ROOT, stats  # noqa: E402


def split_eval(mod, bars, cfg):
    full = mod.backtest(bars, cfg)
    ts = sorted({b.ts for v in bars.values() for b in v})
    cut = ts[int(len(ts) * 0.6)] if ts else None
    warm = timedelta(days=60) if cfg.get("timeframe_min") else timedelta(days=320)
    first = mod.backtest({s: [b for b in v if b.ts < cut] for s, v in bars.items()}, cfg)
    last = mod.backtest({s: [b for b in v if b.ts >= cut - warm] for s, v in bars.items()}, cfg)
    eq = full.equity_log
    return {"full": stats(full), "first_60": stats(first), "last_40": stats(last),
            "range": [str(ts[0])[:10], str(ts[-1])[:10]] if ts else None,
            "equity_curve": eq[::max(1, len(eq) // 300)], "recent_trades": full.trades[-15:]}


class CryptoDeskShim:
    """Lets the 6-agent crypto desk go through the same split test as the other desks."""
    @staticmethod
    def backtest(bars, cfg_dict):
        from backtest import run
        from cloud.crypto_step import load_cfg
        from desks.common import Book
        cfg = load_cfg()
        desk, _, _ = run(cfg, bars, quiet=True)
        b = Book("crypto", cfg.starting_cash, cfg.fee_pct, cfg.slippage_pct)
        b.trades = [{**t, "symbol": t["symbol"]} for t in desk.trades]
        b.equity_log = [list(e) for e in desk.equity_curve]
        b.cash = desk.equity()
        b.fees_paid = desk.broker.fees_paid
        return b


def buy_hold(bars, cash=100.0):
    rets = []
    for v in bars.values():
        if len(v) > 1:
            rets.append(v[-1].close / v[0].close - 1)
    return round(sum(rets) / len(rets), 4) if rets else None


def main():
    now = datetime.now(timezone.utc)
    out = {"updated": now.isoformat(), "desks": {}}
    try:
        raw = alpaca_bars(turtle.CFG["symbols"], now - timedelta(days=730), timeframe="1Hour")
        bars = {s: aggregate(v, 240) for s, v in raw.items() if v}
        r = split_eval(turtle, bars, turtle.CFG)
        r["buy_hold_return"] = buy_hold(bars)
        out["desks"]["turtle"] = r
        print("turtle", r["full"])
    except Exception as e:
        out["desks"]["turtle"] = {"error": str(e)[:300]}
        print("turtle research failed:", e)
    try:
        try:
            raw = alpaca_bars(reversion.CFG["symbols"], now - timedelta(days=3650), now - timedelta(minutes=20),
                              timeframe="1Day", feed="sip")
        except Exception:  # free plans without SIP history fall back to IEX
            raw = alpaca_bars(reversion.CFG["symbols"], now - timedelta(days=3650), timeframe="1Day", feed="iex")
        bars = {s: aggregate(v, 1440) for s, v in raw.items() if v}
        r = split_eval(reversion, bars, reversion.CFG)
        r["buy_hold_return"] = buy_hold(bars)
        out["desks"]["reversion"] = r
        print("reversion", r["full"])
    except Exception as e:
        out["desks"]["reversion"] = {"error": str(e)[:300]}
        print("reversion research failed:", e)
    try:
        from cloud.crypto_step import load_cfg
        ccfg = load_cfg()
        tf = "1Hour" if ccfg.timeframe_min == 60 else f"{ccfg.timeframe_min}Min"
        raw = alpaca_bars(ccfg.symbols, now - timedelta(days=365), timeframe=tf)
        bars = {s: aggregate(v, ccfg.timeframe_min) for s, v in raw.items() if v}
        r = split_eval(CryptoDeskShim, bars, {"timeframe_min": ccfg.timeframe_min})
        r["buy_hold_return"] = buy_hold(bars)
        out["desks"]["crypto"] = r
        print("crypto desk", r["full"])
    except Exception as e:
        out["desks"]["crypto"] = {"error": str(e)[:300]}
        print("crypto desk research failed:", e)
    lab = os.path.join(ROOT, "lab_out", "report.json")
    if os.path.exists(lab):
        rep = json.load(open(lab))
        out["qqq_lab"] = {k: rep.get(k) for k in ("verdict", "days", "date_range", "trades_simulated", "survivor_rate",
                                                   "control_survivor_rate", "by_signal")}
        out["qqq_lab"]["top"] = (rep.get("survivors") or rep.get("best_overall") or [])[:5]
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "docs", "research.json"), "w"), default=str)


if __name__ == "__main__":
    main()
