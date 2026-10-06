"""Command-line entry point for the eToro AutoTrader package.

Run from the repository root, for example:
    python -m etoro.cli --account demo check
    python -m etoro.cli --account demo order --symbol XAUUSD --side BUY --amount 500 --leverage 20 --yes

The account flag selects the matching user key. Demo and real credentials are
never mixed.
"""
from __future__ import annotations

import os
import sys

from app import main as app_main


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Account switching is deliberately an environment override for this
    # process only. It never edits .env/env.
    if "--account" in args:
        i = args.index("--account")
        if i + 1 >= len(args):
            raise SystemExit("--account requires demo or real")
        account = args[i + 1].strip().lower()
        if account not in {"demo", "real"}:
            raise SystemExit("--account must be exactly demo or real")
        os.environ["ETORO_ACCOUNT"] = account
        del args[i:i + 2]

    # Real-account execution requires an explicit --live flag. The flag is
    # intentionally left in argv so the order subcommand can verify it.
    if "--live" in args:
        account = os.environ.get("ETORO_ACCOUNT", "demo").strip().lower()
        if account != "real":
            raise SystemExit("--live is only allowed with --account real")
        os.environ["ETORO_TRADING_MODE"] = "live"
        os.environ["ETORO_DRY_RUN"] = "false"

    return app_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
