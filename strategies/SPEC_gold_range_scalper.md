# Adaptive Gold Range Scalper v0.1 - SPEC + IMPLEMENTATION

Author: Ming (2026-10-02). Built signal-only in `C:\eToro-AutoTrader`.

## 1. Regime: RANGE or TREND (1H filter)
Activate range strategy only when ALL hold: ADX < 20-22, EMA20/EMA50 flat,
both boundaries touched >=2x, range width > minimum, no major macro event.

## 2. Dynamic range (96 x 15m = ~24h)
- Range High = highest high, Range Low = lowest low.
- Do not blindly trust the extremes: each boundary must be TOUCHED >= 2x.

Zones by position within the range:
```
        RANGE HIGH
        ----------------
        SELL ZONE      85-100%
        MIDPOINT
        BUY ZONE        0-15%
        ----------------
        RANGE LOW
```

## 3. Entry rules (15m)
BUY : bottom 15% AND RSI(14)<40 AND bullish rejection AND ADX<22 AND no breakout.
SELL: top 15%    AND RSI(14)>60 AND bearish rejection AND ADX<22 AND no breakout.
RSI 40/60 deliberately - 30/70 enters too late for a range strategy.

## 4. Take profit (partial)
TP1 = 45% of range, TP2 = 75% of range, 50% of position at each.
Worked example: range 4100-4200, buy 4115 -> TP1 4145, TP2 4175.

## 5. Stop loss (mandatory)
Just outside the boundary. Example: buy 4115, range low 4100 -> stop 4092.
If gold breaks the range, the range assumption was wrong. Never average down.

## 6. BREAKOUT MODE (most important)
15m close outside range AND move > 0.5 x ATR AND momentum expands
-> STOP ALL RANGE TRADES. Never fade an overbought breakout.

## 7. Position sizing
Size from ACCOUNT RISK, never "always $100 at 20x".
Account 5000, risk 0.25% -> max loss $12.50; size derived from stop distance.
At 20x, a 0.5% gold move is ~10% of the committed MARGIN.

## 8. No overnight positions
Entry -> TP/SL -> close. Avoids financing and keeps the backtest clean.

## 9. Architecture + journal
Market Data -> Regime Detector -> Range Scalper -> Signal -> Risk -> Sizing
-> eToro API -> Order Manager -> Position Monitor -> Trade Journal.

---

## Implementation status
- [x] `strategies/indicators.py` - EMA, ATR, ADX, RSI, Bollinger width, touches, rejection
- [x] `strategies/gold_range_scalper.py` - regime, range, zones, entries, TP, stop, breakout
- [x] `strategies/risk.py` - risk-based position sizing
- [x] `strategies/journal.py` - JSONL trade journal
- [x] `scalper.py` - CLI runner (**signal-only, never places orders**)
- [x] breakout mode overrides range mode entirely
- [ ] macro-event guard fed from a local events file (manual `--macro-block` for now)
- [ ] eToro gold financing/rollover time confirmed (for the no-overnight rule)
- [ ] final `min_range_width_pct` (provisional 0.40%)

## Verified against spec examples
- range 4100-4200, buy 4115 -> TP1 4145, TP2 4175, stop 4091.8  (spec: 4145/4175/4092)
- account 5000, risk 0.25% -> risk $12.50, notional $2217, margin $110.86 at 20x
- 2000 notional: +0.5% -> +10, -1% -> -20, -5% -> -100  (matches spec table)

## Run
```
python scalper.py
python scalper.py --json
python scalper.py --symbol GLD --account-equity 5000 --risk-pct 0.0025
python scalper.py --macro-block          # veto range mode around data
python scalper.py --boundary-mode validated
```
Journal: `C:\eToro-AutoTrader\journal\signals.jsonl`
