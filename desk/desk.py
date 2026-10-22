"""The trading desk: runs the six agents on every closed bar, for every symbol.

Flow for each new bar:
  1. fill orders queued on the previous bar (at THIS bar's open: no look-ahead)
  2. LUNA manages open positions against this bar's high/low
  3. indicators update with the bar
  4. NOVA checks the daily brakes
  5. ATLAS + VEGA read the market -> ORION writes a thesis -> TITAN looks for
     the trigger -> NOVA sizes it or vetoes it -> order queued for next bar
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .agents import Atlas, Bar, Luna, Market, Nova, Orion, Titan, Vega, _say
from .config import DeskConfig


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry: float
    plan: dict
    opened_ts: object
    opened_i: int
    cost: float = 0.0          # cash spent (long) incl. fees
    proceeds: float = 0.0      # cash received so far incl. fees
    fees: float = 0.0
    start_qty: float = 0.0
    exits: list = field(default_factory=list)
    equity_at_entry: float = 0.0


class Desk:
    def __init__(self, cfg: DeskConfig, broker, headlines: dict | None = None, journal_path=None):
        self.cfg = cfg
        self.broker = broker
        self.markets = {s: Market(cfg) for s in cfg.symbols}
        self.atlas, self.vega, self.orion = Atlas(), Vega(headlines), Orion()
        self.titan, self.luna, self.nova = Titan(), Luna(), Nova(cfg)
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.events: list[dict] = []
        self.equity_curve: list[tuple] = []
        self.forming: dict[str, bool] = {}
        self.last_thesis: dict[str, dict] = {}
        self.charge: dict[str, float] = {s: 0.0 for s in cfg.symbols}
        self.day_pnl_by_symbol: dict[str, float] = {s: 0.0 for s in cfg.symbols}
        self.i = 0
        self.journal = open(journal_path, "a") if journal_path else None

    # ------------------------------------------------------------------ utils
    def emit(self, ts, symbol, ev):
        ev = {"i": self.i, "t": str(ts), "sym": symbol, **ev}
        self.events.append(ev)
        if self.journal:
            self.journal.write(json.dumps(ev, default=str) + "\n")
            self.journal.flush()
        return ev

    def equity(self) -> float:
        eq = self.broker.cash
        for s, p in self.positions.items():
            px = self.markets[s].last.close if self.markets[s].last else p.entry
            eq += p.qty * px if p.side == "long" else -p.qty * px
        return eq

    # ---------------------------------------------------------- order helpers
    def _open(self, symbol, order, bar: Bar):
        side = order["side"]
        eq_before = self.equity()
        fill = self.broker.execute(symbol, "buy" if side == "long" else "sell", order["qty"], bar.open)
        m = self.markets[symbol]
        plan = self.luna.plan(m, side, fill["price"])
        plan["risk_per_unit"] = order["risk_per_unit"]
        sgn = 1 if side == "long" else -1
        plan["stop"] = fill["price"] - sgn * order["risk_per_unit"]
        plan["tp1"] = fill["price"] + sgn * self.cfg.tp1_r * order["risk_per_unit"]
        notional = fill["qty"] * fill["price"]
        pos = Position(symbol, side, fill["qty"], fill["price"], plan, bar.ts, self.i,
                       start_qty=fill["qty"], fees=fill["fee"], equity_at_entry=eq_before)
        if side == "long":
            pos.cost = notional + fill["fee"]
        else:
            pos.proceeds = notional - fill["fee"]
        self.positions[symbol] = pos
        self.nova.trades_today[symbol] = self.nova.trades_today.get(symbol, 0) + 1
        self.emit(bar.ts, symbol, _say("TITAN", "fill",
            f"FILLED {side.upper()} {fill['qty']:.5g} @ {fill['price']:.2f} "
            f"(${notional:.2f}), stop {plan['stop']:.2f}, TP1 {plan['tp1']:.2f}",
            side=side, price=fill["price"], qty=fill["qty"], stop=plan["stop"], tp1=plan["tp1"]))

    def _reduce(self, symbol, qty, price, ts, why):
        pos = self.positions[symbol]
        full = qty >= pos.qty - 1e-12
        side = "sell" if pos.side == "long" else "buy"
        fill = self.broker.execute(symbol, side, qty, price)
        notional = fill["qty"] * fill["price"]
        pos.fees += fill["fee"]
        if pos.side == "long":
            pos.proceeds += notional - fill["fee"]
        else:
            pos.cost += notional + fill["fee"]
        pos.qty = 0.0 if full else pos.qty - fill["qty"]
        pos.exits.append((fill["price"], fill["qty"], why))
        if pos.qty <= 1e-12:
            self._finish(symbol, ts, why)
        else:
            part = (fill["price"] - pos.entry) * fill["qty"] * (1 if pos.side == "long" else -1)
            self.emit(ts, symbol, _say("LUNA", "trim",
                f"{why}: sold {fill['qty']:.5g} @ {fill['price']:.2f} (+${part:.2f} locked)",
                price=fill["price"], stop=pos.plan["stop"]))

    def _finish(self, symbol, ts, why):
        pos = self.positions.pop(symbol)
        pnl = pos.proceeds - pos.cost
        risk_usd = pos.plan["risk_per_unit"] * pos.start_qty
        r_mult = pnl / risk_usd if risk_usd else 0.0
        avg_exit = sum(p * q for p, q, _ in pos.exits) / max(1e-12, sum(q for _, q, _ in pos.exits))
        trade = {"symbol": symbol, "side": pos.side, "entry_t": str(pos.opened_ts), "exit_t": str(ts),
                 "entry": pos.entry, "exit": avg_exit, "qty": pos.start_qty, "pnl": round(pnl, 4),
                 "r": round(r_mult, 2), "fees": round(pos.fees, 4), "why": why,
                 "trimmed": pos.plan["trimmed"], "bars": self.i - pos.opened_i,
                 "equity_at_entry": round(pos.equity_at_entry, 4)}
        self.trades.append(trade)
        self.day_pnl_by_symbol[symbol] += pnl
        icon = "PROFIT LOCKED" if pnl > 0 else "LOSS TAKEN"
        self.emit(ts, symbol, _say("LUNA", "close",
            f"{icon} {pnl:+.2f} ({r_mult:+.2f}R) - {why}", pnl=pnl, r=r_mult, exit=avg_exit))
        if self.nova.record_close(symbol, pnl, self.i):
            self.emit(ts, symbol, _say("NOVA", "cooldown",
                f"{self.cfg.cooldown_after_losses} losses in a row on {symbol}, benching it for "
                f"{self.cfg.cooldown_bars} bars"))

    # ------------------------------------------------------------- main loop
    def on_bar(self, symbol: str, bar: Bar):
        self.i += 1
        m = self.markets[symbol]
        cfg = self.cfg

        if self.nova.new_day(bar.session, self.equity()):
            self.day_pnl_by_symbol = {s: 0.0 for s in cfg.symbols}
            self.emit(bar.ts, "DESK", _say("NOVA", "day",
                f"New session {bar.session}. Bankroll ${self.equity():.2f}. Brakes armed: "
                f"-{cfg.daily_loss_limit_pct:.0%} loss / +{cfg.daily_profit_goal_pct:.0%} lock"))

        # 1) fills queued last bar
        order = self.pending.pop(symbol, None)
        if order and order["kind"] == "entry" and symbol not in self.positions:
            self._open(symbol, order, bar)
        elif order and order["kind"] == "exit" and symbol in self.positions:
            self._reduce(symbol, self.positions[symbol].qty, bar.open, bar.ts, order["why"])

        # 2) Luna manages the open trade against this bar
        if symbol in self.positions and m.ready:
            pos = self.positions[symbol]
            for act in self.luna.manage(m, pos, bar):
                if symbol not in self.positions:
                    break
                if act["action"] == "trim":
                    self._reduce(symbol, pos.qty / 2, act["price"], bar.ts, act["why"])
                else:
                    self._reduce(symbol, pos.qty, act["price"], bar.ts, act["why"])

        # 3) indicators
        m.update(bar)

        # end-of-day flatten (stocks)
        if cfg.flatten_at_close and symbol in self.positions and \
                (bar.ts.hour, bar.ts.minute) >= (15, 55):
            self._reduce(symbol, self.positions[symbol].qty, bar.close, bar.ts, "flat before the bell")

        # 4) brakes
        was_closed = self.nova.desk_closed
        reason = self.nova.check_brakes(self.equity())
        if reason and not was_closed:
            self.emit(bar.ts, "DESK", _say("NOVA", "brake", reason))
            if reason.startswith("LOSS"):
                for s in list(self.positions):
                    px = self.markets[s].last.close
                    self._reduce(s, self.positions[s].qty, px, bar.ts, "loss brake: flatten everything")

        self.equity_curve.append((str(bar.ts), round(self.equity(), 4)))
        if not m.ready:
            return

        # 5) open trade: Luna watches for invalidation
        if symbol in self.positions:
            self.charge[symbol] = 100.0
            why = self.luna.invalidated(m, self.positions[symbol])
            if why and symbol not in self.pending:
                self.pending[symbol] = {"kind": "exit", "why": why}
                self.emit(bar.ts, symbol, _say("LUNA", "invalid", why + ", exiting next bar"))
            return
        if symbol in self.pending:
            return

        # 6) the thinking chain
        atlas = self.atlas.read(m)
        vega = self.vega.read(symbol)
        thesis = self.orion.think(m, atlas, vega)
        self.last_thesis[symbol] = {**thesis, "regime": atlas["regime"], "flow": atlas["flow"]}
        tradable = thesis["side"] == "long" or cfg.allow_short
        ch = self.titan.charge(m, thesis["side"]) if tradable else 0.0
        self.charge[symbol] = ch

        if ch >= 70 and not self.forming.get(symbol):
            self.forming[symbol] = True
            self.emit(bar.ts, symbol, _say("TITAN", "forming",
                f"setup forming: {'CALL' if thesis['side']=='long' else 'PUT'}-side "
                f"{thesis['side']} at VWAP/EMA50, {ch:.0f}% charged", side=thesis["side"], charge=ch))
        elif ch < 40 and self.forming.get(symbol):
            self.forming[symbol] = False
            self.emit(bar.ts, symbol, _say("TITAN", "faded", "setup faded, standing down"))

        trig = self.titan.trigger(m, thesis["side"])
        if not trig or not tradable:
            return
        self.forming[symbol] = False
        if not thesis["ok"]:
            self.emit(bar.ts, symbol, _say("ORION", "reject",
                f"{trig['level']} cross {thesis['side']}, but confidence {thesis['confidence']:.0%}: "
                + ("; ".join(thesis["cons"]) or "not enough reasons"), **thesis))
            return
        risk_unit = cfg.stop_atr * m.atr.value
        verdict = self.nova.approve(symbol, thesis["side"], bar.close, risk_unit, self.equity(),
                                    self.broker.cash, len(self.positions) + sum(
                                        1 for o in self.pending.values() if o["kind"] == "entry"),
                                    self.i)
        if not verdict["ok"]:
            self.emit(bar.ts, symbol, _say("NOVA", "veto", "BLOCKED: " + verdict["why"]))
            return
        self.pending[symbol] = {"kind": "entry", "side": thesis["side"], "qty": verdict["qty"],
                                "risk_per_unit": risk_unit}
        self.emit(bar.ts, symbol, _say("ORION", "thesis",
            f"{thesis['side'].upper()} {thesis['confidence']:.0%}: " + ", ".join(thesis["pros"])
            + (f" | against: {'; '.join(thesis['cons'])}" if thesis["cons"] else ""), **thesis))
        self.emit(bar.ts, symbol, _say("NOVA", "approve",
            f"APPROVED ${verdict['notional']:.2f}, risking ${verdict['risk_usd']:.2f}"
            + (" (size capped)" if verdict["capped"] else ""), **verdict))
        self.emit(bar.ts, symbol, _say("TITAN", "fire",
            f"FIRE {thesis['side'].upper()}: closed across {trig['level']} {trig['level_px']:.2f}"))

    def flush_pending(self, ts):
        """LIVE mode: act on queued orders right now at market instead of waiting a bar."""
        for sym in list(self.pending):
            order = self.pending.pop(sym)
            m = self.markets[sym]
            if not m.last:
                continue
            ref = m.last.close
            now_bar = Bar(ts, ref, ref, ref, ref, 0.0)
            if order["kind"] == "entry" and sym not in self.positions:
                self._open(sym, order, now_bar)
            elif order["kind"] == "exit" and sym in self.positions:
                self._reduce(sym, self.positions[sym].qty, ref, ts, order["why"])

    def close_all(self, ts, why="session end"):
        for s in list(self.positions):
            self._reduce(s, self.positions[s].qty, self.markets[s].last.close, ts, why)
