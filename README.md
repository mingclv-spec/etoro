# eToro AutoTrader

A small, safe Python app on top of the **eToro Public API** (agent keys).

```
eToro-AutoTrader/
  app.py               CLI entry point (check / watchlists / search / get / order / close)
  test_connection.py   the original smoke test - now working
  etoro/
    __init__.py        package exports
    config.py          loads settings from .env / env + process environment
    client.py          EtoroClient: auth headers, retries, rate limiting, trade guards
  env                  your credentials and limits (never commit)
  requirements.txt
  .gitignore
```

## Setup

```powershell
cd C:\eToro-AutoTrader
python -m pip install -r requirements.txt
python test_connection.py
```

## Credentials

Both of these are read, in this order: **`.env` then `env`**, and any value already
set in the process environment wins. Your current file is `env`, so nothing needed
to move - but you can rename it to `.env` any time and it still works.

| Variable | Meaning |
| --- | --- |
| `ETORO_API_KEY` | Public API Key (`x-api-key`) |
| `ETORO_USER_KEY` | User Key (`x-user-key`) |
| `ETORO_DRY_RUN` | `true` = never send a real order (keep this true until ready) |
| `ETORO_ACCOUNT` | `demo` or `real` |
| `ETORO_MAX_ORDER_USD` | hard cap per order |
| `ETORO_MAX_POSITION_USD` | hard cap per position |
| `ETORO_MAX_DAILY_LOSS_USD` | daily loss budget |
| `ETORO_ALLOWED_SYMBOLS` | allow-list, comma separated (`TSLA,SPY,QQQ,GLD`) |

Secrets are never printed: `Settings.describe()` returns `<set: 63 chars>` style markers.

## Usage

```powershell
python app.py check                       # credentials + live API ping
python app.py settings                    # resolved config, secrets masked
python app.py watchlists --json           # list watchlists
python app.py search TSLA NVDA            # symbol -> instrumentId
python app.py get /watchlists             # arbitrary path
python app.py get /market-data/search --param internalSymbolFull=BTC
```

Trading commands exist but are **blocked by design** while dry-run is on:

```powershell
python app.py order --symbol TSLA --amount 25 --yes
# blocked: Dry-run is ON (ETORO_DRY_RUN=true). No order was sent.

python app.py close --position-id 12345678 --instrument-id 1111 --yes
```

## Safety model

1. **Dry-run first.** `open_market_order()` and `close_position()` raise
   `EtoroDryRunBlocked` unless `ETORO_DRY_RUN=false`.
2. **Risk limits.** Every order is checked against `ETORO_MAX_ORDER_USD`; any
   symbol outside `ETORO_ALLOWED_SYMBOLS` is rejected.
3. **No accidental double orders.** Only GET/HEAD are auto-retried. POST/DELETE
   are never retried, so a flaky network cannot duplicate a trade.
4. **Explicit confirmation.** The CLI requires `--yes`.
5. **Secrets stay local.** `.gitignore` excludes `.env` / `env`; keys live in
   headers only and are never logged.

## API reference notes

* Base host: `https://public-api.etoro.com`, versioned paths under `/api/v1`
  (orders also exist as `/api/v2/trading/execution/orders`).
* Headers on every call: `x-api-key`, `x-user-key`, `x-request-id` (fresh UUID).
* Rate limits: default shared quota is 60 requests / 60 s; market data 120 / 60 s.
  The client backs off automatically on `429` and honours `Retry-After`.
* Useful paths:
  * `GET /watchlists`
  * `GET /market-data/search?internalSymbolFull=TSLA`
  * `POST /api/v2/trading/execution/orders` - open by `amount`, `units` or `contracts`
  * `POST /api/v1/trading/execution/market-close-orders/positions/{positionId}` - close
* Full docs: <https://api-portal.etoro.com>

## Library use

```python
from etoro import load_settings, EtoroClient

settings = load_settings()
client = EtoroClient(settings)

print(client.get_watchlists())
print(client.resolve_instrument_id("TSLA"))

# blocked while dry-run is on:
# client.open_market_order("TSLA", 25)
```

## Extending

The strategy scaffold in `C:\eToro-RSI-Bot` (`config.py`, `strategy.py`,
`risk.py`, `main.py`) is empty - it can be wired onto `EtoroClient` directly:

```python
from etoro import build_client
client = build_client()   # reads env / .env
```
