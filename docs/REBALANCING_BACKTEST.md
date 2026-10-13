# SPY / GLD fixed-ratio rebalancing backtest

This is an independent research/backtest module. It does not import the eToro client, strategy router, live execution adapter, or paper order engine, and it never sends orders.

## Model

- Assets: SPY and GLD.
- Target: 50% SPY / 50% GLD.
- Frequency: daily close.
- Initial capital: USD 100,000 by default.
- Transaction cost: 5 basis points per dollar traded by default; configure with `--cost-bps`.
- Benchmarks: SPY buy-and-hold, GLD buy-and-hold, and static initial 50/50 buy-and-hold.
- Metrics: final equity, net P&L, total return, CAGR, annualized daily volatility, Sharpe ratio (risk-free rate 0), max drawdown, turnover, and transaction costs.
- Outputs: JSON report, daily equity CSV, trade/turnover CSV, and log file.

### No-look-ahead timing

The return from close(t-1) to close(t) is earned by weights established after close(t-1). Only after that return is booked does the simulator calculate drifted weights and apply the 50/50 rebalance and transaction cost. The new 50/50 weights apply to the next return interval.

This avoids using close(t) to decide the weights that earned the return ending at close(t). A closing-price fill is still a simplified execution assumption, not a guarantee of a real broker fill.

## Data

Use adjusted/total-return-like closes when available, because plain ETF closing prices omit cash distributions. CSV input should have a `Date` column and preferably `Adj Close`; `Close` is accepted as a fallback but may understate returns. SPY and GLD are aligned on dates shared by both series.

Optional Yahoo Finance downloader uses `yfinance` with `auto_adjust=True`. It is optional and is not added to the project's required dependencies.

## Run on Windows PowerShell

From `C:\eToro-AutoTrader`:

```powershell
# Optional downloader dependency
py -m pip install yfinance

# Historical test, default 10 years
py rebalancing_backtest.py --download --period 10y

# Longer sample
py rebalancing_backtest.py --download --period 20y --initial-capital 100000 --cost-bps 5

# Or use local CSVs (Date,Adj Close or Date,Close)
py rebalancing_backtest.py --spy-csv data\spy.csv --gld-csv data\gld.csv --cost-bps 5
```

Outputs default to `journal\rebalancing_backtest\`:
- `report.json`
- `daily_equity.csv`
- `trade_log.csv`
- `backtest.log`

The `journal\` folder is excluded from Git. You can choose a different output directory with `--output`.

## Tests

```powershell
py -m unittest discover -s tests -p "test_fixed_ratio_rebalancer.py" -v
```

## Interpretation and limitations

- The backtest is historical research, not a forecast or investment recommendation.
- The default 5 bps is a sensitivity assumption, not a verified eToro fee quote. Re-run with multiple values (for example 0, 5, 10, 25 bps).
- It does not model tax, AUD/USD FX conversion, bid/ask changes, tracking error, market impact, or actual broker execution.
- It assumes a fully invested, unleveraged portfolio and no external deposits or withdrawals.
- This module is intentionally not registered as a GOLD/BTC strategy and does not change their existing risk controls.
- Historical results depend on the selected period and data vendor. Compare multiple periods and cost assumptions before drawing conclusions.
