"""Run a no-order SPY/GLD fixed-ratio rebalancing historical backtest.

Examples (PowerShell):
  py rebalancing_backtest.py --download --period 10y
  py rebalancing_backtest.py --spy-csv data/spy.csv --gld-csv data/gld.csv
  py rebalancing_backtest.py --download --period 20y --initial-capital 100000 --cost-bps 5
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from strategies.fixed_ratio_rebalancer import PricePoint, backtest_rebalancing


def configure_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("rebalancing_backtest")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file_handler = logging.FileHandler(output_dir / "backtest.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def read_csv(path: Path, label: str) -> List[PricePoint]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"{path}: CSV has no header")
        lookup = {name.strip().lower(): name for name in reader.fieldnames}
        date_col = lookup.get("date") or lookup.get("datetime")
        price_col = lookup.get("adj close") or lookup.get("adjusted close") or lookup.get("adj_close") or lookup.get("close")
        if not date_col or not price_col:
            raise ValueError(f"{path}: needs Date and Adj Close (preferred) or Close columns")
        rows = []
        for row in reader:
            date_value = str(row.get(date_col, "")).strip()[:10]
            price_value = str(row.get(price_col, "")).strip().replace(",", "")
            if not date_value or not price_value or price_value.lower() in {"nan", "none", "null"}:
                continue
            rows.append(PricePoint(date_value, float(price_value)))
    if len(rows) < 2:
        raise ValueError(f"{path}: fewer than two valid prices")
    return rows


def download_yfinance(symbol: str, period: str) -> List[PricePoint]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Download mode needs yfinance. Install with: py -m pip install yfinance") from exc

    data = yf.download(symbol, period=period, interval="1d", auto_adjust=True,
                       progress=False, actions=False, threads=False)
    if data is None or data.empty:
        raise RuntimeError(f"No daily data returned for {symbol}")
    # yfinance may return MultiIndex columns even for one ticker.
    if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
        data.columns = data.columns.get_level_values(0)
    if "Close" not in data.columns:
        raise RuntimeError(f"Download for {symbol} has no Close column")
    rows = []
    for index, value in data["Close"].items():
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            rows.append(PricePoint(str(index.date()), price))
    if len(rows) < 2:
        raise RuntimeError(f"Fewer than two daily closes downloaded for {symbol}")
    return rows


def write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Historical SPY/GLD fixed-ratio rebalancing backtest (no orders).")
    parser.add_argument("--spy-csv", type=Path, help="SPY daily CSV with Date and Adj Close/Close")
    parser.add_argument("--gld-csv", type=Path, help="GLD daily CSV with Date and Adj Close/Close")
    parser.add_argument("--download", action="store_true", help="Download adjusted daily closes using optional yfinance")
    parser.add_argument("--period", default="10y", help="yfinance history period, e.g. 5y, 10y, 20y, max (default 10y)")
    parser.add_argument("--initial-capital", type=float, default=100000.0)
    parser.add_argument("--cost-bps", type=float, default=5.0, help="transaction cost per dollar traded in basis points (default 5)")
    parser.add_argument("--spy-weight", type=float, default=0.5)
    parser.add_argument("--gld-weight", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=ROOT / "journal" / "rebalancing_backtest")
    args = parser.parse_args(argv)

    logger = configure_logging(args.output)
    try:
        if args.download:
            if args.spy_csv or args.gld_csv:
                raise ValueError("Use either --download or CSV input, not both")
            logger.info("Downloading SPY and GLD adjusted daily prices (read-only; no broker calls)")
            spy_rows = download_yfinance("SPY", args.period)
            gld_rows = download_yfinance("GLD", args.period)
        else:
            if not args.spy_csv or not args.gld_csv:
                parser.error("provide --spy-csv and --gld-csv, or use --download")
            spy_rows = read_csv(args.spy_csv, "SPY")
            gld_rows = read_csv(args.gld_csv, "GLD")

        report = backtest_rebalancing(
            spy_rows, gld_rows,
            initial_capital=args.initial_capital,
            target_spy=args.spy_weight,
            target_gld=args.gld_weight,
            cost_bps=args.cost_bps,
        )
        report["data_source"] = "yfinance auto-adjusted daily closes" if args.download else "user-provided CSV closes"
        report["notes"] = [
            "This is a historical simulation, not a forecast or investment recommendation.",
            "CSV input should use dividend-adjusted/total-return-like closes where available; plain Close omits cash distributions.",
            "Daily closing-price fills are a simplifying assumption; spreads, tax, FX, tracking error, and fund-specific costs may differ.",
            "No eToro API, order endpoint, live account, or paper-order endpoint is called.",
        ]
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        write_csv(args.output / "daily_equity.csv", report["daily"])
        write_csv(args.output / "trade_log.csv", report["trades"])

        m = report["metrics"]
        logger.info("Period: %s to %s (%s observations)", m["start_date"], m["end_date"], m["observations"])
        logger.info("Daily 50/50 rebalancing: final=$%s return=%s%% CAGR=%s%% maxDD=%s%%",
                    f'{m["final_equity"]:,.2f}', m["total_return_pct"], m["cagr_pct"], m["max_drawdown_pct"])
        logger.info("Costs=$%s turnover=$%s (%sx initial capital)",
                    f'{report["total_costs_dollars"]:,.2f}',
                    f'{report["total_turnover_dollars"]:,.2f}',
                    report["turnover_multiple_of_initial_capital"])
        for name, bm in report["benchmarks"].items():
            logger.info("%s: final=$%s return=%s%% CAGR=%s%% maxDD=%s%%",
                        name, f'{bm["final_equity"]:,.2f}', bm["total_return_pct"],
                        bm["cagr_pct"], bm["max_drawdown_pct"])
        logger.info("Saved report, daily equity, trade log, and log under %s", args.output)
        return 0
    except Exception as exc:
        logger.exception("Backtest failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
