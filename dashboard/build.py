"""Bake a replay JSON into the Desk City page.

  python backtest.py --replay dashboard/replay.json
  python dashboard/build.py dashboard/replay.json dashboard/desk_city.html
Then open desk_city.html in any browser.
"""
import json
import pathlib
import sys

here = pathlib.Path(__file__).parent
src = sys.argv[1] if len(sys.argv) > 1 else here / "replay.json"
out = sys.argv[2] if len(sys.argv) > 2 else here / "desk_city.html"
data = json.loads(pathlib.Path(src).read_text())
data["config"] = {"timeframe_min": data.get("config", {}).get("timeframe_min", 15)}
html = (here / "template.html").read_text().replace(
    "/*REPLAY*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
pathlib.Path(out).write_text(html)
print(f"wrote {out} ({len(html) // 1024} KB)")
