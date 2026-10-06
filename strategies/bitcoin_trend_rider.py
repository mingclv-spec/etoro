"""BTC Trend Rider v1.0 - signal-only strategy.

Designed for Bitcoin's 24/7 market. It never places an order.
Use paper/demo first and collect at least 1,000 signals before live use.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Sequence
from .indicators import Candle, adx, atr, ema, rsi

VERSION = "1.0"

@dataclass(frozen=True)
class BTCConfig:
    trend_ema_fast: int = 50
    trend_ema_slow: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    rsi_period: int = 14
    rsi_min: float = 55.0
    rsi_max: float = 75.0
    breakout_bars: int = 20
    volume_period: int = 20
    volume_multiplier: float = 1.20
    atr_period: int = 14
    stop_atr: float = 2.0
    tp1_r: float = 1.5
    tp2_r: float = 3.0
    risk_fraction: float = 0.005
    max_leverage: int = 3

def signal(candles_4h: Sequence[Candle], candles_1h: Sequence[Candle],
           candles_15m: Sequence[Candle], cfg: BTCConfig = BTCConfig()) -> dict:
    if len(candles_4h) < cfg.trend_ema_slow or len(candles_1h) < max(cfg.breakout_bars, 30):
        return {"strategy": "BTC_TREND_RIDER", "version": VERSION, "action": "WAIT",
                "reason": "insufficient candle history"}
    price = candles_1h[-1].close
    ema50_4h = ema([c.close for c in candles_4h], cfg.trend_ema_fast)
    ema200_4h = ema([c.close for c in candles_4h], cfg.trend_ema_slow)
    adx4h = adx(candles_4h, cfg.adx_period)
    ema50_1h = ema([c.close for c in candles_1h], cfg.trend_ema_fast)
    ema200_1h = ema([c.close for c in candles_1h], cfg.trend_ema_slow)
    r = rsi(candles_1h, cfg.rsi_period)
    a = atr(candles_1h, cfg.atr_period)
    prior = list(candles_1h)[-(cfg.breakout_bars + 1):-1]
    breakout_high = max(c.high for c in prior)
    breakout = price > breakout_high
    vols = [c.volume for c in candles_1h[-(cfg.volume_period + 1):-1]]
    avg_volume = sum(vols) / len(vols) if vols and any(vols) else 0.0
    volume_ratio = candles_1h[-1].volume / avg_volume if avg_volume else None
    volume_ok = volume_ratio is not None and volume_ratio >= cfg.volume_multiplier
    trend_ok = ema50_4h is not None and ema200_4h is not None and price > ema50_4h > ema200_4h
    trend_1h_ok = ema50_1h is not None and ema200_1h is not None and price > ema50_1h > ema200_1h
    strength_ok = adx4h is not None and adx4h > cfg.adx_min
    momentum_ok = r is not None and cfg.rsi_min <= r <= cfg.rsi_max
    score = sum([trend_ok, trend_1h_ok, strength_ok, breakout, volume_ok, momentum_ok])
    if score >= 5 and a:
        stop = price - cfg.stop_atr * a
        risk = price - stop
        return {"strategy":"BTC_TREND_RIDER","version":VERSION,"action":"BUY","score":score,
                "price":price,"stop_loss":round(stop,2),
                "take_profit_1":round(price+cfg.tp1_r*risk,2),
                "take_profit_2":round(price+cfg.tp2_r*risk,2),
                "atr":a,"adx_4h":adx4h,"rsi_1h":r,"volume_ratio":volume_ratio,
                "max_leverage":cfg.max_leverage,"risk_fraction":cfg.risk_fraction}
    return {"strategy":"BTC_TREND_RIDER","version":VERSION,"action":"STAND_ASIDE",
            "score":score,"price":price,"adx_4h":adx4h,"rsi_1h":r,
            "volume_ratio":volume_ratio,
            "checks":{"trend_4h":trend_ok,"trend_1h":trend_1h_ok,"adx":strength_ok,
                      "breakout":breakout,"volume":volume_ok,"rsi":momentum_ok}}

def describe(cfg: BTCConfig = BTCConfig()) -> dict:
    return {"name":"BTC Trend Rider","version":VERSION,"asset":"BTC",
            "timeframes":["4H","1H","15m"],"config":asdict(cfg)}
