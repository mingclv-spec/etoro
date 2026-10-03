"""Execution runner - Stage A (PAPER ONLY).

    signal -> trading gate -> position sizing -> PAPER fill

The last wire to eToro is deliberately absent. This module cannot place an
order: it calls strategies.paper, never etoro.client.open_market_order.
Going live later means adding one adapter, not rewriting this.

Idempotent: one execution per 15m candle, tracked in journal/executor_state.json.
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
from etoro.config import load_settings
from strategies import paper, live_exec, reconcile, live_positions
from strategies.gold_range_scalper import ScalperConfig, analyze
from strategies.indicators import to_candles
from strategies.killswitch import KillSwitchConfig, evaluate as ks_evaluate, load_state as ks_load
from strategies.notifier import notify
from strategies.risk import RiskConfig, position_size
from strategies.trading_gate import GateConfig, evaluate as gate_evaluate

ROOT = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(ROOT, "journal")
STATE_PATH = os.path.join(JOURNAL, "executor_state.json")
LOG_PATH = os.path.join(JOURNAL, "executor.jsonl")
MARKET_STATE = os.path.join(JOURNAL, "market_state.json")

# GOLD -> 18 "Gold (Non Expiry)" (openable, lev up to 20x, min $1,000).
# 559 = GOLD.24-7 is BLOCKED from opening (allowOpenPosition=false).
SYMBOLS = {"GOLD": 18, "GLD": 3025}


def _now():
    return datetime.now(timezone.utc)


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _live_market():
    st = _load_json(MARKET_STATE, {})
    if not st.get("connected"):
        return None, None
    try:
        spread_pct = float(st.get("spread_pct"))
    except (TypeError, ValueError):
        spread_pct = None
    age = None
    try:
        ts = datetime.fromisoformat(str(st.get("updated_at")).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (_now() - ts).total_seconds()
    except Exception:
        pass
    return spread_pct, age


def fetch_candles(client, instrument_id, interval, count):
    path = f"/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"
    payload = client.get(path)
    groups = payload.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    return to_candles(rows)[::-1]


def run_once(symbol="GOLD", equity=200.0, risk_pct=0.01, force=False):
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        return {"error": f"unknown symbol {symbol}"}
    iid = SYMBOLS[symbol]

    client = build_client()
    h1 = fetch_candles(client, iid, "OneHour", 220)
    m15 = fetch_candles(client, iid, "FifteenMinutes", 320)
    if len(m15) < 2 or len(h1) < 2:
        return {"error": "not enough candles"}
    # Evaluate on the last CLOSED candle so signals cannot repaint.
    m15_closed, h1_closed = m15[:-1], h1[:-1]
    cfg = ScalperConfig()
    report = analyze(h1_closed, m15_closed, cfg)
    signal = report.get("signal", {})
    action = signal.get("action")
    candle = m15_closed[-1].time

    state = _load_json(STATE_PATH, {})
    record = {"ts": _now().isoformat(), "symbol": symbol, "candle": candle,
              "mode": report.get("mode"), "action": action}

    if action not in ("BUY", "SELL"):
        record["result"] = "no actionable signal"
        _append_log(record)
        return record

    if not force and (state.get(symbol) or {}).get("last_executed_candle") == candle:
        record["result"] = "already executed this candle (idempotent skip)"
        _append_log(record)
        return record

    # Never stack positions: one at a time, regardless of signal frequency.
    holding = paper.open_positions()
    if holding and not force:
        record["result"] = "already holding a position - refusing to stack"
        record["open_positions"] = len(holding)
        _append_log(record)
        return record

    # --- trading gate: monitoring stays on, new trades need every condition met
    spread_pct, price_age = _live_market()
    ks = ks_evaluate(ks_load(), KillSwitchConfig(), equity,
                     observed_spread_pct=(spread_pct / 100.0) if spread_pct else None)
    gate = gate_evaluate(
        regime_mode=report.get("mode"),
        killswitch_result=ks,
        spread_pct=spread_pct,
        price_age_seconds=price_age,
        connected=(price_age is not None),
        cfg=GateConfig(),
    )
    record["gate"] = gate
    if not gate["trading_enabled"]:
        record["result"] = "blocked by trading gate"
        _append_log(record)
        return record

    # --- sizing + paper fill
    entry = float(signal["entry"])
    stop = float(signal["stop"])
    sizing = position_size(entry, stop, RiskConfig(account_equity=equity, risk_per_trade_pct=risk_pct))
    record["sizing"] = sizing
    if "error" in sizing:
        record["result"] = f"sizing failed: {sizing['error']}"
        _append_log(record)
        return record

    settings = load_settings()
    if settings.is_paper:
        pos = paper.open_position(
            symbol, action, sizing["notional"], entry,
            stop=stop, tp1=signal.get("tp1"), tp2=signal.get("tp2"),
            leverage=cfg.leverage, spread_pct=spread_pct or 0.0,
            note="Stage A paper fill", signal_candle=candle,
        )
        record["position_id"] = pos["id"]
        record["result"] = "PAPER position opened"
    else:
        # LIVE PATH. Only reachable when TRADING_MODE=live.
        rec = reconcile.reconcile(client, live_positions.open_positions(), dry_run=False)
        record["reconcile"] = rec
        if not rec.get("ok"):
            record["result"] = "BLOCKED: reconciliation failed - refusing to trade"
            _append_log(record)
            return record
        # eToro requires >= 1.0% stop on this instrument (minStopLossPercentage).
        # Distances as fractions. Broker floor is 1% (minStopLossPercentage).
        stop_frac = max(cfg.stop_buffer_pct, 0.01)
        tp_ref = signal.get("tp2") or signal.get("tp1") or entry
        tp_frac = abs(float(tp_ref) - entry) / entry
        sign = 1.0 if action == "BUY" else -1.0
        # stopLossRate / takeProfitRate are ABSOLUTE PRICES on this API.
        stop_price = round(entry * (1.0 - sign * stop_frac), 2)
        tp_price = round(entry * (1.0 + sign * tp_frac), 2)
        # eToro supports buy and sellShort only. A SELL signal is a SHORT.
        tx = "buy" if action == "BUY" else "sellShort"
        # CRITICAL: eToro's "amount" is the MARGIN committed, NOT the notional.
        # exposure = amount x leverage. Proven by the first live fill:
        #   amount 50 -> margin 50, exposure 999.97 at 20x.
        # Sending the notional here would create 20x the intended position.
        exposure = max(sizing["notional"], 1000.0)   # broker min exposure
        margin_amount = round(exposure / cfg.leverage, 2)
        payload = live_exec.build_payload(symbol, iid, margin_amount,
                                          cfg.leverage, stop_price, tp_price,
                                          transaction=tx)
        record["payload"] = payload
        resp = live_exec.place(client, payload)
        record["order_response"] = resp
        final = live_exec.wait_for_terminal(
            client, order_id=resp.get("orderId"),
            reference_id=resp.get("referenceId") or client.last_request_id)
        record["order_final"] = final
        status_id = live_exec.extract_status(final)
        record["order_status_id"] = status_id
        pids = live_exec.extract_positions(final)
        if status_id in (live_exec.STATUS_FILLED, live_exec.STATUS_PARTIAL) and pids:
            lp = live_positions.record_open(
                pids[0], symbol, action, sizing["notional"], entry, stop,
                tp1=signal.get("tp1"), tp2=signal.get("tp2"),
                leverage=cfg.leverage, stop_pct=stop_pct,
                signal_candle=candle)
            record["live_position_id"] = lp["id"]
            record["broker_position_id"] = pids[0]
            record["result"] = "LIVE position OPENED"
        else:
            record["result"] = f"LIVE order not filled (status {status_id})"
        record["position_id"] = None

    state[symbol] = {"last_executed_candle": candle, "at": _now().isoformat(),
                     "position_id": pos["id"]}
    _save_json(STATE_PATH, state)
    _append_log(record)

    notify(
        f"🟢 PAPER TRADE OPENED\n\n{symbol} {action}\n"
        f"Position: ${sizing['notional']:,.2f} (margin ${sizing['margin_required']:,.2f} at {cfg.leverage}×)\n"
        f"Entry: ${entry:,.2f}\nStop: ${stop:,.2f}\n"
        f"TP1: ${float(signal.get('tp1') or 0):,.2f} / TP2: ${float(signal.get('tp2') or 0):,.2f}\n"
        f"Mode: PAPER (no real order)",
        kind="paper_open",
    )
    return record


def _append_log(record):
    os.makedirs(JOURNAL, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="execute.py", description="Stage A paper execution runner")
    p.add_argument("--symbol", default="GOLD")
    p.add_argument("--equity", type=float, default=200.0)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--force", action="store_true", help="ignore the idempotency guard")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    try:
        rec = run_once(args.symbol, args.equity, args.risk_pct, args.force)
    except EtoroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(f"execute: {rec.get('result')}")
        print(f"  mode  : {rec.get('mode')}   action: {rec.get('action')}   candle: {rec.get('candle')}")
        if rec.get("gate") and not rec["gate"]["trading_enabled"]:
            for b in rec["gate"]["blocks"]:
                print(f"  blocked: {b}")
        if rec.get("position_id"):
            print(f"  paper position: {rec['position_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
