"""Run the crypto cloud tick against the fake Alpaca for N simulated days.

  python tests_cloud/simulate_cloud.py --days 3
Uses a temp copy of the repo so your real state/ is untouched.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    work = tempfile.mkdtemp(prefix="desk_sim_")
    shutil.copytree(ROOT, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("state", "docs", ".git", "__pycache__"))
    t0 = datetime.fromisoformat("2026-10-20T15:00:00+00:00")
    env = dict(os.environ, MOCK_NOW=t0.isoformat(), NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
               ALPACA_DATA_URL=f"http://127.0.0.1:{a.port}", APCA_BASE_URL=f"http://127.0.0.1:{a.port}/paper",
               APCA_API_KEY_ID="test", APCA_API_SECRET_KEY="test")
    srv = subprocess.Popen([sys.executable, "tests_cloud/mock_alpaca.py", str(a.port)], cwd=work, env=env)
    time.sleep(1.5)
    try:
        steps = int(a.days * 24 * 4)
        for k in range(steps):
            now = t0 + timedelta(minutes=15 * k + 1)
            req = urllib.request.Request(f"http://127.0.0.1:{a.port}/_clock", method="POST",
                                         data=json.dumps({"now": now.isoformat()}).encode())
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req).read()
            r = subprocess.run([sys.executable, "cloud/crypto_step.py"], cwd=work,
                               env=dict(env, DESK_NOW=now.isoformat()), capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout, r.stderr)
                raise SystemExit(f"tick {k} failed")
            if k % 24 == 0 or k == steps - 1:
                print(f"tick {k:4d} {now:%m/%d %H:%M}Z  {r.stdout.strip()}")
        js = json.load(open(os.path.join(work, "docs", "crypto.json")))
        print("\nscoreboard:", {k: js["scoreboard"][k] for k in ("trades", "wins", "losses", "net_pnl", "end_equity")})
        print("last events:")
        for e in js["events"][-8:]:
            print("  ", e["t"][5:16], e["sym"], e["agent"], e["msg"][:90])
        print("\nworkdir:", work)
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
