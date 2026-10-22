"""QQQ 0DTE options bot on Alpaca PAPER.

  python options_paper.py            # runs the trading day, then stops
  python options_paper.py --rank 2   # use survivor #2 from the lab

Setup choice: the lab's top survivor. If the lab hasn't found one yet, it paper-trades
the best TRAIN candidate (flagged "experimental"), so you always get paper data.
Rules: 1 contract, signal on a 1-min close, buy right away, exit on premium
stop / target / time stop / 3:29pm, max 3 trades a day, done after 2 losses.
Kill switch: a file named STOP.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from lab.engine import COOLDOWN, LAST_MINUTE, MAX_PER_DAY, Variant
from lab.market import NY, build_days
from lab.options import OptionCosts, occ_symbol
from lab.signals import SIGNALS, WINDOWS

PAPER = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
ROOT = os.path.dirname(os.path.abspath(__file__))
SD = os.path.join(ROOT, "state", "options")
DOCS = os.path.join(ROOT, "docs")
MAX_LOSSES_PER_DAY = 2
# cloud jobs stop at ~15:20 ET, so the cloud flattens earlier (LAST_MINUTE=340 -> 3:10pm)
LAST = int(os.environ.get("LAST_MINUTE", LAST_MINUTE))
FALLBACK = {"signal": "orb15", "window": "morning", "trend": True, "stop": 0.4,
            "target": 0.6, "hold": 45, "otm": 0}


def req(base, method, path, body=None, q=None):
    url = base + path + ("?" + urllib.parse.urlencode(q) if q else "")
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
        "APCA-API-KEY-ID": os.environ.get("APCA_API_KEY_ID", ""),
        "APCA-API-SECRET-KEY": os.environ.get("APCA_API_SECRET_KEY", ""), "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def qqq_bars(start_utc, end_utc=None):
    q = {"symbols": "QQQ", "timeframe": "1Min", "start": start_utc.isoformat(), "limit": 10000, "feed": "iex"}
    if end_utc:
        q["end"] = end_utc.isoformat()
    js = req(DATA, "GET", "/v2/stocks/bars", q=q)
    return [(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY), b["o"], b["h"], b["l"], b["c"], b["v"])
            for b in (js.get("bars") or {}).get("QQQ", [])]


def option_quote(sym):
    qt = req(DATA, "GET", "/v1beta1/options/quotes/latest", q={"symbols": sym})["quotes"][sym]
    bid, ask = float(qt["bp"]), float(qt["ap"])
    return (bid + ask) / 2, bid, ask


def place(sym, side, limit):
    o = req(PAPER, "POST", "/v2/orders", {"symbol": sym, "qty": "1", "side": side, "type": "limit",
                                          "limit_price": f"{max(0.01, limit):.2f}", "time_in_force": "day"})
    for _ in range(15):
        if o.get("status") == "filled":
            return float(o["filled_avg_price"])
        time.sleep(2)
        o = req(PAPER, "GET", f"/v2/orders/{o['id']}")
    req(PAPER, "DELETE", f"/v2/orders/{o['id']}")
    return None


def pick_setup(rank=1):
    p = os.path.join(ROOT, "lab_out", "report.json")
    if os.path.exists(p):
        rep = json.load(open(p))
        if len(rep.get("survivors", [])) >= rank:
            return Variant(**rep["survivors"][rank - 1]["variant"]), "survivor"
        if rep.get("candidates"):
            return Variant(**rep["candidates"][0]["variant"]), "experimental"
        if rep.get("best_overall"):  # nothing proven: paper-trade the closest thing to an edge, to collect live data
            return Variant(**rep["best_overall"][0]["variant"]), "experimental (lab found no proven edge)"
    return Variant(**FALLBACK), "experimental (no lab run yet)"


class Bot:
    def __init__(self, v: Variant, status: str):
        self.v, self.status = v, status
        self.costs = OptionCosts()
        self.pos = None
        self.today = None
        self.trades_today = self.losses_today = 0
        self.last_exit_min = -99
        self.seen = set()
        self.warm = []
        self.events = []
        os.makedirs(SD, exist_ok=True)
        os.makedirs(DOCS, exist_ok=True)

    def log(self, now, kind, msg, **data):
        ev = {"t": now.isoformat(), "kind": kind, "msg": msg, **data}
        self.events.append(ev)
        print(now.strftime("%H:%M"), kind, msg)
        with open(os.path.join(SD, "journal.jsonl"), "a") as f:
            f.write(json.dumps(ev) + "\n")

    def record_trade(self, row):
        p = os.path.join(SD, "trades.csv")
        new = not os.path.exists(p)
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    def tick(self, now):
        """One pass. Returns seconds to sleep before the next pass (None = done for the day)."""
        if now.date() != self.today:
            self.today, self.trades_today, self.losses_today, self.last_exit_min = now.date(), 0, 0, -99
            self.warm = [r for r in qqq_bars((now - timedelta(days=6)).astimezone(timezone.utc),
                                             now.replace(hour=4, minute=0).astimezone(timezone.utc))]
            self.log(now, "day", f"Session {now.date()}: trading {self.v.label()} [{self.status}]")
        minute = (now.hour - 9) * 60 + now.minute - 30
        if os.path.exists(os.path.join(ROOT, "STOP")):
            if self.pos:
                self.exit(now, minute, "kill switch")
            return None
        if now.weekday() >= 5 or minute > LAST + 2:
            if self.pos:
                self.exit(now, minute, "cutoff")
            return None
        if minute < 0:
            return 30

        if self.pos:
            mid, bid, ask = option_quote(self.pos["sym"])
            self.pos["mark"] = mid
            held = minute - self.pos["minute"]
            v = self.v
            why = ("stop" if mid <= self.pos["entry"] * (1 - v.stop) else
                   "target" if mid >= self.pos["entry"] * (1 + v.target) else
                   "time stop" if held >= v.hold else
                   "end-of-day cutoff" if minute >= LAST else None)
            if why:
                self.exit(now, minute, why, bid)
            return 15

        lo_m, hi_m = WINDOWS[self.v.window]
        if self.trades_today >= MAX_PER_DAY or self.losses_today >= MAX_LOSSES_PER_DAY or not (lo_m <= minute <= min(hi_m, LAST - 15)):
            return 20
        today_rows = qqq_bars(now.replace(hour=4, minute=0).astimezone(timezone.utc), now.astimezone(timezone.utc))
        cut = now.replace(second=0, microsecond=0)
        rows = self.warm + [r for r in today_rows if r[0] < cut]
        days = build_days(rows, min_bars=0)
        if not days or days[-1].date != now.date():
            return 20
        d = days[-1]
        for i, kind in SIGNALS[self.v.signal](d):
            if i != len(d.c) - 1 or (d.date, i) in self.seen or minute <= self.last_exit_min + COOLDOWN:
                continue
            self.seen.add((d.date, i))
            if self.v.trend and ((kind == "C") != (d.c[i] > d.e50[i])):
                self.log(now, "skip", f"{'call' if kind == 'C' else 'put'} signal against the trend, skipped")
                continue
            K = self.costs.strike(d.c[i], kind, self.v.otm)
            sym = occ_symbol("QQQ", d.date, kind, K)
            mid, bid, ask = option_quote(sym)
            px = place(sym, "buy", ask)
            if px:
                self.pos = {"sym": sym, "kind": kind, "K": K, "entry": px, "minute": minute,
                            "t": now.isoformat(), "mark": px, "underlying": d.c[i]}
                self.trades_today += 1
                self.log(now, "entry", f"BUY 1 {sym} @ ${px:.2f} (${px*100:.0f}) on {self.v.signal} "
                         f"{'call' if kind == 'C' else 'put'} signal, QQQ {d.c[i]:.2f}", sym=sym, price=px)
            else:
                self.log(now, "miss", f"no fill on {sym}, skipped")
        return 20

    def exit(self, now, minute, why, bid=None):
        if bid is None:
            _, bid, _ = option_quote(self.pos["sym"])
        px = place(self.pos["sym"], "sell", bid) or place(self.pos["sym"], "sell", bid - 0.02)
        px = px if px is not None else bid
        pnl = (px - self.pos["entry"]) * 100 - 0.08
        self.losses_today += pnl < 0
        self.last_exit_min = minute
        self.log(now, "exit", f"SELL {self.pos['sym']} @ ${px:.2f} ({why}) {'+' if pnl >= 0 else '-'}${abs(pnl):.2f}",
                 sym=self.pos["sym"], price=px, pnl=round(pnl, 2), why=why)
        self.record_trade({"date": str(now.date()), "entry_t": self.pos["t"], "exit_t": now.isoformat(),
                           "symbol": self.pos["sym"], "side": "call" if self.pos["kind"] == "C" else "put",
                           "entry": self.pos["entry"], "exit": px, "pnl": round(pnl, 2), "why": why,
                           "setup": self.v.label(), "status": self.status})
        self.pos = None

    def dashboard(self, now):
        trades = []
        p = os.path.join(SD, "trades.csv")
        if os.path.exists(p):
            trades = list(csv.DictReader(open(p)))
        pnls = [float(t["pnl"]) for t in trades]
        out = {"updated": now.isoformat(), "setup": self.v.label(), "status": self.status,
               "open": self.pos, "today_trades": self.trades_today,
               "events": self.events[-40:], "trades": trades[-60:],
               "total": {"trades": len(pnls), "wins": sum(p > 0 for p in pnls), "net": round(sum(pnls), 2)}}
        with open(os.path.join(DOCS, "options.json"), "w") as f:
            json.dump(out, f, default=str)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, default=1)
    a = ap.parse_args()
    v, status = pick_setup(a.rank)
    import sys as _s
    _s.path.insert(0, ROOT)
    from cloud.council import status as council_status
    st, _, why = council_status("options")
    bot = Bot(v, status)
    if st == "BENCHED":
        now = datetime.now(NY)
        bot.status = "BENCHED by SENTINEL"
        bot.log(now, "benched", f"Benched by the council: {why}. No trades today.")
        bot.dashboard(now)
        print("benched by the council; not trading today")
        return
    last_sync = time.time()
    while True:
        now = datetime.now(NY)
        try:
            wait = bot.tick(now)
        except Exception as e:  # network hiccup: keep the position managed, retry
            print("error:", e)
            wait = 15
        bot.dashboard(now)
        if os.environ.get("CLOUD_SYNC") and time.time() - last_sync > 1200:
            subprocess.run(["bash", os.path.join(ROOT, "cloud", "sync.sh"), "options bot update"], cwd=ROOT)
            last_sync = time.time()
        if wait is None:
            break
        time.sleep(wait)
    bot.dashboard(datetime.now(NY))
    print("done for the day")


if __name__ == "__main__":
    main()
