"""Run the desk on LIVE market data.

Two modes:
  python live.py                 # SHADOW: live prices, simulated fills. No account needed.
  python live.py --alpaca-paper  # orders go to your Alpaca PAPER account (fake money, real fills)

Leave it running (laptop, or a $5/mo cloud box). Ctrl+C to stop.
Create a file named STOP in this folder and the desk flattens and quits (kill switch).
Everything is written to journal.jsonl and state.json.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

from desk.broker import AlpacaBroker, PaperBroker
from desk.config import crypto_config, stocks_config
from desk.data import aggregate, alpaca_bars
from desk.desk import Desk
from desk.stats import scoreboard


def write_state(desk, path="state.json"):
    state = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "equity": round(desk.equity(), 4),
        "brake": desk.nova.desk_closed,
        "symbols": {s: {"price": m.last.close if m.last else None,
                        "vwap": m.vwap.value, "ema50": m.ema_fast.value,
                        "charge": desk.charge.get(s), "thesis": desk.last_thesis.get(s),
                        "position": vars(desk.positions[s]) if s in desk.positions else None,
                        "day_pnl": desk.day_pnl_by_symbol.get(s)}
                    for s, m in desk.markets.items()},
        "recent_events": desk.events[-40:],
        "scoreboard": scoreboard(desk.trades, desk.equity_curve, desk.cfg.starting_cash,
                                 desk.broker.fees_paid),
    }
    with open(path, "w") as f:
        json.dump(state, f, default=str, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", action="store_true")
    ap.add_argument("--alpaca-paper", action="store_true", help="send orders to Alpaca paper")
    ap.add_argument("--cash", type=float, default=100.0)
    a = ap.parse_args()

    cfg = stocks_config(starting_cash=a.cash) if a.stocks else crypto_config(starting_cash=a.cash)
    broker = (AlpacaBroker(cfg.starting_cash, cfg.fee_pct, cfg.slippage_pct) if a.alpaca_paper
              else PaperBroker(cfg.starting_cash, cfg.fee_pct, cfg.slippage_pct))
    desk = Desk(cfg, broker, journal_path="journal.jsonl")
    mode = "ALPACA PAPER" if a.alpaca_paper else "SHADOW (simulated fills)"
    print(f"Desk online - {mode} - {cfg.symbols} on {cfg.timeframe_min}-min bars, bankroll ${cfg.starting_cash}")

    # warm up indicators on recent history (no trading on history)
    hist = alpaca_bars(cfg.symbols, datetime.now(timezone.utc) - timedelta(days=4))
    seen = {}
    real_broker, desk.broker = desk.broker, PaperBroker(cfg.starting_cash, 0, 0)
    for s, bars in hist.items():
        for b in aggregate(bars, cfg.timeframe_min)[:-1]:  # last one may still be forming
            desk.markets[s].update(b)
            seen[s] = b.ts
    desk.broker = real_broker
    print("Warm-up done. Watching for closed bars...")

    news = json.load(open("news.json")) if os.path.exists("news.json") else None
    while True:
        if os.path.exists("STOP"):
            desk.close_all(datetime.now(timezone.utc), "kill switch")
            write_state(desk)
            print("Kill switch: flattened and stopped.")
            return
        if news is not None:
            desk.vega.headlines = news
        try:
            recent = alpaca_bars(cfg.symbols, datetime.now(timezone.utc) - timedelta(hours=3))
        except Exception as e:  # network hiccup: wait and retry, never crash mid-trade
            print("data error, retrying:", e)
            time.sleep(30)
            continue
        for s, bars in recent.items():
            closed = aggregate(bars, cfg.timeframe_min)[:-1]
            for b in closed:
                if s not in seen or b.ts > seen[s]:
                    desk.on_bar(s, b)
                    seen[s] = b.ts
                    for e in desk.events[-5:]:
                        if e["i"] == desk.i:
                            print(f"{e['t'][5:16]} {e['sym']:8} {e['agent']:5} {e['msg']}")
        desk.flush_pending(datetime.now(timezone.utc))
        write_state(desk)
        time.sleep(30)


if __name__ == "__main__":
    main()
