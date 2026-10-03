"""CLI runner for the Adaptive Gold Range Scalper v0.1.

SIGNAL ONLY - this never places an order. It prints what the strategy sees and
logs every evaluation to the trade journal so a human can decide.

    python scalper.py                          # human-readable report
    python scalper.py --json                   # machine-readable
    python scalper.py --symbol GLD             # GLD ETF instead of the GOLD CFD
    python scalper.py --account-equity 5000 --risk-pct 0.0025
    python scalper.py --no-journal             # do not append to the journal
"""
from __future__ import annotations

import argparse
import json
import sys

from etoro import EtoroError, build_client
from strategies.gold_range_scalper import ScalperConfig, analyze
from strategies.indicators import to_candles
from strategies.journal import DEFAULT_PATH as JOURNAL_PATH, log_signal
from strategies.risk import RiskConfig, pnl_table, position_size

INSTRUMENTS = {"GOLD": 559, "GLD": 3025}
CANDLES_1H = 220
CANDLES_15M = 320


def fetch_candles(client, instrument_id, interval, count):
    path = f"/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"
    payload = client.get(path)
    groups = payload.get("candles") or []
    rows = groups[0].get("candles", []) if groups else []
    return to_candles(rows)[::-1]  # API returns newest-first


def render(report, sizing=None):
    r, g, s = report["range"], report["regime"], report["signal"]
    L = []
    L.append(report["strategy"])
    L.append(f"symbol          : {report.get('symbol')} ({report.get('instrument_id')})")
    L.append(f"mode            : {report['mode']}")
    L.append("")
    L.append("GATES")
    for name, ok in report["gates"].items():
        L.append(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    L.append("")
    L.append("REGIME (1H)")
    L.append(f"  last close     : {g['price']}")
    L.append(f"  ADX(14)        : {round(g['adx14'],2) if g['adx14'] is not None else None}")
    L.append(f"  ATR(14)        : {round(g['atr14'],3) if g['atr14'] is not None else None}")
    L.append(f"  BB width       : {round(g['bb_width_pct'],3) if g['bb_width_pct'] is not None else None} %")
    L.append(f"  EMA20 / EMA50  : {round(g['ema_fast'],2) if g['ema_fast'] else None} / {round(g['ema_slow'],2) if g['ema_slow'] else None}")
    L.append("")
    L.append(f"RANGE (last {r.get('bars_used')} x 15m = {r.get('lookback_hours')}h, {r.get('boundary_mode')})")
    L.append(f"  high / low     : {r.get('range_high')} / {r.get('range_low')}")
    L.append(f"  width          : {round(r['width_pct'],3) if r.get('width_pct') is not None else None} %")
    L.append(f"  touches hi/lo  : {r.get('touches_resistance')} / {r.get('touches_support')}")
    L.append("")
    b = report.get("breakout", {})
    if b:
        L.append(f"BREAKOUT CHECK  : {'TRIGGERED' if b.get('breakout') else 'quiet'}  ({b.get('atr_source')} ATR)")
        for name, ok in (b.get("checks") or {}).items():
            L.append(f"  [{'YES' if ok else 'no '}] {name}")
        L.append("")
    L.append(f"SIGNAL          : {s['action']}")
    L.append(f"  {s['reason']}")
    if s.get("zone_position_pct") is not None:
        L.append(f"  zone position  : {s['zone_position_pct']} % of range")
    if s.get("rsi14") is not None:
        L.append(f"  RSI(14)        : {s['rsi14']}")
    if "entry" in s:
        L.append(f"  entry          : {s['entry']}")
        L.append(f"  stop           : {s['stop']}   <- mandatory, no averaging down")
        L.append(f"  TP1 / TP2      : {s.get('tp1')} / {s.get('tp2')}  ({int(s.get('tp1_portion',0)*100)}% / {int(s.get('tp2_portion',0)*100)}%)")
    if "entry" in s and sizing and "error" not in sizing:
        L.append("")
        L.append("POSITION SIZING (from account risk)")
        L.append(f"  account        : ${sizing['account_equity']}")
        L.append(f"  risk / trade   : {sizing['risk_per_trade_pct']} %  = ${sizing['risk_amount']}")
        L.append(f"  stop distance  : {sizing['stop_distance_pct']} %")
        L.append(f"  notional       : ${sizing['notional']}  (margin ${sizing['margin_required']} at {sizing['leverage']}x = {sizing['margin_pct_of_equity']}% of equity)")
        L.append(f"  max loss       : ${sizing['max_loss_at_stop']}")
        for w in sizing.get("warnings", []):
            L.append(f"  WARNING        : {w}")
    L.append("")
    L.append("NOTE            : no overnight positions - entry -> TP/SL -> close.")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(prog="scalper.py",
                                description="Adaptive Gold Range Scalper v0.1 (signal only)")
    p.add_argument("--symbol", default="GOLD", help="GOLD (CFD, 559) or GLD (ETF, 3025)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--adx-max", type=float, default=None)
    p.add_argument("--min-width-pct", type=float, default=None)
    p.add_argument("--boundary-mode", choices=("raw", "validated"), default=None)
    p.add_argument("--macro-block", action="store_true")
    p.add_argument("--account-equity", type=float, default=5000.0)
    p.add_argument("--risk-pct", type=float, default=0.0025, help="risk per trade, e.g. 0.0025 = 0.25%%")
    p.add_argument("--leverage", type=int, default=None)
    p.add_argument("--no-journal", action="store_true", help="do not append to the trade journal")
    p.add_argument("--show-pnl-table", action="store_true")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    if symbol not in INSTRUMENTS:
        print(f"unknown symbol {symbol}; known: {', '.join(INSTRUMENTS)}", file=sys.stderr)
        return 2
    iid = INSTRUMENTS[symbol]

    cfg = ScalperConfig()
    if args.adx_max is not None:
        cfg.adx_max = args.adx_max
    if args.min_width_pct is not None:
        cfg.min_range_width_pct = args.min_width_pct
    if args.boundary_mode is not None:
        cfg.boundary_mode = args.boundary_mode
    if args.leverage is not None:
        cfg.leverage = args.leverage
    cfg.macro_block = args.macro_block

    try:
        client = build_client()
        h1 = fetch_candles(client, iid, "OneHour", CANDLES_1H)
        m15 = fetch_candles(client, iid, "FifteenMinutes", CANDLES_15M)
    except EtoroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = analyze(h1, m15, cfg)
    report["symbol"] = symbol
    report["instrument_id"] = iid

    risk_cfg = RiskConfig(account_equity=args.account_equity,
                          risk_per_trade_pct=args.risk_pct,
                          leverage=cfg.leverage,
                          max_leverage=cfg.max_leverage)
    sizing = None
    s = report.get("signal", {})
    if s.get("entry") is not None and s.get("stop") is not None:
        sizing = position_size(s["entry"], s["stop"], risk_cfg)
        report["sizing"] = sizing

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report, sizing))
        if args.show_pnl_table and sizing and "error" not in sizing:
            print("\nPnL table at that notional:")
            for row in pnl_table(sizing["notional"]):
                print(f"  {row['move_pct']:+.2f}%  ->  {row['pl']:+.2f}")

    if not args.no_journal:
        path = log_signal(report, extra={"sizing": sizing} if sizing else None)
        if not args.json:
            print(f"\njournal         : appended to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
