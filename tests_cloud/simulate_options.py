"""Run the QQQ options bot through simulated trading days against the fake Alpaca.

  python tests_cloud/simulate_options.py --days 3
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
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    work = tempfile.mkdtemp(prefix="opt_sim_")
    shutil.copytree(ROOT, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("state", "docs", ".git", "__pycache__"))
    start = datetime(2026, 10, 20, 13, 25, tzinfo=timezone.utc)  # 9:25 ET (EDT)
    os.environ.update(MOCK_NOW=(start + timedelta(days=a.days)).isoformat(),
                      NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
                      ALPACA_DATA_URL=f"http://127.0.0.1:{a.port}", APCA_BASE_URL=f"http://127.0.0.1:{a.port}/paper",
                      APCA_API_KEY_ID="test", APCA_API_SECRET_KEY="test")
    srv = subprocess.Popen([sys.executable, "tests_cloud/mock_alpaca.py", str(a.port)], cwd=work)
    time.sleep(1.5)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    sys.path.insert(0, work)
    os.chdir(work)
    import options_paper as ob
    from lab.market import NY
    try:
        v, status = ob.pick_setup()
        bot = ob.Bot(v, status)
        for d in range(a.days):
            day0 = start + timedelta(days=d)
            if day0.astimezone(NY).weekday() >= 5:
                continue
            for m in range(0, 375):
                now = day0 + timedelta(minutes=m, seconds=5)
                opener.open(urllib.request.Request(f"http://127.0.0.1:{a.port}/_clock", method="POST",
                                                   data=json.dumps({"now": now.isoformat()}).encode())).read()
                if bot.tick(now.astimezone(NY)) is None:
                    break
            bot.dashboard(now.astimezone(NY))
        js = json.load(open(os.path.join(work, "docs", "options.json")))
        print("\nsetup:", js["setup"], "|", js["status"])
        print("totals:", js["total"])
        for t in js["trades"][-10:]:
            print("  ", t["date"], t["symbol"], t["side"], t["entry"], "->", t["exit"], t["why"], t["pnl"])
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
