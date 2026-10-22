"""The six agents. Each one has ONE job and explains itself in plain English.

    ATLAS  -> reads the flow (volume surges, volatility, trend)
    VEGA   -> reads the narrative (headlines you feed it; neutral if none)
    ORION  -> writes the trade thesis AND argues against it
    TITAN  -> times the entry (VWAP / EMA cross with confirmation)
    LUNA   -> manages the exit (stop, trim at +1R, trail, invalidation)
    NOVA   -> risk officer. Sizes every trade and can veto anything.

None of these "predict" the market. They are rules. The point of the desk is
that every decision is written down, so paper results can be checked honestly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import DeskConfig
from .indicators import ATR, EMA, RSI, RollingZ, SessionVWAP


# --------------------------------------------------------------------------
@dataclass
class Bar:
    ts: object            # datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def session(self):
        return self.ts.date()


@dataclass
class Market:
    """Indicator state for one symbol, shared by all agents."""
    cfg: DeskConfig
    vwap: SessionVWAP = field(default_factory=SessionVWAP)
    atr: ATR = field(default_factory=ATR)
    rsi: RSI = field(default_factory=RSI)
    volz: RollingZ = field(default_factory=RollingZ)
    ema_fast: EMA = None
    ema_slow: EMA = None
    prev_close: float | None = None
    prev_vwap: float | None = None
    prev_ema: float | None = None
    bars: int = 0
    last: Bar | None = None

    def __post_init__(self):
        self.ema_fast = EMA(self.cfg.ema_fast)
        self.ema_slow = EMA(self.cfg.ema_slow)

    def update(self, bar: Bar):
        if self.last is not None:
            self.prev_close = self.last.close
            self.prev_vwap = self.vwap.value
            self.prev_ema = self.ema_fast.value
        self.vwap.update(bar.session, bar.high, bar.low, bar.close, bar.volume)
        self.atr.update(bar.high, bar.low, bar.close)
        self.rsi.update(bar.close)
        self.volz.update(bar.volume)
        self.ema_fast.update(bar.close)
        self.ema_slow.update(bar.close)
        self.bars += 1
        self.last = bar

    @property
    def ready(self) -> bool:
        return self.bars >= self.cfg.warmup_bars and self.atr.ready


def _say(agent, kind, msg, **data):
    return {"agent": agent, "kind": kind, "msg": msg, "data": data}


# --------------------------------------------------------------------------
class Atlas:
    """Flow tracker. Our version of 'tracking whales': unusual volume + volatility."""
    name = "ATLAS"

    def read(self, m: Market) -> dict:
        c = m.cfg
        price = m.last.close
        atr_pct = m.atr.value / price
        regime = "dead" if atr_pct < c.dead_atr_pct else "wild" if atr_pct > c.wild_atr_pct else "normal"
        trend = 0
        if m.ema_slow.ready:
            if m.ema_fast.value > m.ema_slow.value and m.ema_fast.slope > 0:
                trend = 1
            elif m.ema_fast.value < m.ema_slow.value and m.ema_fast.slope < 0:
                trend = -1
        flow = m.volz.value
        surge = flow >= 2.0
        return {"regime": regime, "trend": trend, "flow": round(flow, 2),
                "surge": surge, "atr_pct": atr_pct}


class Vega:
    """Narrative reader. Scores headlines with a simple keyword model.

    Feed it real headlines (news.json or your own scraper). With no headlines
    it stays neutral on purpose: no news is not a signal.
    """
    name = "VEGA"
    BULL = ("approval", "approved", "etf inflow", "beats", "upgrade", "partnership",
            "record high", "adoption", "rate cut", "buyback", "surge")
    BEAR = ("hack", "exploit", "lawsuit", "sec sues", "ban", "downgrade", "misses",
            "outflow", "delist", "bankrupt", "rate hike", "liquidation", "plunge")

    def __init__(self, headlines: dict | None = None):
        self.headlines = headlines or {}

    def read(self, symbol: str) -> dict:
        items = self.headlines.get(symbol, [])
        score = 0
        for h in items:
            t = h.lower()
            score += sum(w in t for w in self.BULL) - sum(w in t for w in self.BEAR)
        tilt = max(-1.0, min(1.0, score / 3))
        return {"tilt": tilt, "n": len(items)}


class Orion:
    """Builds the thesis, then plays devil's advocate against it."""
    name = "ORION"

    def think(self, m: Market, atlas: dict, vega: dict) -> dict:
        c = m.cfg
        price, vwap, ema = m.last.close, m.vwap.value, m.ema_fast.value
        atr = m.atr.value
        # direction comes from the bigger trend, not from where price happens to be
        if atlas["trend"] != 0:
            side = "long" if atlas["trend"] > 0 else "short"
        else:
            side = "long" if m.ema_fast.slope >= 0 else "short"
        sgn = 1 if side == "long" else -1

        pros, cons = [], []
        conf = 0.35
        if atlas["trend"] == sgn:
            conf += 0.20; pros.append("trend agrees")
        elif atlas["trend"] == -sgn:
            conf -= 0.15; cons.append("fighting the bigger trend")
        if (price - ema) * sgn > 0:
            conf += 0.10; pros.append("above/below EMA50 on the right side")
        if atlas["flow"] > 0.5:
            conf += 0.10; pros.append(f"volume picking up (z={atlas['flow']})")
        if atlas["surge"]:
            conf += 0.05; pros.append("volume surge")
        if vega["tilt"] * sgn > 0.2:
            conf += 0.10; pros.append("headlines lean our way")
        elif vega["tilt"] * sgn < -0.2:
            conf -= 0.15; cons.append("headlines lean against us")

        stretch = abs(price - vwap) / atr if atr else 0
        if stretch > c.max_stretch_atr:
            conf -= 0.25; cons.append(f"chasing: {stretch:.1f} ATR from VWAP")
        r = m.rsi.value
        if side == "long" and r > c.rsi_chase_long:
            conf -= 0.15; cons.append(f"RSI {r:.0f}, overheated")
        if side == "short" and r < c.rsi_chase_short:
            conf -= 0.15; cons.append(f"RSI {r:.0f}, washed out")
        if atlas["regime"] != "normal":
            conf -= 0.30; cons.append(f"market is {atlas['regime']}")

        conf = round(max(0.0, min(1.0, conf)), 2)
        return {"side": side, "confidence": conf, "pros": pros, "cons": cons,
                "ok": conf >= c.min_confidence}


