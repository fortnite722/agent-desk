"""Shared plumbing for the strategy desks (Turtle, Reversion, ...).

Each desk is a pure strategy function:   decide(book, sym, bars, cfg) -> list of actions
The SAME function runs in the backtest and live, so what you research is what trades.
A Book is the desk's own bankroll (JSON on disk), separate from every other desk.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")


# ---------------------------------------------------------------- indicators
def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def ema(vals, n):
    if len(vals) < n:
        return None
    k, e = 2 / (n + 1), sum(vals[:n]) / n
    for v in vals[n:]:
        e += k * (v - e)
    return e


def atr(bars, n=20):
    if len(bars) < n + 1:
        return None
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars[-n-1:-1], bars[-n:])]
    return sum(trs) / n


def rsi(closes, n=2):
    if len(closes) < n + 20:
        return None
    gains = losses = 0.0
    # Wilder smoothing over a longer tail for stability
    seed = closes[-(n + 20):]
    ag = al = None
    for a, b in zip(seed[:-1], seed[1:]):
        ch = b - a
        g, l = max(ch, 0), max(-ch, 0)
        if ag is None:
            ag, al = g, l
        else:
            ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


# ---------------------------------------------------------------- the book
class Book:
    def __init__(self, name, cash, fee_pct, slip_pct):
        self.name, self.cash, self.fee, self.slip = name, cash, fee_pct, slip_pct
        self.start_cash = cash
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.events: list[dict] = []
        self.equity_log: list[list] = []
        self.last_px: dict[str, float] = {}
        self.fees_paid = 0.0

    # ---- persistence
    def to_dict(self):
        return dict(vars(self))

    @classmethod
    def from_dict(cls, d):
        b = cls(d["name"], d["cash"], d["fee"], d["slip"])
        b.__dict__.update(d)
        return b

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.events = self.events[-300:]
        self.equity_log = self.equity_log[-3000:]
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, default=str)

    @classmethod
    def load(cls, path, name, cash, fee, slip):
        if os.path.exists(path):
            return cls.from_dict(json.load(open(path)))
        return cls(name, cash, fee, slip)

    # ---- money
    def equity(self):
        return self.cash + sum(p["qty"] * self.last_px.get(s, p["entry"]) for s, p in self.positions.items())

    def log(self, t, sym, kind, msg, **data):
        self.events.append({"t": str(t), "sym": sym, "kind": kind, "msg": msg, **data})

    def buy(self, t, sym, qty, px, plan, why, executor=None):
        fill = px * (1 + self.slip)
        if executor:
            got = executor(sym, "buy", qty, fill)
            if got is None:
                self.log(t, sym, "miss", f"order not filled for {sym}, skipped")
                return False
            qty, fill = got
        cost = qty * fill
        fee = cost * self.fee
        self.cash -= cost + fee
        self.fees_paid += fee
        self.positions[sym] = {"qty": qty, "entry": fill, "t": str(t), "fees": fee, **plan}
        self.log(t, sym, "entry", f"BUY {sym} {qty:.6g} @ {fill:.4g} (${cost:.2f}) - {why}", price=fill)
        return True

    def sell(self, t, sym, px, why, executor=None):
        p = self.positions.get(sym)
        if not p:
            return
        fill = px * (1 - self.slip)
        qty = p["qty"]
        if executor:
            got = executor(sym, "sell", qty, fill)
            if got is not None:
                qty, fill = got
        proceeds = qty * fill
        fee = proceeds * self.fee
        self.cash += proceeds - fee
        self.fees_paid += fee
        pnl = proceeds - fee - (p["qty"] * p["entry"] + p.get("fees", 0))
        del self.positions[sym]
        tr = {"symbol": sym, "entry_t": p["t"], "exit_t": str(t), "entry": p["entry"], "exit": fill,
              "qty": p["qty"], "pnl": round(pnl, 4), "ret": round(pnl / (p["qty"] * p["entry"]), 4), "why": why}
        self.trades.append(tr)
        self.log(t, sym, "exit", f"{'PROFIT' if pnl > 0 else 'LOSS'} {pnl:+.2f} on {sym} - {why}", pnl=pnl)


# ---------------------------------------------------------------- stats
def stats(book: Book):
    pnls = [t["pnl"] for t in book.trades]
    eq = [e for _, e in book.equity_log] or [book.start_cash]
    peak, dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak if peak else 0)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p <= 0)
    return {"trades": len(pnls), "wins": len(wins), "win_rate": round(len(wins) / len(pnls), 3) if pnls else 0,
            "net": round(sum(pnls), 2), "return_pct": round((book.equity() - book.start_cash) / book.start_cash, 4),
            "profit_factor": round(sum(wins) / gl, 2) if gl else (99.0 if wins else 0.0),
            "max_drawdown": round(dd, 4), "fees": round(book.fees_paid, 2), "equity": round(book.equity(), 2)}


# ---------------------------------------------------------------- live orders
def alpaca_executor():
    """Returns a function that sends market orders to Alpaca PAPER, or None without keys."""
    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key:
        return None

    def req(method, path, body=None):
        r = urllib.request.Request(PAPER + path, method=method, data=json.dumps(body).encode() if body else None,
                                   headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec,
                                            "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}

    def execute(sym, side, qty, ref_px):
        crypto = "/" in sym
        if side == "sell" and crypto:
            try:
                qty = min(qty, float(req("GET", f"/v2/positions/{sym.replace('/', '')}")["qty"]))
            except Exception:
                pass
        if crypto:
            order = {"symbol": sym, "qty": f"{qty:.8f}", "side": side, "type": "market", "time_in_force": "gtc"}
        elif side == "buy":
            order = {"symbol": sym, "notional": f"{qty * ref_px:.2f}", "side": side, "type": "market", "time_in_force": "day"}
        else:
            order = {"symbol": sym, "qty": f"{qty:.6f}", "side": side, "type": "market", "time_in_force": "day"}
        try:
            o = req("POST", "/v2/orders", order)
        except urllib.error.HTTPError as e:
            print("order rejected:", e.code, e.read()[:200])
            return None
        for _ in range(20):
            if o.get("status") == "filled":
                break
            time.sleep(1)
            o = req("GET", f"/v2/orders/{o['id']}")
        if o.get("status") != "filled":
            return None
        return float(o["filled_qty"]), float(o["filled_avg_price"])

    return execute


def write_desk_json(name, book: Book, cfg: dict, extra: dict):
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    eq = book.equity_log
    out = {"desk": name, "updated": datetime.now(timezone.utc).isoformat(),
           "mode": "alpaca-paper" if os.environ.get("APCA_API_KEY_ID") else "shadow",
           "equity": round(book.equity(), 4), "start_cash": book.start_cash, "cash": round(book.cash, 4),
           "positions": book.positions, "trades": book.trades[-60:], "events": book.events[-50:],
           "equity_curve": eq[::max(1, len(eq) // 400)], "stats": stats(book), "config": cfg, **extra}
    with open(os.path.join(ROOT, "docs", f"{name}.json"), "w") as f:
        json.dump(out, f, default=str)


def fmt_t(t):
    return t.isoformat() if hasattr(t, "isoformat") else str(t)


def isfinite(x):
    return x is not None and math.isfinite(x)
