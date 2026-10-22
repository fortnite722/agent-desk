"""REVERSION DESK: the RSI(2) pullback on SPY / QQQ / IWM (daily bars).

Larry Connors' widely published short-term mean-reversion rules for index ETFs:
  ENTER  near the close when RSI(2) < 10 (a sharp 1-3 day dip)
         and price is above its 200-day average (only buy dips in uptrends)
  EXIT   when price closes above its 5-day average, or after 10 days,
         or on a -10% safety stop
  SIZE   a third of the desk's bankroll per ETF, fractional shares
Trades at ~3:50pm ET. Long only. Wins often, holds a few days.
"""
from __future__ import annotations

from .common import Book, rsi, sma

CFG = {"rsi_n": 2, "rsi_entry": 10, "trend_n": 200, "exit_n": 5, "max_days": 10, "stop_pct": 0.10,
       "alloc": 1 / 3, "fee_pct": 0.0, "slip_pct": 0.0005, "cash": 100.0, "symbols": ["SPY", "QQQ", "IWM"]}


def levels(hist, cfg=CFG):
    closes = [b.close for b in hist]
    if len(closes) < cfg["trend_n"] + 1:
        return None
    return {"rsi": rsi(closes, cfg["rsi_n"]), "sma200": sma(closes, cfg["trend_n"]), "sma5": sma(closes, cfg["exit_n"])}


def on_bar(book: Book, sym, hist, cfg=CFG, executor=None):
    b = hist[-1]
    book.last_px[sym] = b.close
    lv = levels(hist, cfg)
    p = book.positions.get(sym)
    if p:
        p["days"] = p.get("days", 0) + 1
        if b.close <= p["entry"] * (1 - cfg["stop_pct"]):
            book.sell(b.ts, sym, b.close, "-10% safety stop", executor)
        elif lv and b.close > lv["sma5"]:
            book.sell(b.ts, sym, b.close, "closed above 5-day average", executor)
        elif p["days"] >= cfg["max_days"]:
            book.sell(b.ts, sym, b.close, f"{cfg['max_days']}-day time stop", executor)
        return
    if not lv or lv["rsi"] is None:
        return
    if lv["rsi"] < cfg["rsi_entry"] and b.close > lv["sma200"]:
        if cfg.get("block"):
            book.log(b.ts, sym, "veto", f"{sym} RSI(2) {lv['rsi']:.1f} dip, but {cfg['block']}")
            return
        budget = min(book.equity() * cfg["alloc"] * cfg.get("size_mult", 1.0), book.cash * 0.99)
        if budget < 1.0:
            return
        book.buy(b.ts, sym, budget / b.close, b.close, {"days": 0},
                 f"RSI(2) {lv['rsi']:.1f} dip in an uptrend (above 200-day avg)", executor)


def backtest(bars_by_sym, cfg=CFG, cash=None):
    book = Book("reversion", cash or cfg["cash"], cfg["fee_pct"], cfg["slip_pct"])
    rows = sorted(((b.ts, s, i) for s, bars in bars_by_sym.items() for i, b in enumerate(bars)), key=lambda r: (r[0], r[1]))
    last_t = None
    for t, s, i in rows:
        on_bar(book, s, bars_by_sym[s][max(0, i - 260):i + 1], cfg)
        if t != last_t:
            book.equity_log.append([str(t), round(book.equity(), 4)])
            last_t = t
    for s in list(book.positions):
        book.sell(last_t, s, book.last_px[s], "backtest end")
    book.equity_log.append([str(last_t), round(book.equity(), 4)])
    return book