class Titan:
    """Times the entry: a candle CLOSES across VWAP or the 50 EMA, with confirmation."""
    name = "TITAN"

    def charge(self, m: Market, side: str) -> float:
        """0-100%: how close price is to triggering (for the dashboard meter)."""
        if not m.ready:
            return 0.0
        price, atr = m.last.close, m.atr.value
        sgn = 1 if side == "long" else -1
        best = 0.0
        for lvl in (m.vwap.value, m.ema_fast.value):
            dist = (lvl - price) * sgn / atr  # >0: still on the far side, approaching
            if dist > 0:                       # only a FRESH cross counts as a setup
                best = max(best, max(0.0, 100 - dist * 60))
        return round(min(best, 99.0), 1)

    def trigger(self, m: Market, side: str) -> dict | None:
        if m.prev_close is None or m.prev_vwap is None or m.prev_ema is None:
            return None
        c = m.cfg
        price, prev = m.last.close, m.prev_close
        buf = c.confirm_atr * m.atr.value
        sgn = 1 if side == "long" else -1
        for name, lvl, prev_lvl in (("VWAP", m.vwap.value, m.prev_vwap),
                                    ("EMA50", m.ema_fast.value, m.prev_ema)):
            crossed = (prev - prev_lvl) * sgn <= 0 and (price - lvl) * sgn > buf
            if crossed and m.volz.value > -0.5:
                return {"level": name, "price": price, "level_px": lvl}
        return None


