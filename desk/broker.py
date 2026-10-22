"""Brokers. PaperBroker = fully simulated. AlpacaBroker = Alpaca's paper account.

Both charge fees + slippage the same way, so simulated results stay honest.
Real money is deliberately NOT wired up: AlpacaBroker refuses live keys
unless you edit the code yourself and set ALLOW_LIVE = True.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

ALLOW_LIVE = False  # leave this False until paper results earn it


class PaperBroker:
    def __init__(self, cash: float, fee_pct: float, slippage_pct: float):
        self.cash = cash
        self.fee_pct = fee_pct
        self.slip = slippage_pct
        self.fees_paid = 0.0

    def fill_price(self, side: str, price: float) -> float:
        return price * (1 + self.slip) if side == "buy" else price * (1 - self.slip)

    def execute(self, symbol: str, side: str, qty: float, price: float) -> dict:
        px = self.fill_price(side, price)
        notional = qty * px
        fee = notional * self.fee_pct
        self.fees_paid += fee
        if side == "buy":
            self.cash -= notional + fee
        else:
            self.cash += notional - fee
        return {"symbol": symbol, "side": side, "qty": qty, "price": px, "fee": fee}


class AlpacaBroker(PaperBroker):
    """Sends market orders to Alpaca's PAPER API and records the real fills.

    Set env vars APCA_API_KEY_ID and APCA_API_SECRET_KEY (free paper account at alpaca.markets).
    """
    PAPER = "https://paper-api.alpaca.markets"

    def __init__(self, cash: float, fee_pct: float, slippage_pct: float):
        """`cash` is the desk's own bankroll (e.g. $100), tracked separately from
        Alpaca's default $100k paper balance so sizing matches your real plan."""
        self.key = os.environ.get("APCA_API_KEY_ID", "")
        self.secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not self.key or not self.secret:
            raise SystemExit("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY first (paper keys).")
        base = os.environ.get("APCA_BASE_URL", self.PAPER)
        if "paper" not in base and not ALLOW_LIVE:
            raise SystemExit("Refusing to trade live. Paper only until you change ALLOW_LIVE.")
        self.base = base
        self._req("GET", "/v2/account")  # fails fast if keys are wrong
        super().__init__(cash, fee_pct, slippage_pct)

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}

    def execute(self, symbol: str, side: str, qty: float, price: float) -> dict:
        crypto = "/" in symbol
        if crypto and side == "sell":
            # Alpaca takes crypto fees out of the coins you receive, so sell what we actually hold
            try:
                held = float(self._req("GET", f"/v2/positions/{symbol.replace('/', '')}")["qty"])
                qty = min(qty, held)
            except Exception:
                pass
        order = {"symbol": symbol, "qty": f"{qty:.6f}" if crypto else str(int(qty)),
                 "side": side, "type": "market", "time_in_force": "gtc" if crypto else "day"}
        if not crypto and int(qty) < 1:  # fractional stock orders use notional
            order.pop("qty")
            order["notional"] = f"{qty * price:.2f}"
        o = self._req("POST", "/v2/orders", order)
        for _ in range(20):  # wait for the fill
            o = self._req("GET", f"/v2/orders/{o['id']}")
            if o["status"] == "filled":
                break
            time.sleep(1)
        px = float(o.get("filled_avg_price") or price)
        filled = float(o.get("filled_qty") or qty)
        notional = filled * px
        fee = notional * self.fee_pct
        self.fees_paid += fee
        self.cash += -(notional + fee) if side == "buy" else (notional - fee)
        return {"symbol": symbol, "side": side, "qty": filled, "price": px, "fee": fee,
                "order_id": o["id"], "status": o["status"]}
