"""Command line entry point for the eToro AutoTrader app."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from etoro import EtoroDryRunBlocked, EtoroError, __version__, build_client
from etoro.config import ConfigError, load_settings
from strategies.bitcoin_trend_rider import BTCConfig, signal as analyze_btc
from strategies.gold_range_scalper import ScalperConfig, analyze as analyze_gold
from strategies.indicators import to_candles
from strategies.polymarket_btc import PolymarketConfig, PolymarketError, fetch_btc_signal
from strategies.strategy_router import strategy_name


def _parse_params(pairs: Optional[List[str]]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--param expects key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        params[key.strip()] = value.strip()
    return params


def _dump(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))

def _table(payload: Any) -> None:
    """Render structured output as a compact table; JSON remains opt-in."""
    if isinstance(payload, dict):
        rows = []
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, default=str)
            rows.append({"field": key, "value": value})
        if not rows:
            print("(no data)")
            return
        columns = ["field", "value"]
    elif isinstance(payload, list):
        rows = []
        for item in payload:
            if isinstance(item, dict):
                rows.append(item)
            else:
                rows.append({"value": item})
        if not rows:
            print("(no data)")
            return
        columns = list(rows[0].keys())
    else:
        rows = [{"value": payload}]
        columns = ["value"]

    widths = {
        col: max(len(col), *(len(str(row.get(col, ""))) for row in rows))
        for col in columns
    }
    print(" | ".join(col.ljust(widths[col]) for col in columns))
    print("-+-".join("-" * widths[col] for col in columns))
    for row in rows:
        print(" | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


def cmd_settings(args: argparse.Namespace) -> int:
    _dump(load_settings().describe())
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    print(f"eToro AutoTrader {__version__}")
    print(f"  env file : {settings.env_source or '<process environment>'}")
    print(f"  account  : {settings.account} ({'demo' if settings.is_demo else 'REAL'})")
    print(f"  dry-run  : {settings.dry_run}")
    print(f"  live     : {'ENABLED' if settings.live_trading_enabled else 'disabled'}")
    print("  instruments: dynamic (eToro market-data catalog)")
    print(f"  limits   : order<={settings.max_order_usd} / position<={settings.max_position_usd} USD")
    client = build_client(settings=settings)
    result = client.ping()
    print(f"\nAPI check: OK ({result['status']}, {result['elapsed_ms']} ms)")
    print(f"  watchlists returned : {result['watchlists']}")
    print(f"  x-request-id        : {result['x_request_id']}")
    return 0


def cmd_watchlists(args: argparse.Namespace) -> int:
    payload = build_client().get_watchlists()
    rows = payload.get("watchlists", []) if isinstance(payload, dict) else payload
    print(f"{len(rows)} watchlist(s):")
    for row in rows:
        flag = " (default)" if row.get("isDefault") else ""
        print(f"  - {row.get('name')}{flag} - {row.get('totalItems', 0)} items - id={row.get('watchlistId')}")
    if args.json:
        print()
        _dump(payload)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    client = build_client()
    for symbol in args.symbol:
        try:
            instrument_id = client.resolve_instrument_id(symbol)
        except EtoroError as exc:
            print(f"{symbol}: not found ({exc})")
            continue
        print(f"{symbol.upper()} -> instrumentId {instrument_id}")
        if args.json:
            _dump(client.search_instruments(symbol))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    client = build_client()
    payload = client.get(args.path, params=_parse_params(args.param) or None)
    _dump(payload)
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    client = build_client()
    if not args.yes:
        raise SystemExit("Refusing to place an order without --yes (explicit confirmation).")

    if args.leverage > 1 and args.stop_loss is None:
        raise SystemExit("--stop-loss is required when --leverage is greater than 1.")

    if args.stop_loss is not None and args.stop_loss <= 0:
        raise SystemExit("--stop-loss must be greater than 0 (an absolute stop-loss price).")

    if args.take_profit is not None and args.take_profit <= 0:
        raise SystemExit("--take-profit must be greater than 0 (an absolute take-profit price).")

    _dump(client.open_market_order(
        args.symbol,
        args.amount,
        transaction=args.transaction.lower(),
        leverage=args.leverage,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
    ))
    return 0


def _fmt_price(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _condition_rows(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    gates = analysis.get("gates") or {}
    for key, value in gates.items():
        rows.append({"condition": key, "result": "PASS" if value else "FAIL"})

    signal = analysis.get("signal") or {}
    for side, checks in (("BUY", signal.get("buy_conditions")), ("SELL", signal.get("sell_conditions"))):
        if not isinstance(checks, dict):
            continue
        for key, value in checks.items():
            rows.append({"condition": f"{side}: {key}", "result": "PASS" if value else "FAIL"})

    breakout = analysis.get("breakout") or {}
    for key, value in (breakout.get("checks") or {}).items():
        rows.append({"condition": f"BREAKOUT: {key}", "result": "PASS" if value else "FAIL"})
    return rows


def cmd_signal(args: argparse.Namespace) -> int:
    """Read live eToro candles and print a signal; never places an order."""
    symbol = args.symbol.strip().upper()

    client = build_client()
    instrument_id = client.resolve_instrument_id(symbol)
    rate_payload = client.get_rates(instrument_id)
    live_price = client.extract_rate(rate_payload, instrument_id)

    if strategy_name(symbol) == "BTC_TREND_RIDER":
        raw_4h = client.get_candles(instrument_id, "FourHours", 220, direction="asc")
        raw_1h = client.get_candles(instrument_id, "OneHour", 120, direction="asc")
        candles_4h = to_candles(client.extract_candles(raw_4h))
        candles_1h = to_candles(client.extract_candles(raw_1h))
        if len(candles_4h) < 200:
            raise EtoroError(f"Not enough 4H candles returned for BTC: {len(candles_4h)}")
        if len(candles_1h) < 60:
            raise EtoroError(f"Not enough 1H candles returned for BTC: {len(candles_1h)}")

        try:
            pm = fetch_btc_signal(PolymarketConfig())
        except PolymarketError as exc:
            pm = {
                "available": False,
                "source": "polymarket",
                "reason": str(exc),
            }

        cfg = BTCConfig(
            max_leverage=1,
            trade_amount_usd=20.0,
            max_loss_usd=10.0,
        )
        analysis = analyze_btc(candles_4h, candles_1h, [], cfg, pm)
        action = analysis.get("action", "STAND_ASIDE")

        print("BTC TRADING SIGNAL")
        print("=" * 64)
        _table({
            "market": symbol,
            "instrument_id": instrument_id,
            "live_price": _fmt_price(live_price),
            "analysis_candle_price": _fmt_price(analysis.get("price")),
            "strategy": analysis.get("strategy"),
            "technical_score": f"{analysis.get('technical_score', 0)}/6",
        })

        print("\nTECHNICAL CONDITIONS")
        _table([
            {"condition": key, "result": "PASS" if value else "FAIL"}
            for key, value in (analysis.get("checks") or {}).items()
        ])

        pm_rows = analysis.get("polymarket") or {}
        print("\nPOLYMARKET")
        _table({
            "available": pm_rows.get("available"),
            "market": pm_rows.get("question"),
            "duration_minutes": pm_rows.get("duration_minutes"),
            "time_remaining_minutes": pm_rows.get("time_remaining_minutes"),
            "up_probability": pm_rows.get("up_probability"),
            "down_probability": pm_rows.get("down_probability"),
            "probability_change_15m": pm_rows.get("probability_change_15m"),
            "volume_usd": pm_rows.get("volume_usd"),
            "bias": pm_rows.get("bias"),
            "score": pm_rows.get("score"),
            "confirms_buy": pm_rows.get("confirms_buy"),
            "conflict": pm_rows.get("conflict"),
        })
        if not pm_rows.get("available") and pm.get("reason"):
            print(f"note: {pm.get('reason')}")

        print("\nSIGNAL")
        _table({
            "action": action,
            "reason": analysis.get("reason"),
            "price": _fmt_price(analysis.get("price")),
            "breakout_high": _fmt_price(analysis.get("breakout_high")),
            "adx_4h": analysis.get("adx_4h"),
            "rsi_1h": analysis.get("rsi_1h"),
            "volume_ratio": analysis.get("volume_ratio"),
        })

        print("\nTRADE / RISK")
        _table({
            "investment_usd": analysis.get("trade_amount_usd"),
            "leverage": f"{analysis.get('max_leverage')}x",
            "position_exposure_usd": analysis.get("position_exposure_usd", analysis.get("trade_amount_usd")),
            "max_loss_usd": analysis.get("max_loss_usd"),
            "strategy_stop": _fmt_price(analysis.get("strategy_stop")),
            "cash_risk_stop": _fmt_price(analysis.get("cash_risk_stop")),
            "stop": _fmt_price(analysis.get("stop")),
            "estimated_loss_usd": analysis.get("estimated_loss_usd"),
            "take_profit_1": _fmt_price(analysis.get("take_profit_1")),
            "take_profit_2": _fmt_price(analysis.get("take_profit_2")),
        })

        print("\nRESULT")
        print("BUY SIGNAL — NO ORDER PLACED" if action == "BUY" else "NO TRADE — NO ORDER PLACED")

        if args.json:
            print("\nRAW ANALYSIS")
            _dump({
                "symbol": symbol,
                "instrument_id": instrument_id,
                "live_price": live_price,
                "analysis": analysis,
            })
        return 0

    if strategy_name(symbol) != "GOLD_RANGE_SCALPER":
        raise SystemExit(
            f"signal is currently implemented for GOLD and BTC; {symbol} is not an active signal strategy."
        )

    # Fetch enough history for EMA(50), ADX(14), the 24h/96-bar range,
    # rejection candles and breakout momentum.
    raw_1h = client.get_candles(instrument_id, "OneHour", 120, direction="asc")
    raw_15m = client.get_candles(instrument_id, "FifteenMinutes", 120, direction="asc")
    candles_1h = to_candles(client.extract_candles(raw_1h))
    candles_15m = to_candles(client.extract_candles(raw_15m))

    if len(candles_1h) < 60:
        raise EtoroError(f"Not enough 1H candles returned for GOLD: {len(candles_1h)}")
    if len(candles_15m) < 100:
        raise EtoroError(f"Not enough 15m candles returned for GOLD: {len(candles_15m)}")

    cfg = ScalperConfig(
        leverage=20,
        max_leverage=20,
        trade_amount_usd=50.0,
        max_loss_usd=25.0,
    )
    analysis = analyze_gold(candles_1h, candles_15m, cfg)
    signal = analysis.get("signal") or {}
    action = signal.get("action", "WAIT")

    print("GOLD TRADING SIGNAL")
    print("=" * 64)
    _table({
        "market": symbol,
        "instrument_id": instrument_id,
        "live_price": _fmt_price(live_price),
        "analysis_candle_price": _fmt_price((analysis.get("regime") or {}).get("price")),
        "strategy": analysis.get("strategy"),
        "mode": analysis.get("mode"),
        "candles_1h": len(candles_1h),
        "candles_15m": len(candles_15m),
    })

    print("\nCONDITIONS")
    _table(_condition_rows(analysis))

    print("\nSIGNAL")
    _table({
        "action": action,
        "reason": signal.get("reason"),
        "zone_position_pct": signal.get("zone_position_pct"),
        "rsi14": signal.get("rsi14"),
        "range_low": _fmt_price(signal.get("range_low")),
        "range_high": _fmt_price(signal.get("range_high")),
    })

    # Risk budget is configured independently of whether a trade is triggered.
    exposure = cfg.trade_amount_usd * min(cfg.leverage, cfg.max_leverage)
    risk_rows = {
        "investment_usd": cfg.trade_amount_usd,
        "leverage": f"{min(cfg.leverage, cfg.max_leverage)}x",
        "position_exposure_usd": exposure,
        "max_loss_usd": cfg.max_loss_usd,
    }
    for key in ("strategy_stop", "risk_stop", "stop", "estimated_loss_usd",
                "stop_distance_pct", "tp1", "tp2"):
        if key in signal:
            risk_rows[key] = signal[key]

    print("\nTRADE / RISK")
    _table(risk_rows)

    print("\nRESULT")
    if action in {"BUY", "SELL"}:
        print(f"{action} SIGNAL — NO ORDER PLACED")
    elif action == "STAND_DOWN":
        print("STAND DOWN — NO ORDER PLACED")
    else:
        print("NO TRADE — NO ORDER PLACED")

    if args.json:
        print("\nRAW ANALYSIS")
        _dump({
            "symbol": symbol,
            "instrument_id": instrument_id,
            "live_price": live_price,
            "analysis": analysis,
        })
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    client = build_client()
    if not args.yes:
        raise SystemExit("Refusing to close a position without --yes (explicit confirmation).")
    _dump(client.close_position(
        args.position_id,
        instrument_id=args.instrument_id,
        units_to_deduct=args.units,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.py", description="eToro AutoTrader")
    parser.add_argument("--debug", action="store_true", help="verbose HTTP logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("settings", help="show resolved settings (secrets masked)").set_defaults(func=cmd_settings)
    sub.add_parser("check", help="verify credentials and connectivity").set_defaults(func=cmd_check)

    p = sub.add_parser("watchlists", help="list your watchlists")
    p.add_argument("--json", action="store_true", help="also dump the raw payload")
    p.set_defaults(func=cmd_watchlists)

    p = sub.add_parser("search", help="resolve symbol(s) to instrument ids")
    p.add_argument("symbol", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", help="raw GET against any API path")
    p.add_argument("path", help="e.g. /watchlists or /api/v1/market-data/search")
    p.add_argument("--param", action="append", help="query parameter key=value (repeatable)")
    p.add_argument("--json", action="store_true", help="also dump the raw JSON payload")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("order", help="open a market order (gated by dry-run + limits)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--amount", type=float, required=True, help="cash amount in USD")
    p.add_argument("--transaction", "--side", dest="transaction", choices=("buy", "sell", "BUY", "SELL"), default="buy")
    p.add_argument("--leverage", type=int, default=1)
    p.add_argument("--stop-loss", type=float)
    p.add_argument("--take-profit", type=float)
    p.add_argument("--yes", action="store_true", help="confirm you really mean it")
    p.add_argument("--json", action="store_true", help="also dump the raw JSON response")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("signal", help="analyze a live market signal without placing an order")
    p.add_argument("--symbol", required=True)
    p.add_argument("--json", action="store_true", help="also dump the raw signal analysis")
    p.set_defaults(func=cmd_signal)

    p = sub.add_parser("close", help="close a position (gated by dry-run)")
    p.add_argument("--position-id", type=int, required=True)
    p.add_argument("--instrument-id", type=int, required=True)
    p.add_argument("--units", type=float, help="omit for a full close")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true", help="also dump the raw JSON response")
    p.set_defaults(func=cmd_close)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except (ConfigError, EtoroDryRunBlocked) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2
    except EtoroError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
