"""Execution runner - Stage A (PAPER ONLY).

signal -> trading gate -> position sizing -> PAPER fill.
Prints a detailed human-readable execution trace while retaining JSON journaling.
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
from etoro.config import ConfigError, load_settings
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
SYMBOLS = {"GOLD": 18, "GLD": 3025}


def _now():
    return datetime.now(timezone.utc)


def _step(message, level="INFO"):
    ts = _now().astimezone().strftime("%H:%M:%S")
    icon = {"OK": "✓", "ERROR": "✗", "WARN": "!", "INFO": "•"}.get(level, "•")
    print(f"[{ts}] {icon} {message}", flush=True)


def _banner(title):
    print("\n" + "=" * 64, flush=True)
    print(title, flush=True)
    print("=" * 64, flush=True)


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
    _step(f"Fetching {count} {interval} candles for instrument {instrument_id}...")
    payload = client.get(path)
    groups = payload.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    candles = to_candles(rows)[::-1]
    _step(f"Received {len(candles)} {interval} candles", "OK")
    return candles


def _print_config(settings):
    _banner("eToro Agent Configuration")
    _step(f"Account: {settings.account.upper()}")
    _step(f"User key source: {settings.user_key_source}")
    _step(f"API key: {'SET (' + str(len(settings.api_key)) + ' chars)' if settings.api_key else 'MISSING'}",
          "OK" if settings.api_key else "ERROR")
    _step(f"User key: {'SET (' + str(len(settings.user_key)) + ' chars)' if settings.user_key else 'MISSING'}",
          "OK" if settings.user_key else "ERROR")
    _step(f"Trading mode: {settings.trading_mode.upper()}")
    _step(f"Dry run: {str(settings.dry_run).upper()}")
    _step(f"Live trading enabled: {str(settings.live_trading_enabled).upper()}",
          "WARN" if settings.live_trading_enabled else "OK")


def _finish(record):
    _append_log(record)
    _banner("EXECUTION COMPLETE")
    _step(f"Result: {record.get('result')}")
    if record.get("symbol"):
        _step(f"Symbol: {record.get('symbol')} | Action: {record.get('action')} | Strategy mode: {record.get('mode')}")
    if record.get("position_id"):
        _step(f"Position ID: {record['position_id']}")
    if record.get("live_position_id"):
        _step(f"Live position ID: {record['live_position_id']}")
    if record.get("broker_position_id"):
        _step(f"Broker position ID: {record['broker_position_id']}")
    if record.get("order_status_id") is not None:
        _step(f"Order status ID: {record['order_status_id']}")
    return record


def run_once(symbol="GOLD", equity=200.0, risk_pct=0.01, force=False):
    symbol = symbol.upper()
    settings = load_settings()
    _banner(f"eToro Agent Execution - {symbol}")
    _print_config(settings)
    _step(f"Starting | equity=${equity:,.2f} | risk={risk_pct:.2%} | force={force}")
    if symbol not in SYMBOLS:
        _step(f"Unknown symbol: {symbol}", "ERROR")
        return {"error": f"unknown symbol {symbol}"}
    iid = SYMBOLS[symbol]
    _step(f"Resolved {symbol} -> instrument ID {iid}", "OK")

    _step("Creating eToro API client...")
    client = build_client()
    _step("eToro API client ready", "OK")

    h1 = fetch_candles(client, iid, "OneHour", 220)
    m15 = fetch_candles(client, iid, "FifteenMinutes", 320)
    if len(m15) < 2 or len(h1) < 2:
        _step("Not enough candles to evaluate strategy", "ERROR")
        return {"error": "not enough candles"}

    m15_closed, h1_closed = m15[:-1], h1[:-1]
    candle = m15_closed[-1].time
    _step(f"Evaluating last CLOSED 15m candle: {candle}", "OK")

    cfg = ScalperConfig()
    _step("Running GOLD range scalper...")
    report = analyze(h1_closed, m15_closed, cfg)
    signal = report.get("signal", {})
    action = signal.get("action")
    _step(f"Strategy mode={report.get('mode')} | signal={action or 'NONE'}")

    record = {"ts": _now().isoformat(), "symbol": symbol, "candle": candle,
              "mode": report.get("mode"), "action": action}

    if action not in ("BUY", "SELL"):
        record["result"] = "no actionable signal"
        _step("No BUY/SELL signal. Nothing to execute.", "WARN")
        return _finish(record)

    _step(f"Signal={action}", "OK")
    _step(f"Entry=${float(signal['entry']):,.2f} | Stop=${float(signal['stop']):,.2f} | TP1=${float(signal.get('tp1') or 0):,.2f} | TP2=${float(signal.get('tp2') or 0):,.2f}")

    state = _load_json(STATE_PATH, {})
    if not force and (state.get(symbol) or {}).get("last_executed_candle") == candle:
        record["result"] = "already executed this candle (idempotent skip)"
        _step("Idempotency guard blocked duplicate execution for this candle.", "WARN")
        return _finish(record)

    holding = paper.open_positions()
    _step(f"Existing paper positions={len(holding)}")
    if holding and not force:
        record["result"] = "already holding a position - refusing to stack"
        record["open_positions"] = len(holding)
        _step("Position stacking blocked because a position already exists.", "WARN")
        return _finish(record)

    spread_pct, price_age = _live_market()
    _step(f"Market spread={spread_pct:.4f}%" if spread_pct is not None else "Market spread unavailable", "OK" if spread_pct is not None else "WARN")
    _step(f"Market price age={price_age:.1f}s" if price_age is not None else "Market price age unavailable / market disconnected", "OK" if price_age is not None else "WARN")

    _step("Checking kill switch and trading gate...")
    ks = ks_evaluate(ks_load(), KillSwitchConfig(), equity,
                     observed_spread_pct=(spread_pct / 100.0) if spread_pct else None)
    gate = gate_evaluate(regime_mode=report.get("mode"), killswitch_result=ks,
                         spread_pct=spread_pct, price_age_seconds=price_age,
                         connected=(price_age is not None), cfg=GateConfig())
    record["gate"] = gate
    if not gate["trading_enabled"]:
        record["result"] = "blocked by trading gate"
        _step("TRADING BLOCKED", "ERROR")
        for block in gate.get("blocks", []):
            _step(f"Gate block: {block}", "WARN")
        return _finish(record)
    _step("Trading gate passed", "OK")

    entry = float(signal["entry"])
    stop = float(signal["stop"])
    _step("Calculating position size...")
    sizing = position_size(entry, stop, RiskConfig(account_equity=equity, risk_per_trade_pct=risk_pct))
    record["sizing"] = sizing
    if "error" in sizing:
        record["result"] = f"sizing failed: {sizing['error']}"
        _step(f"Position sizing failed: {sizing['error']}", "ERROR")
        return _finish(record)
    _step(f"Position notional=${sizing['notional']:,.2f} | margin required=${sizing['margin_required']:,.2f}", "OK")

    mode = "PAPER" if settings.is_paper else "LIVE"
    _step(f"Trading mode={mode}")

    if settings.is_paper:
        _step("Opening PAPER position...")
        pos = paper.open_position(symbol, action, sizing["notional"], entry,
                                  stop=stop, tp1=signal.get("tp1"), tp2=signal.get("tp2"),
                                  leverage=cfg.leverage, spread_pct=spread_pct or 0.0,
                                  note="Stage A paper fill", signal_candle=candle)
        record["position_id"] = pos["id"]
        record["result"] = "PAPER position opened"
        _step(f"PAPER position opened: {pos['id']}", "OK")
    else:
        _step("LIVE mode: reconciling broker positions before order...", "WARN")
        rec = reconcile.reconcile(client, live_positions.open_positions(), dry_run=False)
        record["reconcile"] = rec
        if not rec.get("ok"):
            record["result"] = "BLOCKED: reconciliation failed - refusing to trade"
            _step("LIVE TRADE BLOCKED: reconciliation failed", "ERROR")
            return _finish(record)
        _step("Broker reconciliation passed", "OK")

        stop_frac = max(cfg.stop_buffer_pct, 0.01)
        tp_ref = signal.get("tp2") or signal.get("tp1") or entry
        tp_frac = abs(float(tp_ref) - entry) / entry
        sign = 1.0 if action == "BUY" else -1.0
        stop_price = round(entry * (1.0 - sign * stop_frac), 2)
        tp_price = round(entry * (1.0 + sign * tp_frac), 2)
        tx = "buy" if action == "BUY" else "sellShort"
        exposure = max(sizing["notional"], 1000.0)
        margin_amount = round(exposure / cfg.leverage, 2)
        _step(f"LIVE order={tx} | margin=${margin_amount:,.2f} | leverage={cfg.leverage}x | exposure=${exposure:,.2f}")
        _step(f"Stop=${stop_price:,.2f} | TP=${tp_price:,.2f}")
        payload = live_exec.build_payload(symbol, iid, margin_amount, cfg.leverage,
                                           stop_price, tp_price, transaction=tx)
        record["payload"] = payload
        _step("Submitting LIVE order to eToro...", "WARN")
        resp = live_exec.place(client, payload)
        record["order_response"] = resp
        _step(f"Order submitted | orderId={resp.get('orderId')} | referenceId={resp.get('referenceId')}", "OK")
        _step("Waiting for terminal order status...")
        final = live_exec.wait_for_terminal(client, order_id=resp.get("orderId"),
                                             reference_id=resp.get("referenceId") or client.last_request_id)
        record["order_final"] = final
        status_id = live_exec.extract_status(final)
        record["order_status_id"] = status_id
        pids = live_exec.extract_positions(final)
        _step(f"Final status={status_id} | positions returned={len(pids)}")
        if status_id in (live_exec.STATUS_FILLED, live_exec.STATUS_PARTIAL) and pids:
            lp = live_positions.record_open(pids[0], symbol, action, sizing["notional"], entry, stop,
                                            tp1=signal.get("tp1"), tp2=signal.get("tp2"),
                                            leverage=cfg.leverage, stop_pct=stop_frac,
                                            signal_candle=candle)
            record["live_position_id"] = lp["id"]
            record["broker_position_id"] = pids[0]
            record["result"] = "LIVE position OPENED"
            _step(f"LIVE position OPENED: broker position {pids[0]}", "OK")
        else:
            record["result"] = f"LIVE order not filled (status {status_id})"
            _step(f"LIVE order not filled | status={status_id}", "WARN")
        record["position_id"] = None

    state[symbol] = {"last_executed_candle": candle, "at": _now().isoformat(),
                     "position_id": record.get("position_id") or record.get("live_position_id")}
    _save_json(STATE_PATH, state)
    _step("Execution state saved to journal/executor_state.json", "OK")
    _append_log(record)
    _step("Execution record saved to journal/executor.jsonl", "OK")

    notify(f"🟢 {mode} TRADE OPENED\n\n{symbol} {action}\n"
           f"Position: ${sizing['notional']:,.2f} (margin ${sizing['margin_required']:,.2f} at {cfg.leverage}×)\n"
           f"Entry: ${entry:,.2f}\nStop: ${stop:,.2f}\n"
           f"TP1: ${float(signal.get('tp1') or 0):,.2f} / TP2: ${float(signal.get('tp2') or 0):,.2f}\n"
           f"Mode: {mode}", kind="paper_open" if settings.is_paper else "live_open")
    return _finish(record)


def _append_log(record):
    os.makedirs(JOURNAL, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _check_config():
    try:
        settings = load_settings()
    except ConfigError as exc:
        _step(f"Configuration error: {exc}", "ERROR")
        return 1
    _print_config(settings)
    _step("Configuration check PASSED.", "OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="execute.py", description="eToro execution runner")
    p.add_argument("--symbol", default="GOLD")
    p.add_argument("--equity", type=float, default=200.0)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--force", action="store_true", help="ignore the idempotency guard")
    p.add_argument("--json", action="store_true")
    p.add_argument("--check-config", action="store_true",
                   help="validate account and credential selection without trading")
    args = p.parse_args(argv)
    try:
        if args.check_config:
            return _check_config()
        rec = run_once(args.symbol, args.equity, args.risk_pct, args.force)
    except (EtoroError, ConfigError) as exc:
        _step(f"eToro API error: {exc}", "ERROR")
        return 1
    except Exception as exc:
        _step(f"Unexpected error: {type(exc).__name__}: {exc}", "ERROR")
        return 1
    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
