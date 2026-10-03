# eToro AutoTrader

A Python-based trading framework for eToro, currently focused on **gold range/scalping signal collection, paper execution, risk controls, market-data monitoring, and trade journaling**.

> **Current stage: PAPER / validation.**
>
> The project is designed to collect and evaluate **1,000+ signals** before real-money trading is enabled. Do not treat the current live-execution code path as production-ready.

## What this project does

The current workflow is:

```
eToro market data
      ↓
Gold Range Scalper
      ↓
Signal / regime analysis
      ↓
Trading Gate
      ↓
Risk & position sizing
      ↓
Paper execution
      ↓
Journal / metrics / notifications
```

The project is deliberately separated into:

- **Signal generation** — identifies BUY / SELL / WAIT conditions.
- **Trading gate** — blocks trades when market conditions, connectivity, price freshness, spread, or kill-switch conditions are not satisfied.
- **Risk management** — calculates position size from account equity and stop distance.
- **Paper execution** — records simulated positions without sending real orders.
- **Market monitoring** — maintains a read-only eToro WebSocket connection and writes market state/heartbeat information.
- **Journaling** — records signals and executions for later analysis.
- **Notifications** — sends operational/trading alerts.

## Current validation objective

The immediate objective is:

1. Collect at least **1,000 deduplicated 15-minute signals**.
2. Record every signal, including WAIT / BUY / SELL.
3. Measure:
   - signal frequency
   - win rate
   - average return
   - maximum drawdown
   - risk/reward
   - false signals
   - performance by market regime
   - performance after spreads/cost assumptions
4. Only after validation, decide whether live execution should be enabled.

The collector is designed so that repeated evaluations of the same 15-minute candle do **not** create duplicate signal records.

## Project structure

```
.
├── app.py                       # eToro API CLI
├── scalper.py                   # Signal-only strategy runner
├── collect.py                   # Deduplicated signal collector
├── execute.py                   # Paper execution engine
├── market_monitor.py            # Read-only WebSocket market monitor
├── backtest.py                  # Backtesting utilities
├── perf.py                      # Performance utilities
├── report.py                    # Reporting
├── status.py                    # One-hour/status summary
├── position_monitor.py          # Position monitoring
├── notify_send.py               # Notification helper
│
├── etoro/
│   ├── __init__.py
│   ├── client.py                # eToro API client
│   └── config.py                # Configuration and environment loading
│
├── strategies/
│   ├── gold_range_scalper.py    # Main strategy
│   ├── indicators.py            # Technical indicators
│   ├── trading_gate.py          # Trade-entry gate
│   ├── risk.py                  # Position sizing/risk
│   ├── paper.py                 # Paper positions/fills
│   ├── live_exec.py             # eToro execution adapter
│   ├── live_positions.py        # Live-position state
│   ├── reconcile.py             # Position reconciliation
│   ├── killswitch.py            # Emergency trading controls
│   ├── notifier.py              # Notifications
│   ├── journal.py               # Signal journal
│   └── ...
│
├── journal/                     # Runtime state/logs - never commit
└── .gitignore
```

## Requirements

- Python 3.10+
- eToro API credentials
- Internet access
- Windows PowerShell or another Python-compatible shell

Install dependencies:

```powershell
cd C:\eToro-AutoTrader
py -m pip install -r requirements.txt
```

## Configuration

Create a local `env` or `.env` file in the project root.

**Never commit this file.**

Example structure:

```text
ETORO_API_KEY=<your-api-key>
ETORO_USER_KEY=<your-real-user-key>
ETORO_USER_KEY_demo=<your-demo-user-key>

ETORO_ACCOUNT=demo
ETORO_TRADING_MODE=paper
ETORO_DRY_RUN=true

ETORO_MAX_ORDER_USD=100
ETORO_MAX_POSITION_USD=500
ETORO_MAX_DAILY_LOSS_USD=50

ETORO_ALLOWED_SYMBOLS=GOLD
```

