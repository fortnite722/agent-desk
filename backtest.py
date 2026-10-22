"""Run the desk on historical or synthetic bars and print the honest scoreboard.

Examples
  python backtest.py                         # synthetic crypto practice data
  python backtest.py --alpaca --days 5       # real Alpaca crypto bars, last 5 days
  python backtest.py --stocks --alpaca       # SPY/QQQ/IWM (needs paper keys)
  python backtest.py --replay replay.json    # also export a replay for the dashboard
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from desk.broker import PaperBroker
from desk.config import crypto_config, stocks_config
from desk.data import aggregate, alpaca_bars, merge_by_time, synthetic
from desk.desk import Desk
from desk.stats import monte_carlo, print_report, scoreboard


def run(cfg, bars_by_symbol, headlines=None, journal=None, replay_path=None, quiet=False):
    broker = PaperBroker(cfg.starting_cash, cfg.fee_pct, cfg.slippage_pct)
    desk = Desk(cfg, broker, headlines, journal)
    stream = merge_by_time(bars_by_symbol)
    frames = []
    last_ts = None
    for sym, bar in stream:
        desk.on_bar(sym, bar)
        if replay_path and bar.ts != last_ts:
            last_ts = bar.ts
        if replay_path and sym == cfg.symbols[-1]:
            # one frame per timestamp, after every symbol has seen it
            frames.append({
                "t": bar.ts.strftime("%m/%d %H:%M"),
                "i": desk.i,
                "eq": round(desk.equity(), 3),
                "brake": desk.nova.desk_closed,
                "s": {s: [round(m.last.close, 4) if m.last else None,
                          round(m.vwap.value, 4) if m.vwap.value else None,
                          round(m.ema_fast.value, 4) if m.ema_fast.value else None,
                          desk.charge.get(s, 0),
                          (desk.positions[s].side if s in desk.positions else
                           ("charging" if desk.charge.get(s, 0) >= 70 else "watching")),
                          round(desk.day_pnl_by_symbol.get(s, 0), 3),
                          (round(desk.positions[s].plan["stop"], 4) if s in desk.positions else None)]
                      for s, m in desk.markets.items()},
            })
    if stream:
        desk.close_all(stream[-1][1].ts, "backtest end")
    sb = scoreboard(desk.trades, desk.equity_curve, cfg.starting_cash, broker.fees_paid)
    mc = monte_carlo(desk.trades, cfg.starting_cash, n_trades=max(20, len(desk.trades)))
    if not quiet:
        print_report(sb, mc)
    if replay_path:
        with open(replay_path, "w") as f:
            json.dump({"symbols": cfg.symbols, "start_cash": cfg.starting_cash,
                       "config": {k: v for k, v in vars(cfg).items() if k != "symbols"},
                       "frames": frames, "events": desk.events, "trades": desk.trades,
                       "scoreboard": sb, "monte_carlo": mc}, f, default=str)
    return desk, sb, mc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", action="store_true", help="SPY/QQQ/IWM instead of crypto")
    ap.add_argument("--alpaca", action="store_true", help="use real Alpaca bars")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cash", type=float, default=100.0)
    ap.add_argument("--news", help="news.json: {symbol: [headline, ...]}")
    ap.add_argument("--replay", help="write a replay JSON for the dashboard")
    ap.add_argument("--journal", default="journal.jsonl")
    a = ap.parse_args()

    cfg = stocks_config(starting_cash=a.cash) if a.stocks else crypto_config(starting_cash=a.cash)
    if a.alpaca:
        start = datetime.now(timezone.utc) - timedelta(days=a.days)
        bars = alpaca_bars(cfg.symbols, start)
    else:
        bars = synthetic(cfg.symbols, days=a.days, seed=a.seed, crypto=not a.stocks)
        print("NOTE: synthetic random data. Any profit here is luck, not edge.")
    bars = {s: aggregate(b, cfg.timeframe_min) for s, b in bars.items()}
    print(f"{len(cfg.symbols)} symbols x {len(next(iter(bars.values())))} bars of {cfg.timeframe_min}-min")
    news = json.load(open(a.news)) if a.news else None
    run(cfg, bars, news, a.journal, a.replay)


if __name__ == "__main__":
    main()
