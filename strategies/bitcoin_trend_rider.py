"""BTC Trend Rider v1.1 - technical trend + Polymarket confirmation.

Signal-only strategy. It never places an order.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping, Optional, Sequence

from .indicators import Candle, adx, atr, ema, rsi

VERSION = "1.1"


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
    max_leverage: int = 1
    trade_amount_usd: float = 20.0
    max_loss_usd: float = 10.0
    polymarket_min_probability: float = 0.30
    polymarket_block_probability: float = 0.40
    polymarket_confirm_probability: float = 0.60


def _risk_block(price: float, stop: float, cfg: BTCConfig) -> dict[str, Any]:
    exposure = cfg.trade_amount_usd * min(cfg.max_leverage, 1)
    cash_stop = price * max(0.0, 1.0 - min(0.99, cfg.max_loss_usd / exposure))
    effective_stop = max(stop, cash_stop)
    risk_pct = abs(price - effective_stop) / price
    return {
        "investment_usd": cfg.trade_amount_usd,
        "leverage": min(cfg.max_leverage, 1),
        "position_exposure_usd": exposure,
        "max_loss_usd": cfg.max_loss_usd,
        "strategy_stop": round(stop, 2),
        "cash_risk_stop": round(cash_stop, 2),
        "stop": round(effective_stop, 2),
        "estimated_loss_usd": round(exposure * risk_pct, 2),
        "stop_distance_pct": round(risk_pct * 100.0, 3),
    }


def signal(
    candles_4h: Sequence[Candle],
    candles_1h: Sequence[Candle],
    candles_15m: Sequence[Candle],
    cfg: BTCConfig = BTCConfig(),
    polymarket: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Analyze BTC trend and optionally use Polymarket as confirmation."""
    min_1h = max(cfg.trend_ema_slow, cfg.breakout_bars + 1, 30)
    if len(candles_4h) < cfg.trend_ema_slow or len(candles_1h) < min_1h:
        return {
            "strategy": "BTC_TREND_RIDER",
            "version": VERSION,
            "action": "WAIT",
            "reason": "insufficient candle history",
        }

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

    technical_score = sum(
        [trend_ok, trend_1h_ok, strength_ok, breakout, volume_ok, momentum_ok]
    )

    pm = dict(polymarket or {})
    pm_available = bool(pm.get("available"))
    pm_up = float(pm["up_probability"]) if pm_available and pm.get("up_probability") is not None else None
    pm_score = int(pm.get("score", 0)) if pm_available else 0

    # Polymarket confirms or blocks a technical BUY; it never reverses the
    # long-only BTC trend strategy into a short.
    pm_confirms = pm_available and pm_up >= cfg.polymarket_confirm_probability
    pm_conflict = pm_available and pm_up < cfg.polymarket_block_probability

    action = "STAND_ASIDE"
    reason = "technical setup not confirmed"
    risk = {}

    if technical_score >= 5 and a:
        stop = price - cfg.stop_atr * a
        risk = _risk_block(price, stop, cfg)

        if pm_conflict:
            reason = "technical BUY blocked by bearish Polymarket confirmation"
        elif pm_available and not pm_confirms:
            reason = "technical BUY waiting for Polymarket confirmation"
        else:
            action = "BUY"
            reason = "technical trend/breakout confirmed"
            if pm_available:
                reason += " and Polymarket is bullish"
            risk.update({
                "atr": a,
                "take_profit_1": round(price + cfg.tp1_r * (price - risk["stop"]), 2),
                "take_profit_2": round(price + cfg.tp2_r * (price - risk["stop"]), 2),
            })

    result: dict[str, Any] = {
        "strategy": "BTC_TREND_RIDER",
        "version": VERSION,
        "action": action,
        "reason": reason,
        "score": technical_score,
        "technical_score": technical_score,
        "price": price,
        "adx_4h": adx4h,
        "rsi_1h": r,
        "volume_ratio": volume_ratio,
        "breakout_high": breakout_high,
        "max_leverage": cfg.max_leverage,
        "trade_amount_usd": cfg.trade_amount_usd,
        "max_loss_usd": cfg.max_loss_usd,
        "checks": {
            "trend_4h": trend_ok,
            "trend_1h": trend_1h_ok,
            "adx": strength_ok,
            "breakout": breakout,
            "volume": volume_ok,
            "rsi": momentum_ok,
        },
        "polymarket": {
            "available": pm_available,
            "up_probability": pm_up,
            "down_probability": pm.get("down_probability") if pm_available else None,
            "score": pm_score,
            "bias": pm.get("bias") if pm_available else "UNAVAILABLE",
            "time_remaining_minutes": pm.get("time_remaining_minutes") if pm_available else None,
            "volume_usd": pm.get("volume_usd") if pm_available else None,
            "probability_change_15m": pm.get("probability_change_15m") if pm_available else None,
            "question": pm.get("question") if pm_available else None,
            "duration_minutes": pm.get("duration_minutes") if pm_available else None,
            "confirms_buy": pm_confirms,
            "conflict": pm_conflict,
        },
    }
    result.update(risk)
    return result


def describe(cfg: BTCConfig = BTCConfig()) -> dict:
    return {
        "name": "BTC Trend Rider + Polymarket Confirmation",
        "version": VERSION,
        "asset": "BTC",
        "timeframes": ["4H", "1H", "30m Polymarket", "15m execution reference"],
        "config": asdict(cfg),
    }
