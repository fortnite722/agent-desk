"""Check the lab's option MODEL against REAL historical 0DTE option prices.

Run after `pattern_lab.py --source alpaca`:
  python validate.py            # checks the top survivor's last 25 test trades

For each trade it downloads that day's 1-minute bars for the exact contract
(Alpaca has option history from Feb 2024) and compares the modeled entry/exit
premium with the real one. If the model is off by more than ~15%, trust the
lab's dollar numbers less (direction-of-edge still tends to hold).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from lab.engine import Simulator, Variant, split
from lab.market import NY, build_days, load_bars
from lab.options import occ_symbol


def option_bars(symbol, day):
    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    st = datetime(day.year, day.month, day.day, 13, 0, tzinfo=timezone.utc)
    en = datetime(day.year, day.month, day.day, 21, 0, tzinfo=timezone.utc)
    q = {"symbols": symbol, "timeframe": "1Min", "start": st.isoformat(), "end": en.isoformat(), "limit": 1000}
    req = urllib.request.Request(os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets") + "/v1beta1/options/bars?" + urllib.parse.urlencode(q),
                                 headers={"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": sec or ""})
    with urllib.request.urlopen(req, timeout=30) as r:
        js = json.loads(r.read())
    out = {}
    for b in (js.get("bars") or {}).get(symbol, []):
        ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY)
        out[(ts.hour - 9) * 60 + ts.minute - 30] = b
    return out


def nearest(bars, minute, field):
    for d in range(0, 4):
        for m in (minute - d, minute + d):
            if m in bars:
                return bars[m][field]
    return None


def main():
    rep = json.load(open("lab_out/report.json"))
    if not rep["survivors"]:
        raise SystemExit("No survivors in lab_out/report.json - nothing to validate.")
    v = Variant(**rep["survivors"][0]["variant"])
    print("validating:", v.label())
    days = build_days(load_bars("alpaca"))
    cut = int(len(days) * 0.6)
    sim = Simulator(days)
    _, test = split(sim.run(v), cut)
    errs = []
    for t in test[-25:]:
        d = days[t["di"]]
        mid0 = sim.path(t["di"], t["i"], t["kind"], v.otm)[0]
        sym = occ_symbol("QQQ", d.date, t["kind"], t["K"])
        try:
            bars = option_bars(sym, d.date)
        except Exception as e:
            print(f"  {d.date} {sym}: fetch failed ({e})"); continue
        real_in = nearest(bars, d.minute[t["i"] + 1], "o")
        if real_in is None:
            print(f"  {d.date} {sym}: no real bars"); continue
        err = (mid0 - real_in) / real_in
        errs.append(err)
        print(f"  {d.date} {sym}: model ${mid0:.2f} vs real ${real_in:.2f}  ({err:+.0%})")
    if errs:
        avg = sum(abs(e) for e in errs) / len(errs)
        bias = sum(errs) / len(errs)
        print(f"\naverage model error {avg:.0%}, bias {bias:+.0%} "
              f"({'model prices too HIGH: real trades were cheaper' if bias > 0 else 'model prices too LOW: real trades cost more'})")
        print("Tip: adjust iv_mult / iv_add in lab/options.py until bias is near 0, then rerun the lab.")


if __name__ == "__main__":
    main()
