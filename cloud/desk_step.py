"""Cloud tick for the strategy desks.

  python cloud/desk_step.py turtle      # every hour: acts on newly closed 4h bars
  python cloud/desk_step.py reversion   # ~3:50pm ET on market days
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk.agents import Bar  # noqa: E402
from desk.data import NY, aggregate, alpaca_bars  # noqa: E402
from desks import reversion, turtle  # noqa: E402
from desks.common import ROOT, Book, alpaca_executor, write_desk_json  # noqa: E402


def now():
    fake = os.environ.get("DESK_NOW")
    return datetime.fromisoformat(fake) if fake else datetime.now(timezone.utc)


def run_turtle():
    cfg = turtle.CFG
    path = os.path.join(ROOT, "state", "turtle", "book.json")
    book = Book.load(path, "turtle", cfg["cash"], cfg["fee_pct"], cfg["slip_pct"])
    seen = book.__dict__.setdefault("seen", {})
    ex = alpaca_executor()
    raw = alpaca_bars(cfg["symbols"], now() - timedelta(days=30), timeframe="1Hour")
    view = {}
    for s, bars in raw.items():
        bars = aggregate(bars, cfg["timeframe_min"])
        if bars and bars[-1].ts + timedelta(minutes=cfg["timeframe_min"]) > now():
            bars = bars[:-1]  # still forming
        if not bars:
            continue
        if s not in seen:  # first run: learn the history, don't trade it
            seen[s] = str(bars[-1].ts)
            book.last_px[s] = bars[-1].close
            book.log(bars[-1].ts, s, "online", f"{s} desk online, watching for a 20-bar breakout")
        for i, b in enumerate(bars):
            if str(b.ts) > seen[s]:
                turtle.on_bar(book, s, bars[max(0, i - 400):i + 1], cfg, ex)
                seen[s] = str(b.ts)
        lv = turtle.levels(bars, cfg) or {}
        tail = []
        for i in range(max(cfg["entry_n"] + 1, len(bars) - 90), len(bars)):
            prior = bars[i - cfg["entry_n"]:i]
            tail.append([str(bars[i].ts), round(bars[i].close, 6), round(max(x.high for x in prior), 6),
                         round(min(x.low for x in bars[i - cfg["exit_n"]:i]), 6)])
        p = book.positions.get(s)
        view[s] = {"price": bars[-1].close, "hi20": lv.get("hi"), "lo10": lv.get("lo"), "ema100": lv.get("ema"),
                   "status": "in trade" if p else ("near breakout" if lv.get("hi") and bars[-1].close > lv["hi"] * 0.98 else "watching"),
                   "stop": p["stop"] if p else None, "bars": tail}
    book.equity_log.append([now().isoformat(), round(book.equity(), 4)])
    book.save(path)
    write_desk_json("turtle", book, {k: v for k, v in cfg.items() if k != "symbols"}, {"symbols": view})
    print(f"turtle: equity ${book.equity():.2f}, {len(book.positions)} open, {len(book.trades)} trades")


def run_reversion():
    cfg = reversion.CFG
    path = os.path.join(ROOT, "state", "reversion", "book.json")
    book = Book.load(path, "reversion", cfg["cash"], cfg["fee_pct"], cfg["slip_pct"])
    ny = now().astimezone(NY)
    today = ny.date()
    daily = alpaca_bars(cfg["symbols"], now() - timedelta(days=420), timeframe="1Day")
    last_min = alpaca_bars(cfg["symbols"], now() - timedelta(hours=2))
    view = {}
    trade_today = book.__dict__.get("seen_day") != str(today) and os.environ.get("REVERSION_ACT", "1") == "1"
    traded = False
    ex = alpaca_executor()
    for s in cfg["symbols"]:
        bars = [b for b in aggregate(daily.get(s, []), 1440) if b.ts.date() < today]
        mins = [b for b in last_min.get(s, []) if b.ts.astimezone(NY).date() == today]
        live = bool(mins)
        if live:  # today's bar so far, closing at the latest minute price
            bars = bars + [Bar(mins[-1].ts, mins[0].open, max(b.high for b in mins), min(b.low for b in mins),
                               mins[-1].close, sum(b.volume for b in mins))]
        if not bars:
            continue
        if live and trade_today:
            reversion.on_bar(book, s, bars[-260:], cfg, ex)
            traded = True
        else:
            book.last_px[s] = bars[-1].close
        lv = reversion.levels(bars, cfg) or {}
        p = book.positions.get(s)
        closes = [b.close for b in bars]
        view[s] = {"price": bars[-1].close, "rsi2": lv.get("rsi"), "sma200": lv.get("sma200"), "sma5": lv.get("sma5"),
                   "status": "holding" if p else ("dip zone" if lv.get("rsi") is not None and lv["rsi"] < 25 else "watching"),
                   "days_held": p.get("days") if p else None,
                   "bars": [[str(b.ts.date()), round(b.close, 4)] for b in bars[-120:]],
                   "trend_ok": bool(lv.get("sma200") and closes[-1] > lv["sma200"])}
    if traded:
        book.seen_day = str(today)
    book.equity_log.append([now().isoformat(), round(book.equity(), 4)])
    book.save(path)
    write_desk_json("reversion", book, {k: v for k, v in cfg.items() if k != "symbols"}, {"symbols": view})
    print(f"reversion: {'acted' if traded else 'watched'}; equity ${book.equity():.2f}, {len(book.positions)} open")


if __name__ == "__main__":
    {"turtle": run_turtle, "reversion": run_reversion}[sys.argv[1]]()
