"""Entry setups. Each returns [(bar_index, 'C' or 'P'), ...] for one day.

The signal fires on a bar's CLOSE; the trade is entered on the NEXT bar's open.
"""
from __future__ import annotations


def _cross(prev_a, prev_b, a, b):
    if prev_a <= prev_b and a > b:
        return "C"
    if prev_a >= prev_b and a < b:
        return "P"
    return None


def vwap_cross(d):
    return [(i, s) for i in range(1, len(d.c)) if (s := _cross(d.c[i-1], d.vwap[i-1], d.c[i], d.vwap[i]))]


def ema50_cross(d):
    return [(i, s) for i in range(1, len(d.c)) if (s := _cross(d.c[i-1], d.e50[i-1], d.c[i], d.e50[i]))]


def ema9_21(d):
    return [(i, s) for i in range(1, len(d.c)) if (s := _cross(d.e9[i-1], d.e21[i-1], d.e9[i], d.e21[i]))]


def orb(n):
    """Opening-range breakout: first close above the first-n-minute high (call) or below the low (put)."""
    def f(d):
        if n not in d.orh:
            return []
        out, up, dn = [], False, False
        for i, m in enumerate(d.minute):
            if m < n:
                continue
            if not up and d.c[i] > d.orh[n]:
                out.append((i, "C")); up = True
            if not dn and d.c[i] < d.orl[n]:
                out.append((i, "P")); dn = True
        return out
    f.__name__ = f"orb{n}"
    return f


def vwap_bounce(d):
    """Trend pullback: in an uptrend (above EMA50), price tags VWAP and closes back above it."""
    out = []
    for i in range(1, len(d.c)):
        if d.c[i] > d.e50[i] and d.l[i] <= d.vwap[i] < d.c[i] and d.c[i-1] > d.vwap[i-1]:
            out.append((i, "C"))
        elif d.c[i] < d.e50[i] and d.h[i] >= d.vwap[i] > d.c[i] and d.c[i-1] < d.vwap[i-1]:
            out.append((i, "P"))
    return out


def rsi_fade(d):
    """Fade extremes: RSI < 25 then an up-close -> call; RSI > 75 then a down-close -> put."""
    out = []
    for i in range(1, len(d.c)):
        if d.rsi[i-1] < 25 and d.c[i] > d.c[i-1]:
            out.append((i, "C"))
        elif d.rsi[i-1] > 75 and d.c[i] < d.c[i-1]:
            out.append((i, "P"))
    return out


SIGNALS = {f.__name__: f for f in (vwap_cross, ema50_cross, ema9_21, orb(5), orb(15), orb(30),
                                   vwap_bounce, rsi_fade)}

# minute windows (minutes after 9:30) for the ENTRY bar
WINDOWS = {"open": (5, 60), "morning": (5, 120), "midday": (120, 240), "power": (240, 345), "all": (5, 345)}
