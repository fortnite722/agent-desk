"""The honest scoreboard: real stats + a Monte Carlo 'what could have happened' test."""
from __future__ import annotations

import random
import statistics


def scoreboard(trades, equity_curve, start_cash, fees_paid=0.0) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = [e for _, e in equity_curve] or [start_cash]
    peak, max_dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak if peak else 0)
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "net_pnl": round(sum(pnls), 2),
        "return_pct": (eq[-1] - start_cash) / start_cash if start_cash else 0.0,
        "end_equity": round(eq[-1], 2),
        "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf") if gross_win else 0.0,
        "expectancy_r": round(statistics.mean([t["r"] for t in trades]), 3) if trades else 0.0,
        "max_drawdown": max_dd,
        "fees_paid": round(fees_paid, 2),
        "fees_vs_gross": round(fees_paid / gross_win, 2) if gross_win else None,
    }


def monte_carlo(trades, start_cash, runs=5000, n_trades=None, ruin_level=0.5, seed=1) -> dict:
    """Reshuffle the desk's own trades (as % of equity) thousands of times.

    Answers: 'given trades like these, how often does $100 double, and how
    often does it get cut in half first?'
    """
    if len(trades) < 5:
        return {"note": "need at least 5 trades for a meaningful Monte Carlo"}
    rng = random.Random(seed)
    # convert each trade to a % return on the equity it was taken with (approx by R * risk%)
    rets = [t["pnl"] / t.get("equity_at_entry", start_cash) for t in trades]
    n = n_trades or len(trades)
    finals, ruined, doubled = [], 0, 0
    for _ in range(runs):
        eq, dead, hit2x = start_cash, False, False
        for _ in range(n):
            eq *= 1 + rng.choice(rets)
            if eq <= start_cash * ruin_level:
                dead = True
                break
            if eq >= start_cash * 2:
                hit2x = True
        finals.append(eq)
        ruined += dead
        doubled += hit2x and not dead
    finals.sort()
    pct = lambda q: round(finals[int(q * (len(finals) - 1))], 2)
    return {"runs": runs, "trades_per_run": n, "p_cut_in_half": ruined / runs,
            "p_double": doubled / runs, "p_profit": sum(f > start_cash for f in finals) / runs,
            "worst_5pct": pct(0.05), "median": pct(0.5), "best_5pct": pct(0.95)}


def print_report(sb, mc, title="DESK REPORT"):
    line = "=" * 56
    print(f"\n{line}\n {title}\n{line}")
    print(f" Trades           {sb['trades']}  ({sb['wins']}W / {sb['losses']}L, win rate {sb['win_rate']:.0%})")
    print(f" Net P&L          ${sb['net_pnl']:+.2f}   ({sb['return_pct']:+.1%})   end equity ${sb['end_equity']:.2f}")
    print(f" Avg win / loss   ${sb['avg_win']:.2f} / ${sb['avg_loss']:.2f}")
    print(f" Profit factor    {sb['profit_factor']}    expectancy {sb['expectancy_r']:+.3f}R per trade")
    print(f" Max drawdown     {sb['max_drawdown']:.1%}")
    fv = sb["fees_vs_gross"]
    print(f" Fees paid        ${sb['fees_paid']:.2f}" + (f"  (= {fv:.0%} of gross wins)" if fv is not None else ""))
    if "runs" in mc:
        print(f"\n MONTE CARLO ({mc['runs']} reshuffles x {mc['trades_per_run']} trades)")
        print(f"   chance to finish up     {mc['p_profit']:.0%}")
        print(f"   chance to double        {mc['p_double']:.0%}")
        print(f"   chance to get cut 50%   {mc['p_cut_in_half']:.0%}")
        print(f"   worst 5% / median / best 5%:  ${mc['worst_5pct']} / ${mc['median']} / ${mc['best_5pct']}")
    else:
        print("\n " + mc["note"])
    print(line)
