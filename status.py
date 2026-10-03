"""Status check-in for the gold scalper paper run."""
from __future__ import annotations
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

J = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal")


def load(path):
    p = os.path.join(J, path)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


sigs = load("signals.jsonl")
trades = load("trades.jsonl")
pos_events = load("paper_positions.jsonl")

latest = {}
for r in pos_events:
    latest[r["id"]] = r
openpos = [r for r in latest.values() if r.get("status") == "open" and r.get("remaining_portion", 0) > 1e-9]

net = sum(t.get("pnl", 0) for t in trades)
last = sigs[-1] if sigs else {}

lines = ["📊 GOLD SCALPER — 1 HOUR CHECK-IN", ""]
lines.append(f"Signals collected : {len(sigs)} / 1000")
lines.append(f"Open paper pos    : {len(openpos)}")
lines.append(f"Closed fills      : {len(trades)}")
lines.append(f"Paper P&L         : ${net:+.2f}")
if last:
    lines.append("")
    lines.append(f"Last signal : {last.get('signal')} ({last.get('regime')})")
    lines.append(f"Candle      : {last.get('candle_time')}")
    r = last.get("range_low"), last.get("range_high")
    if r[0]:
        lines.append(f"Range       : {r[0]} – {r[1]}")
lines.append("")
if not trades and not openpos:
    lines.append("No trades taken. The gate requires RANGE mode and")
    lines.append("both boundaries touched 2x — it has not qualified yet.")
    lines.append("This is the strategy correctly standing aside, not an error.")
lines.append("")
lines.append("Still PAPER. No real orders.")
print("\n".join(lines))
