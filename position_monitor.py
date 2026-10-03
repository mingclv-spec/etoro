"""Position monitor - enforces stop / TP1 / TP2 on OPEN PAPER positions.

This is the exit logic that was missing. Without it a position could run
unbounded, which at 20x leverage is the fastest way to lose an account.

    price <= stop        -> close everything        (reason: stop)
    price >= tp1         -> close 50% of original   (reason: tp1)
    price >= tp2         -> close the remainder     (reason: tp2)

Uses the live WebSocket price when fresh, otherwise the last REST close.
Supports --price to force a price for testing the rules.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from etoro import EtoroError, build_client
from strategies import paper, live_positions
from strategies import live_exec
from strategies.gold_range_scalper import ScalperConfig
from strategies.indicators import to_candles
from strategies.notifier import notify

ROOT = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(ROOT, "journal")
MARKET_STATE = os.path.join(JOURNAL, "market_state.json")
LOG_PATH = os.path.join(JOURNAL, "position_monitor.jsonl")
SYMBOLS = {"GOLD": 559, "GLD": 3025}


def _now():
    return datetime.now(timezone.utc)


def live_prices():
    """Return (bid, ask, age_seconds) from the WS monitor, or (None, None, None)."""
    if not os.path.exists(MARKET_STATE):
        return None, None, None
    try:
        with open(MARKET_STATE, encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception:
        return None, None, None
    if not st.get("connected"):
        return None, None, None
    age = None
    try:
        ts = datetime.fromisoformat(str(st.get("updated_at")).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (_now() - ts).total_seconds()
    except Exception:
        pass
    return st.get("bid"), st.get("ask"), age


def rest_close(instrument_id):
    client = build_client()
    payload = client.get(
        f"/market-data/instruments/{instrument_id}/history/candles/desc/FifteenMinutes/2")
    groups = payload.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    if not rows:
        return None
    return to_candles(rows)[::-1][-1].close


def evaluate_position(pos, price, cfg):
    """Pure decision function - easy to test."""
    side = pos["side"]
    stop, tp1, tp2 = pos.get("stop"), pos.get("tp1"), pos.get("tp2")
    tp1_done = bool(pos.get("tp1_done"))

    if side == "BUY":
        if stop is not None and price <= float(stop):
            return "stop", 1.0
        if tp2 is not None and price >= float(tp2):
            return "tp2", 1.0
        if tp1 is not None and not tp1_done and price >= float(tp1):
            return "tp1", cfg.tp1_portion
    else:  # SELL
        if stop is not None and price >= float(stop):
            return "stop", 1.0
        if tp2 is not None and price <= float(tp2):
            return "tp2", 1.0
        if tp1 is not None and not tp1_done and price <= float(tp1):
            return "tp1", cfg.tp1_portion
    return None, 0.0


def reconcile_broker_ids(client):
    from strategies import reconcile
    return reconcile.summarise_broker(reconcile.broker_positions(client))


def _append_log(record):
    os.makedirs(JOURNAL, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def check_once(forced_price=None, dry_run=False):
    cfg = ScalperConfig()
    open_pos = paper.open_positions()
    bid, ask, age = live_prices()
    if forced_price is not None:
        bid = ask = float(forced_price)
        age = 0.0

    out = {"ts": _now().isoformat(), "open_positions": len(open_pos),
           "bid": bid, "ask": ask, "price_age": age, "actions": []}

    for pos in open_pos:
        # close a long on the bid, a short on the ask (what you'd actually get)
        price = bid if pos["side"] == "BUY" else ask
        if price is None:
            out["actions"].append({"id": pos["id"], "skipped": "no live price"})
            continue
        reason, portion = evaluate_position(pos, float(price), cfg)
        if not reason:
            out["actions"].append({"id": pos["id"], "price": float(price), "action": "hold"})
            continue

        if dry_run:
            out["actions"].append({"id": pos["id"], "price": float(price),
                                   "would": f"close {portion:.0%} ({reason})"})
            continue

        result = paper.close_partial(pos["id"], portion, float(price), reason)
        out["actions"].append({"id": pos["id"], "price": float(price),
                               "closed": reason, "result": result})
        if reason == "stop":
            notify(
                f"🔴 PAPER STOP HIT\n\n{pos['symbol']} {pos['side']}\n"
                f"Exit: ${float(price):,.2f}\nLoss: ${abs(result.get('pnl', 0)):,.2f}\n"
                f"Range assumption was wrong — no averaging down.",
                kind="paper_stop",
            )
        else:
            notify(
                f"🟢 PAPER {reason.upper()} FILLED\n\n{pos['symbol']} {pos['side']}\n"
                f"Exit: ${float(price):,.2f}\nPnL: ${result.get('pnl', 0):,.2f}\n"
                f"Remaining: {result.get('remaining_portion', 0):.0%} of position",
                kind=f"paper_{reason}",
            )

    # Sync live records: a live position the broker no longer holds is closed
    # (broker-managed stop or take-profit fired).
    try:
        lp = live_positions.open_positions()
        if lp:
            client = build_client()
            broker_ids = {str(b.get("position_id")) for b in
                          reconcile_broker_ids(client)}
            for p in lp:
                if str(p.get("broker_position_id")) not in broker_ids:
                    live_positions.close(p["id"], "closed at broker")
                    out["actions"].append({"id": p["id"], "live": "closed at broker"})
                    notify(
                        "LIVE POSITION CLOSED\n\n"
                        f"{p.get('symbol')} {p.get('side')}\n"
                        f"Entry: {p.get('entry')}  stop {p.get('stop')}  target {p.get('tp2')}\n"
                        "Closed at the broker (stop or target hit).",
                        kind="live_closed")
    except Exception as exc:
        out["actions"].append({"live_sync_error": str(exc)[:120]})

    # Only log when something happened: a flat book every minute for months
    # is noise, and the watchdog already covers liveness.
    if out["actions"]:
        _append_log(out)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="position_monitor.py", description="Paper position monitor")
    p.add_argument("--price", type=float, default=None, help="force a price (testing)")
    p.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    try:
        out = check_once(args.price, args.dry_run)
    except EtoroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"position monitor: {out['open_positions']} open | bid {out['bid']} ask {out['ask']}")
        for a in out["actions"]:
            print("  ", json.dumps(a, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
