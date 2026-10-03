"""Readiness + performance report - spec section 10.

Phases: historical backtest -> live signal collection (1,000+ signals) ->
paper trading -> evaluate -> small real trades.

    python report.py
    python report.py --target 1000
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from strategies.metrics import Trade, performance_metrics

ROOT = os.path.dirname(os.path.abspath(__file__))
SIGNALS_PATH = os.path.join(ROOT, "journal", "signals.jsonl")
TRADES_PATH = os.path.join(ROOT, "journal", "trades.jsonl")


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def phase_for(signal_count: int, target: int) -> str:
    if signal_count < target:
        return "PHASE 2 - live signal collection (do NOT trade real money)"
    return "PHASE 3 - paper trading / evaluation (still not real money)"


def main(argv=None):
    p = argparse.ArgumentParser(prog="report.py", description="Scalper readiness + performance")
    p.add_argument("--target", type=int, default=1000, help="signals required before paper trading")
    p.add_argument("--equity", type=float, default=5000.0)
    args = p.parse_args(argv)

    signals = load_jsonl(SIGNALS_PATH)
    trades_raw = load_jsonl(TRADES_PATH)

    print("Adaptive Gold Range Scalper v0.1 - readiness report")
    print(f"  signals collected : {len(signals)}  (target {args.target} before paper trading)")
    pct = (len(signals) / args.target * 100.0) if args.target else 0.0
    print(f"  progress          : {pct:.2f} %")
    print(f"  phase             : {phase_for(len(signals), args.target)}")
    print(f"  journal           : {SIGNALS_PATH}")

    if signals:
        from collections import Counter
        actions = Counter(s.get("signal") for s in signals)
        modes = Counter(s.get("regime") for s in signals)
        print(f"  signal mix        : {dict(actions)}")
        print(f"  regime mix        : {dict(modes)}")

    if not trades_raw:
        print("\n  no closed trades recorded yet (journal/trades.jsonl absent or empty)")
        print("  -> metrics (win rate, profit factor, drawdown) need closed trades")
        return 0

    trades = [
        Trade(
            pnl=float(t.get("pnl", 0.0)),
            entry_time=str(t.get("entry_time", "")),
            exit_time=str(t.get("exit_time", "")),
            holding_minutes=t.get("holding_minutes"),
            spread_cost=float(t.get("spread_cost", 0.0)),
            overnight_cost=float(t.get("overnight_cost", 0.0)),
        )
        for t in trades_raw
    ]

    days = args.target and trades_raw[0].get("days_span")
    m = performance_metrics(trades, starting_equity=args.equity, days_span=days)
    print("\nPERFORMANCE")
    for k, v in m.items():
        print(f"  {k:34s}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
