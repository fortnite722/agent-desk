"""One cloud tick of the crypto desk (GitHub Actions runs this every 15 minutes).

Loads the saved desk, feeds it any newly closed bars, places Alpaca PAPER orders,
then saves everything back: state/crypto/desk.pkl, journal, trades, equity,
and docs/crypto.json for the live Desk City page.
"""
from __future__ import annotations

import csv
import json
import os
import pickle
import sys
from collections import deque
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk.broker import AlpacaBroker, PaperBroker  # noqa: E402
from desk.config import crypto_config  # noqa: E402
from desk.data import aggregate, alpaca_bars  # noqa: E402
from desk.desk import Desk  # noqa: E402
from desk.stats import scoreboard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SD = os.path.join(ROOT, "state", "crypto")
DOCS = os.path.join(ROOT, "docs")
PKL = os.path.join(SD, "desk.pkl")
CFG_FILE = os.path.join(ROOT, "cloud", "config_crypto.json")
HIST = 160


def load_cfg():
    over = json.load(open(CFG_FILE)) if os.path.exists(CFG_FILE) else {}
    over = {k: v for k, v in over.items() if not k.startswith("_")}
    return crypto_config(**over)


def make_broker(cfg, cash):
    if os.environ.get("APCA_API_KEY_ID"):
        return AlpacaBroker(cash, cfg.fee_pct, cfg.slippage_pct), "alpaca-paper"
    return PaperBroker(cash, cfg.fee_pct, cfg.slippage_pct), "shadow"


def now():
    fake = os.environ.get("DESK_NOW")  # tests only
    return datetime.fromisoformat(fake) if fake else datetime.now(timezone.utc)


def main():
    os.makedirs(SD, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)
    cfg = load_cfg()
    from cloud.council import status as council_status
    st, mult, why = council_status("crypto")
    if st == "BENCHED":
        cfg.council_block = f"BENCHED by SENTINEL ({why})"
    else:
        cfg.risk_per_trade_pct *= mult
    print(f"council: crypto desk is {st}")
    fresh = not os.path.exists(PKL)
    if fresh:
        desk = Desk(cfg, PaperBroker(cfg.starting_cash, 0, 0))
        desk.seen, desk.hist, desk.fees_total = {}, {s: deque(maxlen=HIST) for s in cfg.symbols}, 0.0
        warm_days = max(4, cfg.timeframe_min * (cfg.ema_slow + 20) / 1440 + 1)
        hist = alpaca_bars(cfg.symbols, now() - timedelta(days=warm_days))
        for s, bars in hist.items():
            for b in aggregate(bars, cfg.timeframe_min)[:-1]:
                desk.markets[s].update(b)
                desk.seen[s] = b.ts
                desk.hist[s].append(_hrow(desk, s, b))
        cash = cfg.starting_cash
        print(f"fresh desk: warmed up on {sum(len(v) for v in hist.values())} bars")
    else:
        with open(PKL, "rb") as f:
            desk = pickle.load(f)
        cash = desk.cash_saved
        # config changes from the improve loop take effect here (agents read desk.cfg).
        # Symbol / timeframe changes need a fresh desk: delete state/crypto/desk.pkl.
        cfg.symbols, cfg.timeframe_min = desk.cfg.symbols, desk.cfg.timeframe_min
        desk.cfg = cfg
        for m in desk.markets.values():
            m.cfg = cfg
        desk.nova.cfg = cfg

    desk.broker, mode = make_broker(cfg, cash)
    desk.broker.fees_paid = getattr(desk, "fees_total", 0.0)
    n_ev, n_tr = len(desk.events), len(desk.trades)

    recent = alpaca_bars(cfg.symbols, now() - timedelta(hours=6))
    fed = 0
    for s, bars in recent.items():
        closed = aggregate(bars, cfg.timeframe_min)[:-1]
        for b in closed:
            if s not in desk.seen or b.ts > desk.seen[s]:
                desk.on_bar(s, b)
                desk.seen[s] = b.ts
                desk.hist[s].append(_hrow(desk, s, b))
                fed += 1
    desk.flush_pending(now())  # trade now, not 15 minutes from now
    if os.path.exists(os.path.join(ROOT, "STOP")):
        desk.close_all(now(), "kill switch")

    # ---- save
    with open(os.path.join(SD, "journal.jsonl"), "a") as f:
        for e in desk.events[n_ev:]:
            f.write(json.dumps(e, default=str) + "\n")
    new_trades = desk.trades[n_tr:]
    tpath = os.path.join(SD, "trades.csv")
    if new_trades:
        exists = os.path.exists(tpath)
        with open(tpath, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(new_trades[0]))
            if not exists:
                w.writeheader()
            w.writerows(new_trades)
    eq = desk.equity()
    with open(os.path.join(SD, "equity.csv"), "a") as f:
        f.write(f"{now().isoformat()},{eq:.4f}\n")

    desk.events = desk.events[-300:]
    desk.equity_curve = desk.equity_curve[-2000:]
    desk.cash_saved = desk.broker.cash
    desk.fees_total = desk.broker.fees_paid
    broker, desk.broker, desk.journal = desk.broker, None, None
    with open(PKL, "wb") as f:
        pickle.dump(desk, f)
    desk.broker = broker

    write_dashboard(desk, cfg, mode, eq)
    print(f"{mode}: fed {fed} new bars, {len(new_trades)} trades closed, equity ${eq:.2f}")