The application masks credential values when displaying configuration.

### Configuration precedence

Settings are loaded in this order:

1. Built-in defaults
2. `.env`
3. `env`
4. Process environment variables

Process environment variables have the highest priority.

## Verify the eToro connection

Run:

```powershell
cd C:\eToro-AutoTrader
py app.py check
```

This verifies credentials and API connectivity and displays whether the application is configured for demo/real account and paper/live mode.

You can also inspect the resolved configuration:

```powershell
py app.py settings
```

Secrets are masked.

## Generate a signal

The strategy runner is **signal-only**. It does not place an order.

```powershell
py scalper.py
```

JSON output:

```powershell
py scalper.py --json
```

For GOLD:

```powershell
py scalper.py --symbol GOLD
```

For GLD:

```powershell
py scalper.py --symbol GLD
```

A typical evaluation includes:

- 1H market regime
- ADX
- ATR
- Bollinger Band width
- EMA20 / EMA50
- 15-minute range
- range boundaries
- support/resistance touches
- breakout checks
- BUY / SELL / WAIT signal
- entry
- stop
- TP1 / TP2
- position sizing

## Collect signals

Run the collector:

```powershell
py collect.py
```

The collector evaluates the strategy but writes only **one journal record per new 15-minute candle**.

For a manual forced evaluation:

```powershell
py collect.py --force
```

The signal journal is stored under:

```
journal/
```

Runtime journal/state files are excluded from Git.

## Execute a paper trade

The execution engine is:

```powershell
py execute.py
```

It:

1. Fetches current candles.
2. Evaluates the strategy.
3. Uses the last **closed** 15-minute candle.
4. Checks idempotency.
5. Prevents stacking positions.
6. Evaluates the trading gate.
7. Applies kill-switch conditions.
8. Calculates risk-based position sizing.
9. Opens a **paper position** when all gates pass.
10. Records the result in the journal.

JSON output:

```powershell
py execute.py --json
```

Example with explicit equity/risk:

```powershell
py execute.py --equity 200 --risk-pct 0.01
```

### Important

`execute.py` is currently being used for the **paper-validation stage**.

A BUY/SELL signal alone does not guarantee a trade. The trading gate can intentionally block the trade because of:

- stale price data
- disconnected market monitor
- excessive spread
- kill-switch state
- wrong market regime
- other safety conditions

This is expected behaviour.

## Market monitor

The market monitor is a **read-only** WebSocket service.

Run a short test:

```powershell
py market_monitor.py --seconds 60
```

Run continuously:

```powershell
py market_monitor.py
```

Default instrument:

```
559 = GOLD
```

The monitor writes market state and heartbeat information under `journal/`.

If the monitor stops updating, the trading gate/watchdog can prevent new trades.

## Status

Check the current strategy/validation status:

```powershell
py status.py
```

This is useful for checking:

- number of collected signals
- open paper positions
- closed fills
- paper P&L
- latest signal
- current strategy mode

## API utilities

List watchlists:

```powershell
py app.py watchlists
```

Resolve symbols:

```powershell
py app.py search GOLD GLD
```

Raw GET request:

```powershell
py app.py get /watchlists
```

With a parameter:

```powershell
py app.py get /market-data/search --param internalSymbolFull=GOLD
```

## Risk controls

The project contains multiple layers of protection.

### Paper-first

The default configuration is:

```text
ETORO_ACCOUNT=demo
ETORO_TRADING_MODE=paper
ETORO_DRY_RUN=true
```

### Position sizing

Position size is calculated from:

```
account equity
risk per trade
entry price
stop price
leverage
```

The strategy does not average down after a stop.

### One position at a time

The execution runner refuses to stack another position when an existing paper position is open, unless explicitly forced.

### Idempotency

Execution is tied to the 15-minute candle so the same candle is not normally executed twice.

### Trading gate

The gate checks operational and market conditions before allowing a new trade.

### Kill switch

The project includes a kill-switch mechanism for abnormal operating conditions.