class Luna:
    """Exit manager: hard stop, trim half at +1R, move stop to breakeven, trail."""
    name = "LUNA"

    def plan(self, m: Market, side: str, entry: float) -> dict:
        c = m.cfg
        sgn = 1 if side == "long" else -1
        risk = c.stop_atr * m.atr.value
        return {"stop": entry - sgn * risk, "tp1": entry + sgn * c.tp1_r * risk,
                "risk_per_unit": risk, "trimmed": False, "bars": 0}

    def manage(self, m: Market, pos, bar: Bar) -> list[dict]:
        """Called on every new bar for an open position. Returns exit actions."""
        c = m.cfg
        sgn = 1 if pos.side == "long" else -1
        acts = []
        pos.plan["bars"] += 1
        stop, tp1 = pos.plan["stop"], pos.plan["tp1"]
        # 1) stop first (conservative: assume the worst happened inside the bar)
        hit_stop = bar.low <= stop if sgn == 1 else bar.high >= stop
        if hit_stop:
            px = min(bar.open, stop) if sgn == 1 else max(bar.open, stop)
            why = "trailing stop" if pos.plan["trimmed"] else "stop hit"
            return [{"action": "close", "price": px, "why": why}]
        # 2) first target -> trim half, stop to breakeven + costs
        if not pos.plan["trimmed"]:
            hit_tp = bar.high >= tp1 if sgn == 1 else bar.low <= tp1
            if hit_tp:
                px = max(bar.open, tp1) if sgn == 1 else min(bar.open, tp1)
                acts.append({"action": "trim", "price": px, "why": "TP1 +1R, trimming half"})
                be = pos.entry * (1 + sgn * c.round_trip_cost_pct)
                pos.plan["stop"] = be
                pos.plan["trimmed"] = True
        # 3) trail the runner
        if pos.plan["trimmed"]:
            trail = (bar.high - c.trail_atr * m.atr.value) if sgn == 1 else (bar.low + c.trail_atr * m.atr.value)
            if (trail - pos.plan["stop"]) * sgn > 0:
                pos.plan["stop"] = trail
        # 4) time stop
        if pos.plan["bars"] >= c.max_hold_bars:
            acts.append({"action": "close", "price": bar.close, "why": "time stop"})
        return acts

    def invalidated(self, m: Market, pos) -> str | None:
        """Thesis broken: closed back through BOTH VWAP and EMA50 before TP1."""
        if pos.plan["trimmed"]:
            return None
        sgn = 1 if pos.side == "long" else -1
        p = m.last.close
        if (p - m.vwap.value) * sgn < 0 and (p - m.ema_fast.value) * sgn < 0:
            return "thesis broken: back through VWAP and EMA50"
        return None


class Nova:
    """Risk officer. Final veto. Sizes positions. Owns the brakes."""
    name = "NOVA"

    def __init__(self, cfg: DeskConfig):
        self.cfg = cfg
        self.day = None
        self.day_start_equity = cfg.starting_cash
        self.trades_today: dict[str, int] = {}
        self.loss_streak: dict[str, int] = {}
        self.cooldown_until: dict[str, int] = {}
        self.desk_closed = None  # reason string when brakes engage

    def new_day(self, day, equity):
        if day != self.day:
            self.day = day
            self.day_start_equity = equity
            self.trades_today = {}
            self.desk_closed = None
            return True
        return False

    def check_brakes(self, equity) -> str | None:
        if self.desk_closed:
            return self.desk_closed
        pnl_pct = (equity - self.day_start_equity) / self.day_start_equity
        if pnl_pct <= -self.cfg.daily_loss_limit_pct:
            self.desk_closed = f"LOSS BRAKE: down {pnl_pct:.1%} today, desk closed"
        elif pnl_pct >= self.cfg.daily_profit_goal_pct:
            self.desk_closed = f"PROFIT LOCK: up {pnl_pct:.1%}, clocking out"
        return self.desk_closed

    def approve(self, symbol, side, price, risk_per_unit, equity, cash, open_count, bar_i) -> dict:
        c = self.cfg
        def no(why): return {"ok": False, "why": why}
        if side == "short" and not c.allow_short:
            return no("can't short in a cash account, standing down")
        if self.desk_closed:
            return no(self.desk_closed)
        if open_count >= c.max_open_positions:
            return no(f"already {open_count} positions open (max {c.max_open_positions})")
        if self.trades_today.get(symbol, 0) >= c.max_trades_per_day:
            return no(f"hit {c.max_trades_per_day} trades today on {symbol}")
        if bar_i < self.cooldown_until.get(symbol, -1):
            return no(f"cooling off after {c.cooldown_after_losses} losses in a row")
        r_pct = risk_per_unit / price
        need = c.min_edge_multiple * c.round_trip_cost_pct
        if r_pct < need:
            return no(f"move too small: 1R is {r_pct:.2%}, fees need {need:.2%}")
        risk_usd = equity * c.risk_per_trade_pct
        qty = risk_usd / risk_per_unit
        notional = qty * price
        cap = min(equity * c.max_position_pct, cash * 0.98)
        capped = False
        if notional > cap:
            notional, qty, capped = cap, cap / price, True
        if notional < c.min_order_usd:
            return no(f"size ${notional:.2f} below broker minimum")
        return {"ok": True, "qty": qty, "notional": notional, "risk_usd": qty * risk_per_unit,
                "capped": capped}

    def record_close(self, symbol, pnl, bar_i):
        if pnl < 0:
            self.loss_streak[symbol] = self.loss_streak.get(symbol, 0) + 1
            if self.loss_streak[symbol] >= self.cfg.cooldown_after_losses:
                self.cooldown_until[symbol] = bar_i + self.cfg.cooldown_bars
                self.loss_streak[symbol] = 0
                return True
        else:
            self.loss_streak[symbol] = 0
        return False
