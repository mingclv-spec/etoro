# eToro AutoTrader — Three Strategy System v1.0

| Asset | Strategy | Default |
|---|---|---|
| GOLD / XAU | Gold Range Scalper | Range + breakout, long/short |
| BTC / Bitcoin | BTC Trend Rider | Long only |
| Everything else | Universal Trend Momentum | Long only |

## GOLD
The existing gold_range_scalper.py remains the production candidate for GOLD.
It uses a 1H range regime, a dynamic 15m range, rejection entries, partial
take profits, mandatory stops and a breakout mode.

## BTC
bitcoin_trend_rider.py:
- 4H bullish regime: price > EMA50 > EMA200
- 4H ADX > 20
- 1H bullish EMA structure
- 1H 20-bar breakout
- RSI 55-75
- volume >= 1.2x average when volume is available
- BUY only when at least 5 of 6 checks pass
- stop = 2x ATR
- TP1 = 1.5R, TP2 = 3R
- max leverage = 3x

## Everything else
universal_trend_momentum.py:
- price > EMA20 > EMA50 > EMA200
- ADX >= 20
- RSI 52-72
- 20-bar breakout
- BUY when at least 3 of 4 checks pass
- stop = 2x ATR
- TP1 = 1.5R, TP2 = 3R
- max leverage = 2x
- long only for v1

## Safety
These strategy modules are signal-only. They do not place orders.
Instrument discovery remains dynamic through eToro market data; the router only
chooses the strategy. Start with paper/demo execution and record at least 1,000
signals before considering real-money deployment. Keep the existing order,
position and daily-loss risk limits active.
