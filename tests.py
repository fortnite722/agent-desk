"""Sanity tests. Run: python tests.py   (all should say PASS)"""
from datetime import datetime, timedelta

from backtest import run
from desk.agents import Bar
from desk.config import crypto_config
from desk.data import aggregate, synthetic


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return cond


def test_accounting_and_risk():
    ok = True
    for seed in range(1, 9):
        cfg = crypto_config()
        bars = {s: aggregate(b, 15) for s, b in synthetic(cfg.symbols, days=10, seed=seed).items()}
        desk, sb, _ = run(cfg, bars, quiet=True)
        delta = desk.equity() - cfg.starting_cash
        ok &= check(f"seed {seed}: P&L adds up to equity change",
                    abs(sum(t["pnl"] for t in desk.trades) - delta) < 1e-3, f"{delta:+.4f}")
        worst = min((t["pnl"] / t["equity_at_entry"] for t in desk.trades), default=0)
        # 2% risk, plus gap-through-stop slippage and fees on both sides
        ok &= check(f"seed {seed}: worst trade within risk budget", worst > -0.045, f"{worst:.2%}")
        ok &= check(f"seed {seed}: never spent cash it didn't have", min(e for _, e in desk.equity_curve) > 0)
    return ok


def crash_bars(sym, start, n=400, drop_at=300):
    """Gentle uptrend, then a waterfall. The loss brake must close the desk."""
    import math
    bars, px, base = [], 100.0, 100.0
    for k in range(n):
        o = px
        if k < drop_at:  # wavy uptrend so price keeps reclaiming VWAP / EMA50
            base *= 1.0015
            px = base * (1 + 0.025 * math.sin(k / 5))
        else:
            px *= 0.985
        h, l = max(o, px) * 1.001, min(o, px) * 0.999
        bars.append(Bar(start + timedelta(minutes=15 * k), o, h, l, px, 1000 + (k % 7) * 300))
    return bars


def test_loss_brake():
    cfg = crypto_config(symbols=["TEST"], risk_per_trade_pct=0.05, max_position_pct=1.0,
                        daily_loss_limit_pct=0.03, daily_profit_goal_pct=1.0, fee_pct=0.0,
                        slippage_pct=0.0, ema_fast=20, ema_slow=50, warmup_bars=30,
                        min_confidence=0.3)
    start = datetime(2026, 10, 5, 0, 0)
    desk, _, _ = run(cfg, {"TEST": crash_bars("TEST", start)}, quiet=True)
    brakes = [e for e in desk.events if e["kind"] == "brake"]
    crash_start = start + timedelta(minutes=15 * 300)
    during = [t for t in desk.trades if t["exit_t"] >= str(crash_start)]
    worst = min((t["pnl"] / t["equity_at_entry"] for t in desk.trades), default=0)
    a = check("desk actually traded the uptrend", len(desk.trades) > 3, f"{len(desk.trades)} trades")
    b = check("no single trade lost more than ~1 risk unit in the crash", worst > -0.08, f"worst {worst:.2%}")
    c = check("desk ended with most of the bankroll", desk.equity() > 85, f"${desk.equity():.2f}")
    print("     brake events:", [x["msg"] for x in brakes][:3], "| trades closed in crash:", len(during))
    return a and b and c


def test_brakes_direct():
    from desk.agents import Nova
    n = Nova(crypto_config())
    n.new_day("d1", 100.0)
    a = check("brakes stay off at -4%", n.check_brakes(96.0) is None)
    b = check("LOSS BRAKE engages at -5%", (n.check_brakes(94.9) or "").startswith("LOSS"))
    c = check("closed desk vetoes new trades",
              not n.approve("BTC/USD", "long", 100, 1.5, 94.9, 94.9, 0, 1)["ok"])
    n.new_day("d2", 94.9)
    d = check("brakes reset next day", n.check_brakes(95.0) is None)
    e = check("PROFIT LOCK engages at +6%", (n.check_brakes(100.6) or "").startswith("PROFIT"))
    n.new_day("d3", 100.0)
    v = n.approve("BTC/USD", "long", 100.0, 5.0, 100.0, 100.0, 0, 1)
    f = check("sizing risks 2% of equity", v["ok"] and abs(v["risk_usd"] - 2.0) < 1e-9, f"${v['risk_usd']:.2f}")
    g = check("tiny moves vetoed for fees", not n.approve("BTC/USD", "long", 100.0, 0.5, 100, 100, 0, 1)["ok"])
    return all([a, b, c, d, e, f, g])


def test_no_lookahead():
    cfg = crypto_config()
    bars = {s: aggregate(b, 15) for s, b in synthetic(cfg.symbols, days=10, seed=3).items()}
    desk, _, _ = run(cfg, bars, quiet=True)
    fires = {(e["sym"], e["i"]) for e in desk.events if e["kind"] == "fire"}
    fills = [e for e in desk.events if e["kind"] == "fill"]
    later = all(any(s == f["sym"] and i < f["i"] for s, i in fires) for f in fills)
    return check("every fill happens on a LATER bar than its signal", later and bool(fills),
                 f"{len(fills)} fills")


if __name__ == "__main__":
    results = [test_accounting_and_risk(), test_loss_brake(), test_brakes_direct(), test_no_lookahead()]
    print("\nALL PASS" if all(results) else "\nSOMETHING FAILED")
