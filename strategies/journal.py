"""Trade journal - append every signal as JSONL (spec section 9).

Every evaluation is stored, including WAIT/STAND_DOWN, so the history is
complete and the strategy can be reviewed later.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal", "signals.jsonl")


def build_record(report: dict, extra: Optional[dict] = None) -> dict:
    """Flatten a scalper report into the spec's journal schema."""
    g = report.get("regime", {}) or {}
    r = report.get("range", {}) or {}
    s = report.get("signal", {}) or {}
    b = report.get("breakout", {}) or {}

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": report.get("symbol"),
        "instrument_id": report.get("instrument_id"),
        "price": g.get("price"),
        "range_high": r.get("range_high"),
        "range_low": r.get("range_low"),
        "range_width_pct": r.get("width_pct"),
        "touches_high": r.get("touches_resistance"),
        "touches_low": r.get("touches_support"),
        "adx": round(g["adx14"], 2) if g.get("adx14") is not None else None,
        "rsi": s.get("rsi14"),
        "atr": round(g["atr14"], 3) if g.get("atr14") is not None else None,
        "regime": report.get("mode"),
        "signal": s.get("action"),
        "reason": s.get("reason"),
        "zone_position_pct": s.get("zone_position_pct"),
        "entry": s.get("entry"),
        "stop": s.get("stop"),
        "tp1": s.get("tp1"),
        "tp2": s.get("tp2"),
        "breakout_direction": b.get("direction"),
        "gates": report.get("gates"),
        "context": report.get("context"),
    }
    if extra:
        record.update(extra)
    return record


def log_signal(report: dict, path: str = DEFAULT_PATH, extra: Optional[dict] = None) -> str:
    """Append one record. Returns the path written."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = build_record(report, extra=extra)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return path
