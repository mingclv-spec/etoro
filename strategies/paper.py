"""Paper trading ledger - simulated fills for TRADING_MODE=paper.

No order ever leaves this module. It records what WOULD have happened so the
signal set can be evaluated (spec section 10) before any real money moves.

Supports PARTIAL exits, which the strategy needs: TP1 takes 50% of the
position and TP2 takes the remainder.

Writes:
  journal/paper_positions.jsonl - append-only position events (latest per id wins)
  journal/trades.jsonl          - closed (or partially closed) trades for metrics
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(ROOT, "journal")
POSITIONS_PATH = os.path.join(JOURNAL, "paper_positions.jsonl")
TRADES_PATH = os.path.join(JOURNAL, "trades.jsonl")


def _now():
    return datetime.now(timezone.utc)


def _append(path, record):
    os.makedirs(JOURNAL, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _read(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def latest_positions():
    latest = {}
    for r in _read(POSITIONS_PATH):
        latest[r["id"]] = r
    return latest


def open_positions():
    return [r for r in latest_positions().values()
            if r.get("status") == "open" and r.get("remaining_portion", 0) > 1e-9]


def open_position(symbol, side, notional, entry, stop=None, tp1=None, tp2=None,
                  leverage=20, spread_pct=0.0, note="", signal_candle=None):
    rec = {
        "id": str(uuid.uuid4()),
        "status": "open",
        "symbol": symbol,
        "side": side.upper(),
        "notional": float(notional),
        "leverage": int(leverage),
        "entry": float(entry),
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "spread_pct": float(spread_pct),
        "remaining_portion": 1.0,
        "tp1_done": False,
        "opened_at": _now().isoformat(),
        "signal_candle": signal_candle,
        "note": note,
    }
    _append(POSITIONS_PATH, rec)
    return rec


def _trade_row(pos, portion, closed_notional, exit_price, reason, gross, spread_cost, pnl, opened_at):
    closed_at = _now()
    holding = (closed_at - datetime.fromisoformat(opened_at)).total_seconds() / 60.0
    _append(TRADES_PATH, {
        "id": pos["id"], "symbol": pos["symbol"], "side": pos["side"],
        "portion": round(portion, 4), "closed_notional": round(closed_notional, 2),
        "leverage": pos["leverage"], "entry": pos["entry"], "exit": float(exit_price),
        "close_reason": reason, "gross_pnl": round(gross, 4),
        "spread_cost": round(spread_cost, 4), "overnight_cost": 0.0,
        "pnl": round(pnl, 4), "entry_time": opened_at,
        "exit_time": closed_at.isoformat(), "holding_minutes": round(holding, 2),
    })
    return round(holding, 2)


def close_partial(position_id, portion, exit_price, reason="tp1"):
    """Close `portion` of the REMAINING position. portion is 0..1."""
    pos = latest_positions().get(position_id)
    if pos is None:
        return {"error": f"position {position_id} not found"}
    if pos.get("status") != "open":
        return {"error": f"position {position_id} is already {pos.get('status')}"}

    remaining = float(pos.get("remaining_portion", 1.0))
    portion = max(0.0, min(float(portion), remaining))
    if portion <= 0:
        return {"error": "portion is zero"}

    closed_notional = pos["notional"] * portion
    direction = 1.0 if pos["side"] == "BUY" else -1.0
    gross = closed_notional * (float(exit_price) - pos["entry"]) / pos["entry"] * direction
    spread_cost = closed_notional * float(pos.get("spread_pct", 0.0)) / 100.0
    pnl = gross - spread_cost

    holding = _trade_row(pos, portion, closed_notional, exit_price, reason,
                         gross, spread_cost, pnl, pos["opened_at"])

    updated = dict(pos)
    updated["remaining_portion"] = remaining - portion
    updated["last_exit_reason"] = reason
    updated["last_exit_price"] = float(exit_price)
    if reason == "tp1":
        updated["tp1_done"] = True
    if updated["remaining_portion"] <= 1e-9:
        updated["status"] = "closed"
        updated["closed_at"] = _now().isoformat()
    _append(POSITIONS_PATH, updated)

    return {
        "id": position_id, "closed_portion": round(portion, 4),
        "closed_notional": round(closed_notional, 2), "exit": float(exit_price),
        "reason": reason, "gross_pnl": round(gross, 4),
        "spread_cost": round(spread_cost, 4), "pnl": round(pnl, 4),
        "remaining_portion": round(updated["remaining_portion"], 4),
        "status": updated["status"], "holding_minutes": holding,
    }


def close_position(position_id, exit_price, reason="manual"):
    pos = latest_positions().get(position_id)
    if pos is None:
        return {"error": f"position {position_id} not found"}
    return close_partial(position_id, pos.get("remaining_portion", 1.0), exit_price, reason)


def closed_trades():
    return _read(TRADES_PATH)