### Daily/order/position limits

Configuration supports:

```text
ETORO_MAX_ORDER_USD
ETORO_MAX_POSITION_USD
ETORO_MAX_DAILY_LOSS_USD
ETORO_ALLOWED_SYMBOLS
```

## Notifications

The project can send operational notifications for events such as:

- market-monitor problems
- paper trade openings
- emergency/watchdog conditions
- reconnection attempts

For example, a stale market heartbeat can produce an emergency notification and disable new trades until the data path recovers.

## Live trading

**Live trading is not the current validation target.**

The repository contains a live execution adapter, but the system should not be considered production-ready merely because the adapter exists.

Before real trading, the following should be completed and verified:

- 1,000+ signal collection
- backtesting
- paper-trading performance analysis
- realistic spread/slippage assumptions
- reconciliation testing
- order-status handling
- partial-fill handling
- position monitoring
- stop-loss/take-profit verification
- restart/recovery testing
- watchdog testing
- duplicate-order protection
- daily-loss protection
- small-size end-to-end broker testing

Never put API keys or user keys into Git.

## Useful daily workflow

### Start/restart the system

```powershell
cd C:\eToro-AutoTrader

py app.py check
py status.py
py market_monitor.py
```

In another terminal:

```powershell
cd C:\eToro-AutoTrader
py collect.py
```

And for a paper execution evaluation:

```powershell
py execute.py
```

For continuous collection, run `collect.py` from an external scheduler or task runner at an appropriate frequency. The collector itself prevents duplicate records for the same candle.

## Troubleshooting

### API connection failure

Run:

```powershell
py app.py check
```

Then:

```powershell
py app.py settings
```

Check that the credentials are present without exposing them.

### No trade

A BUY/SELL signal does not necessarily mean a trade should be opened.

Run:

```powershell
py execute.py --json
```

Inspect the `gate` section for the blocking condition.

### Stale market data

Check:

```powershell
py market_monitor.py --seconds 60
```

Then inspect the runtime state under:

```
journal/
```

A stale heartbeat/price condition is intentionally treated as a reason to disable new trades.

### No signal

This is not necessarily an error. The strategy is designed to stand aside when the market does not satisfy its range/regime conditions.

Run:

```powershell
py scalper.py
```

to see the individual gates and the reason for the current signal.

## Git and secrets

The repository should contain source code and documentation, **not credentials or runtime state**.

The following are ignored:

```text
.env
*.env
env
*.pem
*.key
.venv/
__pycache__/
logs/
*.log
state/
journal/
*.bak*
```

Before every push, check:

```powershell
git status
git grep -n -i "api.key\|api_key\|user.key\|user_key\|bearer\|secret\|token"
```

Also review Git history if credentials may ever have been committed.

## Development principles

1. **Paper before live.**
2. **Closed candles only for signal decisions.**
3. **No averaging down.**
4. **No duplicate execution for the same candle.**
5. **No trade when market data is stale.**
6. **No trade when the trading gate blocks it.**
7. **Keep broker credentials out of Git.**
8. **Record decisions so results can be independently analysed.**
9. **Treat broker HTTP acceptance as different from confirmed execution/fill.**
10. **Validate the strategy statistically before increasing risk.**

## eToro API

The application uses the eToro Public API.

API documentation:

https://api-portal.etoro.com/

The API client is implemented in:

```
etoro/client.py
etoro/config.py
```

## Project status

**Stage A — Paper validation**

- [x] eToro API connectivity
- [x] credential configuration
- [x] market-data retrieval
- [x] Gold strategy
- [x] signal journal
- [x] risk sizing
- [x] trading gate
- [x] paper positions
- [x] market monitor
- [x] watchdog/notification architecture
- [ ] 1,000+ signal dataset
- [ ] statistical validation
- [ ] robust backtest validation
- [ ] full paper-trading performance report
- [ ] production live-execution validation

**Real-money trading should remain disabled until the validation stage is complete.**
