"""Signal collector - the deduped journal writer for a frequent scheduler.

Why this exists: the strategy runs on 15-minute candles. If a scheduler
invokes the scalper every minute, the last candle is still FORMING, so the
same signal would be appended ~15 times per candle and your "1,000 signals"
count would be meaningless.

This wrapper evaluates as often as you like but only writes ONE journal row
per new candle. It also means a closed market (no new candle) writes nothing,
so no separate market-hours logic is needed.

    python collect.py            # write only if a new candle has appeared
    python collect.py --force    # ignore the dedup guard (manual inspection)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from etoro import EtoroError, build_client
from strategies.gold_range_scalper import ScalperConfig, analyze
from strategies.indicators import to_candles
from strategies.journal import DEFAULT_PATH as JOURNAL_PATH, log_signal

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "journal", "collector_state.json")
INSTRUMENTS = {"GOLD": 559, "GLD": 3025}
CANDLES_1H = 220
CANDLES_15M = 320


def fetch_candles(client, instrument_id, interval, count):
    path = f"/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"
    payload = client.get(path)
    groups = payload.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    return to_candles(rows)[::-1]


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def main(argv=None):
    p = argparse.ArgumentParser(prog="collect.py", description="Deduped signal collector")
    p.add_argument("--symbol", default="GOLD")
    p.add_argument("--force", action="store_true", help="ignore the dedup guard")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    if symbol not in INSTRUMENTS:
        print(f"unknown symbol {symbol}", file=sys.stderr)
        return 2
    iid = INSTRUMENTS[symbol]

    try:
        client = build_client()
        h1 = fetch_candles(client, iid, "OneHour", CANDLES_1H)
        m15 = fetch_candles(client, iid, "FifteenMinutes", CANDLES_15M)
    except EtoroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if len(m15) < 2 or len(h1) < 2:
        print("not enough candles", file=sys.stderr)
        return 1

    # Evaluate on the last CLOSED candle so signals cannot repaint.
    m15_closed, h1_closed = m15[:-1], h1[:-1]
    latest_candle = m15_closed[-1].time
    state = load_state()
    previous = (state.get(symbol) or {}).get("last_candle")

    if latest_candle == previous and not args.force:
        # Same candle still forming - nothing new to record.
        print("NO_REPLY")
        return 0

    report = analyze(h1_closed, m15_closed, ScalperConfig())
    report["symbol"] = symbol
    try:
        from strategies import market_context
        report["context"] = market_context.snapshot(client)
    except Exception as exc:
        report["context"] = {"error": str(exc)[:160]}
    report["instrument_id"] = iid
    path = log_signal(report, extra={"candle_time": latest_candle, "source": "collect.py"})

    state[symbol] = {
        "last_candle": latest_candle,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    s = report.get("signal", {})
    print(f"recorded mode={report.get('mode')} signal={s.get('action')} candle={latest_candle} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
