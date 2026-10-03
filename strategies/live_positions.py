"""Live position ledger - tracks REAL positions the agent opened.

Mirrors strategies/paper.py but for live trades, keyed by the broker's
positionId so reconciliation and the position monitor can both find them.

Opens are only ever recorded AFTER the broker confirms a fill.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(ROOT, "journal")
POSITIONS_PATH = os.path.join(JOURNAL, "live_positions.jsonl")


def _now():
    return datetime.now(timezone.utc)


def _append(record):
    os.makedirs(JOURNAL, exist_ok=True)
    with open(POSITIONS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _read():
    if not os.path.exists(POSITIONS_PATH):
        return []
    rows = []
    with open(POSITIONS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def latest():
    out = {}
    for r in _read():
        out[r["id"]] = r
    return out


def open_positions():
    return [r for r in latest().values()
            if r.get("status") == "open" and r.get("remaining_portion", 0) > 1e-9]


def record_open(broker_position_id, symbol, side, notional, entry, stop,
                tp1=None, tp2=None, leverage=20, units=None,
                stop_pct=None, signal_candle=None, instrument_id=18):
    rec = {
        "id": str(uuid.uuid4()),
        "status": "open",
        "mode": "live",
        "broker_position_id": broker_position_id,
        "instrument_id": int(instrument_id),
        "symbol": symbol,
        "side": side.upper(),
        "notional": float(notional),
        "entry": float(entry),
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "leverage": int(leverage),
        "units": units,
        "stop_pct": stop_pct,
        "remaining_portion": 1.0,
        "tp1_done": False,
        "opened_at": _now().isoformat(),
        "signal_candle": signal_candle,
    }
    _append(rec)
    return rec


def mark(position_id, **fields):
    pos = latest().get(position_id)
    if pos is None:
        return None
    updated = dict(pos)
    updated.update(fields)
    _append(updated)
    return updated


def close(position_id, reason, pnl=None, exit_price=None):
    for r in _read():
        if r["id"] == position_id and r.get("status") == "open":
            r["remaining_portion"] = 0.0
            break
    return mark(position_id, status="closed", close_reason=reason,
                closed_at=_now().isoformat(), pnl=pnl, exit=exit_price)
