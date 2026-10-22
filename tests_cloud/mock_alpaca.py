"""A fake Alpaca (data + paper trading) for testing the cloud bots offline.

  MOCK_NOW=2026-10-20T15:00:00+00:00 python tests_cloud/mock_alpaca.py 8765
Then point the bots at it with ALPACA_DATA_URL / APCA_BASE_URL = http://127.0.0.1:8765
The clock can be moved with POST /_clock {"now": "..."}.
"""
import json
import math
import random
import sys
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from lab.options import bs_price  # noqa: E402

import os
NOW = [datetime.fromisoformat(os.environ.get("MOCK_NOW", "2026-10-20T15:00:00+00:00"))]
EPOCH = NOW[0] - timedelta(days=int(os.environ.get("MOCK_EPOCH_DAYS", "14")))
START = {"BTC/USD": 64000.0, "ETH/USD": 3200.0, "SOL/USD": 150.0, "QQQ": 480.0}
SERIES = {}
ORDERS, POS = {}, {}


def series(sym):
    if sym not in SERIES:
        rng = random.Random(sym)
        n = 60 * 24 * (int(os.environ.get("MOCK_EPOCH_DAYS", "14")) + 20)
        px, out, drift, left = START.get(sym, 100.0), [], 0.0, 0
        for _ in range(n):
            if left <= 0:
                drift, left = rng.choice([-1, 0, 0, 1]) * 0.00012, rng.randint(30, 240)
            left -= 1
            o = px
            px *= math.exp(drift + 0.0009 * rng.gauss(0, 1))
            out.append((o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 100 + rng.random() * 50))
        SERIES[sym] = out
    return SERIES[sym]


def bars(sym, start, end):
    s = series(sym)
    end = min(end, NOW[0])
    t = max(start, EPOCH).replace(second=0, microsecond=0)
    out = []
    while t < end:
        k = int((t - EPOCH).total_seconds() // 60)
        if 0 <= k < len(s):
            o, h, l, c, v = s[k]
            out.append({"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": o, "h": h, "l": l, "c": c, "v": v})
        t += timedelta(minutes=1)
    return out


def last_price(sym):
    b = bars(sym, NOW[0] - timedelta(minutes=3), NOW[0])
    return b[-1]["c"]


def opt_quote(occ):
    kind = "C" if occ[9] == "C" else "P"
    K = int(occ[10:]) / 1000
    S = last_price("QQQ")
    ny = NOW[0].astimezone(timezone(timedelta(hours=-4)))
    left = max(1, (16 * 60) - (ny.hour * 60 + ny.minute))
    mid = max(0.01, bs_price(S, K, left, 0.22, kind))
    return round(mid - 0.02, 2), round(mid + 0.02, 2)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path.replace("/paper/", "/", 1))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path in ("/v1beta3/crypto/us/bars", "/v2/stocks/bars"):
            st = datetime.fromisoformat(q["start"].replace("Z", "+00:00"))
            en = datetime.fromisoformat(q["end"].replace("Z", "+00:00")) if "end" in q else NOW[0]
            return self.send({"bars": {s: bars(s, st, en) for s in q["symbols"].split(",")}, "next_page_token": None})
        if u.path == "/v1beta1/options/quotes/latest":
            s = q["symbols"]
            bp, ap = opt_quote(s)
            return self.send({"quotes": {s: {"bp": bp, "ap": ap}}})
        if u.path == "/v2/account":
            return self.send({"cash": "100000", "status": "ACTIVE"})
        if u.path.startswith("/v2/orders/"):
            return self.send(ORDERS[u.path.split("/")[-1]])
        if u.path.startswith("/v2/positions/"):
            s = u.path.split("/")[-1]
            if s not in POS:
                return self.send({"message": "position does not exist"}, 404)
            return self.send({"symbol": s, "qty": str(POS[s])})
        self.send({"message": "not found " + u.path}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path.replace("/paper/", "/", 1))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if u.path == "/_clock":
            NOW[0] = datetime.fromisoformat(body["now"])
            return self.send({"now": NOW[0].isoformat()})
        if u.path == "/v2/orders":
            sym = body["symbol"]
            if len(sym) > 12:   # option
                bp, ap = opt_quote(sym)
                px = ap if body["side"] == "buy" else bp
            else:
                px = last_price(sym)
            qty = float(body.get("qty") or float(body["notional"]) / px)
            key = sym.replace("/", "")
            POS[key] = POS.get(key, 0) + (qty if body["side"] == "buy" else -qty)
            if abs(POS[key]) < 1e-12:
                POS.pop(key)
            oid = str(uuid.uuid4())
            ORDERS[oid] = {"id": oid, "symbol": sym, "status": "filled", "filled_avg_price": str(px),
                           "filled_qty": str(qty), "side": body["side"]}
            return self.send(ORDERS[oid])
        self.send({"message": "not found"}, 404)

    def do_DELETE(self):
        self.send({})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