def _hrow(desk, s, b):
    m = desk.markets[s]
    return [b.ts.isoformat(), round(b.close, 4), round(m.vwap.value or b.close, 4), round(m.ema_fast.value or b.close, 4)]


def write_dashboard(desk, cfg, mode, eq):
    eq_rows = []
    p = os.path.join(SD, "equity.csv")
    if os.path.exists(p):
        eq_rows = [line.strip().split(",") for line in open(p) if line.strip()][-3000:]
    all_trades = []
    tp = os.path.join(SD, "trades.csv")
    if os.path.exists(tp):
        all_trades = list(csv.DictReader(open(tp)))
    for t in all_trades:
        for k in ("pnl", "r", "fees", "entry", "exit", "qty", "equity_at_entry"):
            t[k] = float(t[k])
    sb = scoreboard(all_trades, [(r[0], float(r[1])) for r in eq_rows] or [("", eq)], cfg.starting_cash,
                    getattr(desk, "fees_total", 0.0))
    out = {
        "updated": now().isoformat(), "mode": mode, "equity": round(eq, 4), "start_cash": cfg.starting_cash,
        "timeframe_min": cfg.timeframe_min, "brake": desk.nova.desk_closed,
        "symbols": {s: {
            "price": m.last.close if m.last else None,
            "charge": desk.charge.get(s, 0),
            "status": (desk.positions[s].side if s in desk.positions else
                       "charging" if desk.charge.get(s, 0) >= 70 else "watching"),
            "stop": desk.positions[s].plan["stop"] if s in desk.positions else None,
            "entry": desk.positions[s].entry if s in desk.positions else None,
            "day_pnl": round(desk.day_pnl_by_symbol.get(s, 0), 4),
            "thesis": desk.last_thesis.get(s),
            "bars": list(desk.hist.get(s, [])),
        } for s, m in desk.markets.items()},
        "events": desk.events[-60:],
        "trades": all_trades[-50:],
        "equity_curve": [[r[0], float(r[1])] for r in eq_rows[::max(1, len(eq_rows) // 400)]],
        "scoreboard": sb,
        "config": {k: v for k, v in vars(cfg).items() if k != "symbols"},
    }
    with open(os.path.join(DOCS, "crypto.json"), "w") as f:
        json.dump(out, f, default=str)


if __name__ == "__main__":
    main()
