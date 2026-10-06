"""Command line entry point for the eToro AutoTrader app."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from etoro import EtoroDryRunBlocked, EtoroError, __version__, build_client
from etoro.config import ConfigError, load_settings


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
