"""Notification queue - formatted alerts for Telegram.

The script NEVER talks to Telegram directly (no bot token here, ever). It
appends formatted messages to a queue file; an OpenClaw automation job drains
the queue and delivers via the Telegram channel. Credentials never leave
OpenClaw.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_PATH = os.path.join(ROOT, "journal", "notifications.jsonl")


def _price(v) -> str:
    if v is None:
        return "n/a"
    return f"${int(v):,}" if float(v) == int(v) else f"${float(v):,.2f}"


def _money(v) -> str:
    if v is None:
        return "n/a"
    return f"${float(v):,.2f}"


def range_detected(range_low, range_high, price, adx, rsi, signal,
                   entry=None, stop=None, tp=None) -> str:
    lines = [
        "🟢 GOLD RANGE DETECTED",
        "",
        f"Range: {_price(range_low)} – {_price(range_high)}",
        f"Current: {_price(price)}",
        f"ADX: {adx:.1f}" if adx is not None else "ADX: n/a",
        f"RSI: {rsi:.1f}" if rsi is not None else "RSI: n/a",
        f"Signal: {signal}",
    ]
    if entry is not None:
        lines.append(f"Entry: {_price(entry)}")
    if stop is not None:
        lines.append(f"SL: {_price(stop)}")
    if tp is not None:
        lines.append(f"TP: {_price(tp)}")
    return "\n".join(lines)


def order_executed(side, notional, leverage, entry, stop=None, tp=None) -> str:
    lines = [
        "🟢 ORDER EXECUTED",
        "",
        f"XAUUSD {side}",
        f"Position: {_money(notional)}",
        f"Leverage: {leverage}×",
        f"Entry: {_price(entry)}",
    ]
    if stop is not None:
        lines.append(f"SL: {_price(stop)}")
    if tp is not None:
        lines.append(f"TP: {_price(tp)}")
    return "\n".join(lines)


def emergency(message, existing_position=None) -> str:
    lines = [
        "🔴 EMERGENCY",
        "",
        message,
        "New trades disabled.",
    ]
    if existing_position:
        lines.append(f"Existing position: {existing_position}")
    lines.append("Agent attempting reconnection...")
    return "\n".join(lines)


def notify(text: str, kind: str = "info", **meta) -> str:
    """Append a message to the queue. Returns its id."""
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    record = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "text": text,
        "sent": False,
    }
    if meta:
        record["meta"] = meta
    with open(QUEUE_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record["id"]


def _read_all():
    if not os.path.exists(QUEUE_PATH):
        return []
    rows = []
    with open(QUEUE_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def drain():
    """Return unsent notifications and mark them sent."""
    rows = _read_all()
    pending = [r for r in rows if not r.get("sent")]
    if not pending:
        return []
    for r in rows:
        r["sent"] = True
    tmp = QUEUE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, QUEUE_PATH)
    return pending
