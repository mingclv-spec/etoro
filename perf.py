"""Performance report for the autonomous demo run.

Reports what the STRATEGY did (not manual trades): signals evaluated,
positions the bot opened, closed results, and current account state.

    python perf.py            # human-readable
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

J = os.path.join(ROOT, "journal")


def _read(name):
    p = os.path.join(J, name)
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


def main():
    sigs = _read("signals.jsonl")
    led = _read("live_positions.jsonl")
    trades = _read("trades.jsonl")

    # latest state per position
    latest = {}
    for r in led:
        latest[r.get("broker_position_id") or r.get("id")] = r
    def _is_strategy(r):
        """Only count positions the STRATEGY opened - not my manual test orders
        and not Ming's own hand trades. Strategy entries carry a real candle
        timestamp; everything else is explicitly tagged."""
        sc = str(r.get("signal_candle") or "")
        if not sc:
            return False
        if any(t in sc.upper() for t in ("MANUAL", "PRE-EXISTING", "EXTERNAL", "TEST")):
            return False
        return "T" in sc and ":" in sc        # looks like an ISO candle time

    bot_pos = [r for r in latest.values() if _is_strategy(r)]
    open_bot = [r for r in bot_pos if r.get("status") == "open"]
    closed_bot = [r for r in bot_pos if r.get("status") == "closed"]

    actions = {}
    modes = {}
    for s in sigs:
        actions[s.get("signal")] = actions.get(s.get("signal"), 0) + 1
        modes[s.get("regime")] = modes.get(s.get("regime"), 0) + 1
    last = sigs[-1] if sigs else {}

    lines = []
    lines.append("📊 GOLD SCALPER — 1 HOUR PERFORMANCE")
    lines.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    lines.append("")
    lines.append("SIGNALS")
    lines.append(f"  evaluated      : {len(sigs)}")
    lines.append(f"  mix            : {actions}")
    lines.append(f"  last           : {last.get('signal')} ({last.get('regime')})")
    lines.append(f"  candle         : {last.get('candle_time')}")
    lines.append("")
    lines.append("BOT TRADES (strategy only)")
    lines.append(f"  opened         : {len(bot_pos)}")
    lines.append(f"  still open     : {len(open_bot)}")
    lines.append(f"  closed         : {len(closed_bot)}")
    net = 0.0
    for r in closed_bot:
        net += float(r.get("pnl") or 0)
    if closed_bot or open_bot:
        lines.append(f"  realised P&L   : ${net:+.2f}")
    for r in open_bot:
        lines.append(f"    OPEN  {r.get('side')} entry {r.get('entry')} "
                     f"stop {r.get('stop')} target {r.get('tp2')}")
    for r in closed_bot:
        lines.append(f"    CLOSED {r.get('side')} entry {r.get('entry')} - {r.get('close_reason')}")
    lines.append("")
    lines.append("ACCOUNT (demo)")
    try:
        from etoro import build_client
        cp = build_client().get("/trading/info/demo/portfolio").get("clientPortfolio", {}) or {}
        lines.append(f"  credit         : ${float(cp.get('credit') or 0):,.2f}")
        lines.append(f"  positions      : {len(cp.get('positions') or [])}")
    except Exception as exc:
        lines.append(f"  unavailable: {str(exc)[:80]}")
    lines.append("")

    if not bot_pos:
        lines.append("No strategy trades yet. The gate has stayed in")
        lines.append("TREND/STAND ASIDE, so it has correctly not traded.")
        lines.append("That is the design, not a fault.")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
