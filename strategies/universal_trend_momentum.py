"""Universal Trend Momentum v1.0 for non-GOLD/non-BTC instruments."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Sequence
from .indicators import Candle, adx, atr, ema, rsi

VERSION = "1.0"

@dataclass(frozen=True)
class UniversalConfig:
    fast_ema: int = 20
    slow_ema: int = 50
    regime_ema: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    rsi_period: int = 14
    rsi_min: float = 52.0
    rsi_max: float = 72.0
    breakout_bars: int = 20
    atr_period: int = 14
    stop_atr: float = 2.0
    tp1_r: float = 1.5
    tp2_r: float = 3.0
    max_leverage: int = 2

def signal(candles: Sequence[Candle], cfg: UniversalConfig = UniversalConfig()) -> dict:
    minimum=max(cfg.regime_ema,cfg.breakout_bars+1,30)
    if len(candles)<minimum:
        return {"strategy":"UNIVERSAL_TREND_MOMENTUM","version":VERSION,"action":"WAIT",
                "reason":"insufficient candle history"}
    price=candles[-1].close
    closes=[c.close for c in candles]
    fast=ema(closes,cfg.fast_ema); slow=ema(closes,cfg.slow_ema); regime=ema(closes,cfg.regime_ema)
    strength=adx(candles,cfg.adx_period); momentum=rsi(candles,cfg.rsi_period); volatility=atr(candles,cfg.atr_period)
    prior=candles[-(cfg.breakout_bars+1):-1]
    breakout=price>max(c.high for c in prior)
    trend=fast is not None and slow is not None and regime is not None and price>fast>slow>regime
    strength_ok=strength is not None and strength>=cfg.adx_min
    momentum_ok=momentum is not None and cfg.rsi_min<=momentum<=cfg.rsi_max
    score=sum([trend,strength_ok,momentum_ok,breakout])
    if score>=3 and volatility:
        stop=price-cfg.stop_atr*volatility; risk=price-stop
        return {"strategy":"UNIVERSAL_TREND_MOMENTUM","version":VERSION,"action":"BUY","score":score,
                "price":price,"stop_loss":round(stop,4),
                "take_profit_1":round(price+cfg.tp1_r*risk,4),
                "take_profit_2":round(price+cfg.tp2_r*risk,4),
                "adx":strength,"rsi":momentum,"atr":volatility,"max_leverage":cfg.max_leverage}
    return {"strategy":"UNIVERSAL_TREND_MOMENTUM","version":VERSION,"action":"STAND_ASIDE",
            "score":score,"price":price,
            "checks":{"trend":trend,"adx":strength_ok,"rsi":momentum_ok,"breakout":breakout},
            "adx":strength,"rsi":momentum}

def describe(cfg: UniversalConfig = UniversalConfig()) -> dict:
    return {"name":"Universal Trend Momentum","version":VERSION,
            "asset_class":"everything except GOLD/BTC","config":asdict(cfg)}
