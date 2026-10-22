"""Load QQQ 1-minute bars and precompute everything the setups need, day by day."""
from __future__ import annotations

import csv
import math
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from desk.data import NY, alpaca_bars, synthetic

ANNUAL = math.sqrt(390 * 252)


@dataclass
class Day:
    date: object
    ts: list = field(default_factory=list)
    o: list = field(default_factory=list)
    h: list = field(default_factory=list)
    l: list = field(default_factory=list)
    c: list = field(default_factory=list)
    v: list = field(default_factory=list)
    vwap: list = field(default_factory=list)
    e9: list = field(default_factory=list)
    e21: list = field(default_factory=list)
    e50: list = field(default_factory=list)
    rsi: list = field(default_factory=list)
    atr: list = field(default_factory=list)
    rv: list = field(default_factory=list)       # annualized realized vol, trailing
    minute: list = field(default_factory=list)   # minutes since 9:30
    prev_close: float | None = None
    orh: dict = field(default_factory=dict)      # opening range high by length
    orl: dict = field(default_factory=dict)


def load_bars(source: str, start: str | None = None, cache: str = "data/QQQ_1min.csv",
              days: int = 250, seed: int = 11, feed: str = "sip"):
    """source = 'synthetic' | 'alpaca' | path/to/file.csv  -> list of (ts, o, h, l, c, v) in NY time."""
    if source == "randomwalk":  # pure coin-flip market: no trends, no patterns. Lab must find NOTHING.
        import random
        rng = random.Random(seed)
        rows, px = [], 480.0
        for dd in range(days):
            day0 = datetime(2025, 1, 6, 9, 30) + timedelta(days=dd)
            for k in range(390):
                o = px
                px *= math.exp(0.0008 * rng.gauss(0, 1))
                h = max(o, px) * (1 + abs(rng.gauss(0, 0.0003)))
                l = min(o, px) * (1 - abs(rng.gauss(0, 0.0003)))
                rows.append((day0 + timedelta(minutes=k), o, h, l, px, 1000 + rng.random() * 500))
        return rows
    if source == "synthetic":
        bars = synthetic(["QQQ"], days=days, seed=seed, crypto=False)["QQQ"]
        return [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]
    if source == "alpaca":
        if os.path.exists(cache):
            rows = read_csv(cache)
            last = rows[-1][0].astimezone(timezone.utc) + timedelta(minutes=1)
            end = datetime.now(timezone.utc) - timedelta(minutes=20)
            if end - last > timedelta(hours=1):
                print(f"cache has data to {rows[-1][0]:%Y-%m-%d %H:%M}; fetching newer bars...")
                cur = last
                while cur < end:
                    nxt = min(end, cur + timedelta(days=30))
                    rows += _fetch_chunk(cur, nxt, feed)
                    cur = nxt
                keep_from = datetime.now(NY) - timedelta(days=int(days * 1.45))
                rows = [r for r in rows if r[0] >= keep_from]
                write_csv(cache, rows)
            return rows
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        st = datetime.fromisoformat(start) if start else datetime.now(timezone.utc) - timedelta(days=int(days * 1.45))
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        end = datetime.now(timezone.utc) - timedelta(minutes=20)  # free plan: no last-15-min SIP
        print(f"downloading QQQ 1-min bars {st:%Y-%m-%d} -> today (this can take a few minutes)...")
        rows = []
        cur = st
        while cur < end:  # month chunks keep each request small
            nxt = min(end, cur + timedelta(days=30))
            got = _fetch_chunk(cur, nxt, feed)
            rows += got
            print(f"  {cur:%Y-%m-%d}: {len(got)} bars")
            cur = nxt
        write_csv(cache, rows)
        return rows
    return read_csv(source)


def _fetch_chunk(st, en, feed):
    import json, urllib.parse, urllib.request
    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key:
        raise SystemExit("Stock data needs your Alpaca paper keys: set APCA_API_KEY_ID / APCA_API_SECRET_KEY")
    out, token = [], None
    while True:
        q = {"symbols": "QQQ", "timeframe": "1Min", "start": st.isoformat(), "end": en.isoformat(),
             "limit": 10000, "feed": feed, "adjustment": "all"}
        if token:
            q["page_token"] = token
        req = urllib.request.Request(os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets") + "/v2/stocks/bars?" + urllib.parse.urlencode(q),
                                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                js = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if feed == "sip" and e.code in (403, 422):
                print("  SIP feed not allowed on this plan, falling back to IEX")
                return _fetch_chunk(st, en, "iex")
            raise
        for b in (js.get("bars") or {}).get("QQQ", []):
            ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY)
            out.append((ts, b["o"], b["h"], b["l"], b["c"], b["v"]))
        token = js.get("next_page_token")
        if not token:
            return out


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r[0].isoformat(), *r[1:]])


def read_csv(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = datetime.fromisoformat(r["timestamp"])
            if ts.tzinfo is not None:
                ts = ts.astimezone(NY)
            rows.append((ts, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"])))
    return rows


def build_days(rows, or_lengths=(5, 15, 30), min_bars=300) -> list[Day]:
    """Regular session only (9:30-15:59 ET). Indicators carry across days where that makes sense."""
    days: list[Day] = []
    k9, k21, k50 = 2 / 10, 2 / 22, 2 / 51
    e9 = e21 = e50 = None
    gains = deque(maxlen=14); losses = deque(maxlen=14)
    trs = deque(maxlen=14); rets = deque(maxlen=60)
    prev_c = None
    cur = None
    for ts, o, h, l, c, v in rows:
        m = (ts.hour - 9) * 60 + ts.minute - 30
        if m < 0 or m >= 390:
            continue
        if cur is None or ts.date() != cur.date:
            if cur and len(cur.c) > min_bars:
                days.append(cur)
            cur = Day(ts.date(), prev_close=prev_c)
            pv = vv = 0.0
        # indicators
        e9 = c if e9 is None else e9 + k9 * (c - e9)
        e21 = c if e21 is None else e21 + k21 * (c - e21)
        e50 = c if e50 is None else e50 + k50 * (c - e50)
        if prev_c:
            ch = c - prev_c
            gains.append(max(ch, 0)); losses.append(max(-ch, 0))
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
            rets.append(math.log(c / prev_c))
        else:
            trs.append(h - l)
        ag, al = (sum(gains) / len(gains) if gains else 0), (sum(losses) / len(losses) if losses else 0)
        rsi = 50.0 if not gains else (100.0 if al == 0 else 100 - 100 / (1 + ag / al))
        tp = (h + l + c) / 3
        pv += tp * v; vv += v
        if len(rets) > 10:
            mu = sum(rets) / len(rets)
            rv = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets)) * ANNUAL
        else:
            rv = 0.2
        for arr, val in ((cur.ts, ts), (cur.o, o), (cur.h, h), (cur.l, l), (cur.c, c), (cur.v, v),
                         (cur.vwap, pv / vv if vv else c), (cur.e9, e9), (cur.e21, e21), (cur.e50, e50),
                         (cur.rsi, rsi), (cur.atr, sum(trs) / len(trs)), (cur.rv, rv), (cur.minute, m)):
            arr.append(val)
        prev_c = c
    if cur and len(cur.c) > min_bars:
        days.append(cur)
    for d in days:
        for n in or_lengths:
            first = [i for i, mm in enumerate(d.minute) if mm < n]
            if first:
                d.orh[n] = max(d.h[i] for i in first)
                d.orl[n] = min(d.l[i] for i in first)
    return days
