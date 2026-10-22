"""All the knobs in one place. Edit these, not the agent code."""
from dataclasses import dataclass, field


@dataclass
class DeskConfig:
    # ---- account -------------------------------------------------------
    starting_cash: float = 100.0
    allow_short: bool = False          # cash accounts / crypto on Alpaca can't short
    flatten_at_close: bool = True      # stocks: be flat before the bell. Crypto never closes.

    # ---- Nova: risk officer (final veto) ------------------------------
    risk_per_trade_pct: float = 0.02   # lose at most 2% of equity if the stop hits
    max_position_pct: float = 0.50     # never put more than 50% of equity in one trade
    max_open_positions: int = 2        # desk-wide
    daily_loss_limit_pct: float = 0.05 # LOSS BRAKE: down 5% on the day -> desk closes
    daily_profit_goal_pct: float = 0.06  # PROFIT LOCK: up 6% -> clock out, keep it
    max_trades_per_day: int = 6        # per symbol
    cooldown_after_losses: int = 2     # this many losses in a row ->
    cooldown_bars: int = 30            # ...sit out this many bars
    min_edge_multiple: float = 2.0     # the 1R move must be >= 2x round-trip costs
    min_order_usd: float = 1.0

    # ---- costs (be pessimistic; fake-money results lie if costs are zero)
    fee_pct: float = 0.0025            # per side. Alpaca crypto taker is ~0.15-0.25%; stocks ~0
    slippage_pct: float = 0.0005       # per side

    # ---- Atlas: market flow -------------------------------------------
    dead_atr_pct: float = 0.0004       # ATR below 0.04% of price = nothing moving
    wild_atr_pct: float = 0.012        # ATR above 1.2% of price per bar = too wild

    # ---- Orion: thesis --------------------------------------------------
    min_confidence: float = 0.55
    max_stretch_atr: float = 2.5       # don't chase price this far from VWAP
    rsi_chase_long: float = 76
    rsi_chase_short: float = 24

    # ---- Titan: entry ---------------------------------------------------
    ema_fast: int = 50
    ema_slow: int = 200
    confirm_atr: float = 0.10          # close must clear the level by 0.1 ATR
    warmup_bars: int = 60

    # ---- Luna: exits ----------------------------------------------------
    stop_atr: float = 1.5
    tp1_r: float = 1.0                 # take half off at +1R, stop -> breakeven
    trail_atr: float = 1.0             # then trail the rest
    max_hold_bars: int = 48

    timeframe_min: int = 15            # bar size. Fees decide this, see README
    symbols: list = field(default_factory=lambda: ["BTC/USD", "ETH/USD", "SOL/USD"])

    @property
    def round_trip_cost_pct(self) -> float:
        return 2 * (self.fee_pct + self.slippage_pct)


# Presets ----------------------------------------------------------------
def crypto_config(**kw) -> DeskConfig:
    base = dict(flatten_at_close=False, timeframe_min=15)
    base.update(kw)
    return DeskConfig(**base)


def stocks_config(**kw) -> DeskConfig:
    base = dict(fee_pct=0.0, slippage_pct=0.0003, flatten_at_close=True, timeframe_min=5,
                symbols=["SPY", "QQQ", "IWM"])
    base.update(kw)
    return DeskConfig(**base)
