"""TURTLE DESK: trend-following breakouts on crypto + gold (PAXG) (4-hour bars).

The classic Turtle / Donchian rules, a family of trend-following systems with
a long public track record across markets:
  ENTER  when a 4h bar closes above the highest high of the last 20 bars,
         and price is above its 100-bar EMA (only trade with the trend)
  STOP   2 x ATR below entry, then trail 3 x ATR below the highest close
  EXIT   on the stop, or a close below the lowest low of the last 10 bars
  SIZE   risk 1% of the desk's bankroll per trade; max 3 coins at once
Long only (crypto can't be shorted on Alpaca). Few trades, big winners, many small losers.
"""
from __future__ import annotations

from .common import Book, atr, ema

CFG = {"timeframe_min": 240, "entry_n": 20, "exit_n": 10, "ema_n": 100, "stop_atr": 2.0, "trail_atr": 3.0,
       "atr_n": 20, "risk_pct": 0.01, "max_positions": 3, "max_pos_pct": 0.40, "fee_pct": 0.0025,
       "slip_pct": 0.0005, "cash": 100.0,
       "symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD", "LINK/USD", "PAXG/USD"]}


def levels(hist, cfg=CFG):
    if len(hist) < max(cfg["entry_n"], cfg["ema_n"]) + 2:
        return None
    prior = hist[:-1]
    return {"hi": max(b.high for b in prior[-cfg["entry_n"]:]),
            "lo": min(b.low for b in prior[-cfg["exit_n"]:]),
            "ema": ema([b.close for b in hist[-(cfg["ema_n"] * 3):]], cfg["ema_n"]),
            "atr": atr(hist, cfg["atr_n"])}


def on_bar(book: Book, sym, hist, cfg=CFG, executor=None):
    b = hist[-1]
    book.last_px[sym] = b.close
    lv = levels(hist, cfg)
    p = book.positions.get(sym)
    if p:
        if b.low <= p["stop"]:
            book.sell(b.ts, sym, min(b.open, p["stop"]), "trailing stop" if p["stop"] > p["entry"] else "stop", executor)
            return
        if lv:
            p["high"] = max(p["high"], b.close)
            p["stop"] = max(p["stop"], p["high"] - cfg["trail_atr"] * lv["atr"])
            if b.close < lv["lo"]:
                book.sell(b.ts, sym, b.close, f"closed below {cfg['exit_n']}-bar low", executor)
        return
    if not lv or not lv["atr"]:
        return
    if b.close > lv["hi"] and b.close > lv["ema"]:
        if cfg.get("block"):
            book.log(b.ts, sym, "veto", f"{sym} broke out, but {cfg['block']}")
            return
        if len(book.positions) >= cfg["max_positions"]:
            book.log(b.ts, sym, "veto", f"{sym} broke out but {cfg['max_positions']} positions already open")
            return
        eq = book.equity()
        risk_unit = cfg["stop_atr"] * lv["atr"]
        qty = eq * cfg["risk_pct"] * cfg.get("size_mult", 1.0) / risk_unit
        cap = min(eq * cfg["max_pos_pct"], book.cash * 0.98)
        if qty * b.close > cap:
            qty = cap / b.close
        if qty * b.close < 1.0:
            book.log(b.ts, sym, "veto", f"{sym} breakout, but size under $1")
            return
        book.buy(b.ts, sym, qty, b.close, {"stop": b.close - risk_unit, "high": b.close},
                 f"broke the 20-bar high {lv['hi']:.4g}, above EMA100", executor)


def backtest(bars_by_sym, cfg=CFG, cash=None):
    book = Book("turtle", cash or cfg["cash"], cfg["fee_pct"], cfg["slip_pct"])
    rows = sorted(((b.ts, s, i) for s, bars in bars_by_sym.items() for i, b in enumerate(bars)), key=lambda r: (r[0], r[1]))
    last_t = None
    for t, s, i in rows:
        bars = bars_by_sym[s]
        on_bar(book, s, bars[max(0, i - 400):i + 1], cfg)
        if t != last_t:
            book.equity_log.append([str(t), round(book.equity(), 4)])
            last_t = t
    for s in list(book.positions):
        book.sell(last_t, s, book.last_px[s], "backtest end")
    book.equity_log.append([str(last_t), round(book.equity(), 4)])
    return book
