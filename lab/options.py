"""0DTE option pricing for the lab.

We don't have years of tick-level option quotes, so the lab prices each 0DTE
contract with Black-Scholes from the underlying's price, minutes left in the
day, and an implied vol estimated from recent realized vol. Then it charges a
bid/ask spread and fees on both sides, so a trade has to beat real costs.

This is a MODEL. `validate.py` checks the model against Alpaca's real
historical option bars for the trades that matter, before you trust it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MIN_PER_YEAR = 390 * 252  # trading minutes per year


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S: float, K: float, minutes_left: float, iv: float, kind: str, r: float = 0.04) -> float:
    T = max(minutes_left, 0.5) / MIN_PER_YEAR
    if iv <= 0:
        return max(0.0, (S - K) if kind == "C" else (K - S))
    sd = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / sd
    d2 = d1 - sd
    if kind == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


@dataclass
class OptionCosts:
    iv_mult: float = 1.10        # 0DTE implied vol usually runs above realized
    iv_add: float = 0.03
    iv_floor: float = 0.12
    half_spread_min: float = 0.01   # $ per share, each side
    half_spread_pct: float = 0.015  # of premium, each side
    fee_per_contract: float = 0.04  # regulatory/clearing fees, each side ($)
    strike_step: float = 1.0        # QQQ strikes are $1 apart

    def iv(self, realized_annual: float) -> float:
        return max(self.iv_floor, realized_annual * self.iv_mult + self.iv_add)

    def half_spread(self, mid: float) -> float:
        return max(self.half_spread_min, mid * self.half_spread_pct)

    def strike(self, S: float, kind: str, otm_steps: int) -> float:
        atm = round(S / self.strike_step) * self.strike_step
        return atm + otm_steps * self.strike_step * (1 if kind == "C" else -1)


def occ_symbol(root: str, day, kind: str, strike: float) -> str:
    """OCC option symbol, e.g. QQQ261021C00480000."""
    return f"{root}{day:%y%m%d}{kind}{int(round(strike * 1000)):08d}"
