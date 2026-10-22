"""Where bars come from: synthetic practice data, a CSV, or Alpaca's market data."""
from __future__ import annotations

import csv
import json
import math
import os
import random
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

from .agents import Bar

START_PRICES = {"BTC/USD": 64000.0, "ETH/USD": 3200.0, "SOL/USD": 150.0,
                "SPY": 560.0, "QQQ": 480.0, "IWM": 215.0}


def synthetic(symbols, days=5, minutes_per_day=None, seed=7, start=None, crypto=True):
    """Regime-switching random walk: quiet chop, trends, and volatility spikes.

    It is random by construction, so a strategy has NO real edge on it. That makes
    it a good honesty test: if the desk 'prints money' here, something is broken.
    Returns {symbol: [Bar, ...]}.
    """
    rng = random.Random(seed)
    minutes_per_day = minutes_per_day or (1440 if crypto else 390)
    start = start or (datetime(2026, 10, 5, 0, 0) if crypto else datetime(2026, 10, 5, 9, 30))
    out = {}
    for s in symbols:
        px = START_PRICES.get(s, 100.0) * (1 + rng.uniform(-0.02, 0.02))
        bars = []
        drift, vol, regime_left = 0.0, 0.0008, 0
        for d in range(days):
            day0 = start + timedelta(days=d)
            for k in range(minutes_per_day):
                if regime_left <= 0:
                    kind = rng.choices(["chop", "trend", "spike"], [0.55, 0.35, 0.10])[0]
                    vol = {"chop": 0.0006, "trend": 0.0008, "spike": 0.0022}[kind] * (1.6 if crypto else 1)
                    drift = rng.choice([-1, 1]) * (0.00025 if kind == "trend" else 0.0) * (1.4 if crypto else 1)
                    regime_left = rng.randint(20, 90)
                regime_left -= 1
                ret = drift + vol * rng.gauss(0, 1)
                o = px
                c = px * math.exp(ret)
                wick = abs(rng.gauss(0, vol * 0.6))
                h = max(o, c) * (1 + wick)
                l = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.6)))
                u = 1.6 - math.sin(math.pi * k / minutes_per_day)  # U-shaped volume
                v = max(1.0, rng.lognormvariate(math.log(1000 * u * (1 + 40 * abs(ret))), 0.35))
                bars.append(Bar(day0 + timedelta(minutes=k), o, h, l, c, v))
                px = c
        out[s] = bars
    return out


def from_csv(path):
    """CSV with columns: timestamp,open,high,low,close,volume (ISO timestamps)."""
    bars = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
            bars.append(Bar(ts, float(r["open"]), float(r["high"]), float(r["low"]),
                            float(r["close"]), float(r["volume"])))
    return bars


def alpaca_bars(symbols, start: datetime, end: datetime | None = None, timeframe="1Min"):
    """Fetch 1-minute bars from Alpaca. Crypto needs no keys; stocks need paper keys."""
    out = {s: [] for s in symbols}
    crypto = [s for s in symbols if "/" in s]
    stocks = [s for s in symbols if "/" not in s]
    params = {"timeframe": timeframe, "start": start.astimezone(timezone.utc).isoformat(),
              "limit": 10000}
    if end:
        params["end"] = end.astimezone(timezone.utc).isoformat()
    headers = {}
    if os.environ.get("APCA_API_KEY_ID"):
        headers = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
                   "APCA-API-SECRET-KEY": os.environ.get("APCA_API_SECRET_KEY", "")}
    jobs = []
    if crypto:
        jobs.append((os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets") + "/v1beta3/crypto/us/bars", crypto, {}))
    if stocks:
        jobs.append((os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets") + "/v2/stocks/bars", stocks, {"feed": "iex"}))
    for url, syms, extra in jobs:
        token = None
        while True:
            q = {**params, **extra, "symbols": ",".join(syms)}
            if token:
                q["page_token"] = token
            req = urllib.request.Request(url + "?" + urllib.parse.urlencode(q), headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                js = json.loads(r.read())
            for s, rows in (js.get("bars") or {}).items():
                for b in rows:
                    ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                    if "/" not in s:
                        ts = ts.astimezone(NY)  # stocks: market clock (VWAP resets, 3:55 flatten)
                    out[s].append(Bar(ts, b["o"], b["h"], b["l"], b["c"], b["v"]))
            token = js.get("next_page_token")
            if not token:
                break
    return out


def aggregate(bars, minutes):
    """Roll 1-minute bars up into N-minute bars."""
    if minutes <= 1:
        return bars
    out, cur, key = [], None, None
    for b in bars:
        k = b.ts.replace(minute=(b.ts.minute // minutes) * minutes, second=0, microsecond=0) \
            if minutes < 60 else b.ts.replace(minute=0, second=0, microsecond=0)
        if k != key:
            if cur:
                out.append(cur)
            key = k
            cur = Bar(k, b.open, b.high, b.low, b.close, b.volume)
        else:
            cur.high, cur.low = max(cur.high, b.high), min(cur.low, b.low)
            cur.close = b.close
            cur.volume += b.volume
    if cur:
        out.append(cur)
    return out


def merge_by_time(bars_by_symbol):
    """Interleave symbols in time order so the desk sees the market like it happens."""
    rows = [(b.ts, s, b) for s, bars in bars_by_symbol.items() for b in bars]
    rows.sort(key=lambda r: (r[0], r[1]))
    return [(s, b) for _, s, b in rows]
