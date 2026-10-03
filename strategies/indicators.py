"""Pure-Python technical indicators for the Adaptive Gold Range Scalper v0.1.

No third-party dependencies - plain Python only, so it runs wherever the
eToro AutoTrader app runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class Candle:
    """One OHLC candle. `time` is the exchange `fromDate` string."""

    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def to_candles(rows: Sequence[dict]) -> List[Candle]:
    """Convert raw API dicts into Candle objects, oldest-first."""
    out: List[Candle] = []
    for row in rows:
        out.append(
            Candle(
                time=str(row.get("fromDate") or row.get("time") or ""),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            )
        )
    return out


def ema_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Exponential moving average; index-aligned, None before the seed."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if period <= 0 or n < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1.0)
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def ema(values: Sequence[float], period: int) -> Optional[float]:
    series = ema_series(values, period)
    return series[-1] if series else None


def true_ranges(candles: Sequence[Candle]) -> List[float]:
    trs: List[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c.high - c.low)
        else:
            prev_close = candles[i - 1].close
            trs.append(
                max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
            )
    return trs


def _wilder(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Wilder's smoothing (used by ATR, ADX and RSI)."""
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Average True Range (Wilder)."""
    smoothed = _wilder(true_ranges(candles), period)
    return smoothed[-1] if smoothed else None


def adx(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Average Directional Index (Wilder). Returns None if not enough data."""
    if len(candles) < period * 2 + 1:
        return None
    plus_dm: List[float] = []
    minus_dm: List[float] = []
    for i in range(1, len(candles)):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    trs = true_ranges(candles)[1:]
    atr_s = _wilder(trs, period)
    plus_s = _wilder(plus_dm, period)
    minus_s = _wilder(minus_dm, period)
    dxs: List[float] = []
    for i in range(len(trs)):
        a, p, m = atr_s[i], plus_s[i], minus_s[i]
        if a in (None, 0) or p is None or m is None:
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        denom = pdi + mdi
        if denom == 0:
            continue
        dxs.append(100.0 * abs(pdi - mdi) / denom)
    if len(dxs) < period:
        return None
    value = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        value = (value * (period - 1) + dx) / period
    return value


def rsi(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Relative Strength Index (Wilder)."""
    closes = [c.close for c in candles]
    if len(closes) < period + 1:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger_width(
    candles: Sequence[Candle], period: int = 20, mult: float = 2.0
) -> Optional[float]:
    """Band width as a fraction of the mid band (relative width)."""
    closes = [c.close for c in candles]
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    if mid == 0:
        return None
    var = sum((x - mid) ** 2 for x in window) / period
    return (2.0 * mult * (var ** 0.5)) / mid


def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old


def window_high_low(candles: Sequence[Candle], lookback: int):
    window = list(candles)[-lookback:] if lookback > 0 else list(candles)
    if not window:
        return None, None
    return max(c.high for c in window), min(c.low for c in window)


def count_touches(
    candles: Sequence[Candle], level: float, tolerance: float, side: str
) -> int:
    """How many candles probed a level. side: 'support' (lows) or 'resistance' (highs)."""
    hits = 0
    for c in candles:
        probe = c.low if side == "support" else c.high
        if abs(probe - level) <= tolerance:
            hits += 1
    return hits


def candle_anatomy(c: Candle) -> dict:
    """Decompose a candle into body and wicks (absolute and as fractions of range)."""
    rng = c.high - c.low
    body = abs(c.close - c.open)
    upper_wick = c.high - max(c.open, c.close)
    lower_wick = min(c.open, c.close) - c.low
    if rng <= 0:
        return {
            "range": 0.0, "body": body, "upper_wick": upper_wick, "lower_wick": lower_wick,
            "upper_wick_frac": 0.0, "lower_wick_frac": 0.0, "close_pos": 0.5,
        }
    return {
        "range": rng,
        "body": body,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "upper_wick_frac": upper_wick / rng,
        "lower_wick_frac": lower_wick / rng,
        "close_pos": (c.close - c.low) / rng,   # 0 = closed at low, 1 = closed at high
    }


def is_bullish_rejection(c: Candle, wick_frac: float = 0.45, close_pos: float = 0.50) -> bool:
    """Rejection of LOWER prices: long lower wick, close back in the upper part."""
    a = candle_anatomy(c)
    return a["lower_wick_frac"] >= wick_frac and a["close_pos"] >= close_pos


def is_bearish_rejection(c: Candle, wick_frac: float = 0.45, close_pos: float = 0.50) -> bool:
    """Rejection of HIGHER prices: long upper wick, close back in the lower part."""
    a = candle_anatomy(c)
    return a["upper_wick_frac"] >= wick_frac and a["close_pos"] <= (1.0 - close_pos)
