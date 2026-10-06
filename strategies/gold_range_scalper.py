"""Adaptive Gold Range Scalper v0.1 - full spec implementation.

SIGNAL ONLY. This module never places an order. It reads candles, decides
RANGE / BREAKOUT / TREND, and returns a suggested action for a human.

Spec (Ming, 2026-10-02):
 1. Regime gate (1H): ADX < 20-22, EMA20/50 flat, both boundaries touched >=2x,
    range width > minimum, no major macro event.
 2. Dynamic range (96 x 15m = ~24h): highest high / lowest low, each boundary
    must be touched >= 2x. Zones: BUY 0-15%, SELL 85-100%.
 3. Entries (15m): BUY = bottom 15% + RSI(14)<40 + bullish rejection + ADX<22
    + no breakout. SELL = top 15% + RSI(14)>60 + bearish rejection + ADX<22
    + no breakout. RSI 40/60 deliberately, not 30/70.
 4. Take profit: partial. TP1 = 45% of range, TP2 = 75%, 50% at each.
 5. Stop loss: MANDATORY just outside the boundary. Never average down.
    eToro caps gold at 20:1.
 6. BREAKOUT MODE - the most important feature. When a 15m candle closes
    outside the range AND the move > 0.5 x ATR AND momentum expands, STOP ALL
    RANGE TRADES. Do not keep fading an overbought breakout.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence

from .indicators import (
    Candle,
    adx,
    atr,
    bollinger_width,
    count_touches,
    ema_series,
    is_bearish_rejection,
    is_bullish_rejection,
    pct_change,
    rsi,
    window_high_low,
)

VERSION = "0.1"


@dataclass
class ScalperConfig:
    """Every threshold is a knob; defaults follow Ming's spec."""

    # --- regime filter (1-hour candles) ---
    adx_period: int = 14
    adx_max: float = 22.0
    ema_fast: int = 20
    ema_slow: int = 50
    ema_slope_lookback: int = 10
    ema_slope_max_pct: float = 0.0030
    ema_gap_max_pct: float = 0.0040
    bb_period: int = 20
    bb_mult: float = 2.0

    # --- dynamic range (15-minute candles) ---
    range_lookback_bars: int = 96
    boundary_mode: str = "raw"
    min_range_width_pct: float = 0.0040    # PROVISIONAL - Ming to set
    touch_tolerance_pct: float = 0.0010
    min_touches_per_side: int = 2

    # --- zones ---
    buy_zone_max_pct: float = 0.15
    sell_zone_min_pct: float = 0.85

    # --- entry filters ---
    rsi_period: int = 14
    rsi_buy_max: float = 40.0
    rsi_sell_min: float = 60.0
    rejection_wick_frac: float = 0.45
    rejection_close_pos: float = 0.50

    # --- take profit ---
    tp1_frac: float = 0.45
    tp2_frac: float = 0.75
    tp1_portion: float = 0.50
    tp2_portion: float = 0.50

    # --- stop loss (mandatory) ---
    stop_buffer_pct: float = 0.0050

    # --- breakout mode (section 6) ---
    breakout_atr_mult: float = 0.5        # spec: move > 0.5 x ATR
    breakout_atr_source: str = "15m"      # "15m" or "1h"
    breakout_momentum_mult: float = 1.5   # body vs average body (volume proxy)
    breakout_lookback: int = 20
    require_momentum_for_breakout: bool = True

    # --- leverage discipline ---
    leverage: int = 20
    max_leverage: int = 20
    trade_amount_usd: float = 50.0
    max_loss_usd: float = 25.0

    # --- macro guard ---
    macro_block: bool = False
    macro_note: str = ""


def _closes(candles):
    return [c.close for c in candles]


def _ema_slope_pct(candles, period, lookback):
    series = ema_series(_closes(candles), period)
    if len(series) < lookback + 1:
        return None
    now, then = series[-1], series[-1 - lookback]
    if now is None or then is None:
        return None
    return pct_change(now, then)


def _swing_highs(window):
    out = []
    for i in range(1, len(window) - 1):
        h = window[i].high
        if h >= window[i - 1].high and h >= window[i + 1].high:
            out.append(h)
    return out


def _swing_lows(window):
    out = []
    for i in range(1, len(window) - 1):
        low = window[i].low
        if low <= window[i - 1].low and low <= window[i + 1].low:
            out.append(low)
    return out


def _cluster(values, tolerance):
    if not values:
        return []
    ordered = sorted(values)
    clusters = [[ordered[0]]]
    for v in ordered[1:]:
        if v - clusters[-1][-1] <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def _validated_upper(window, tolerance, min_touches):
    clusters = [c for c in _cluster(_swing_highs(window), tolerance) if len(c) >= min_touches]
    if not clusters:
        return None
    best = max(clusters, key=lambda c: sum(c) / len(c))
    return sum(best) / len(best)


def _validated_lower(window, tolerance, min_touches):
    clusters = [c for c in _cluster(_swing_lows(window), tolerance) if len(c) >= min_touches]
    if not clusters:
        return None
    best = min(clusters, key=lambda c: sum(c) / len(c))
    return sum(best) / len(best)


def classify_regime(candles_1h, cfg):
    price = candles_1h[-1].close if candles_1h else None
    a = atr(candles_1h, cfg.adx_period)
    d = adx(candles_1h, cfg.adx_period)
    bw = bollinger_width(candles_1h, cfg.bb_period, cfg.bb_mult)
    closes = _closes(candles_1h)
    fast_series = ema_series(closes, cfg.ema_fast)
    slow_series = ema_series(closes, cfg.ema_slow)
    fast = fast_series[-1] if fast_series else None
    slow = slow_series[-1] if slow_series else None
    slope_fast = _ema_slope_pct(candles_1h, cfg.ema_fast, cfg.ema_slope_lookback)
    slope_slow = _ema_slope_pct(candles_1h, cfg.ema_slow, cfg.ema_slope_lookback)

    gap_pct = (abs(fast - slow) / price) if (fast is not None and slow is not None and price) else None

    checks = {
        "adx_below_max": (d is not None and d < cfg.adx_max),
        "ema_pair_flat": (
            gap_pct is not None and gap_pct <= cfg.ema_gap_max_pct
            and slope_fast is not None and abs(slope_fast) <= cfg.ema_slope_max_pct
            and slope_slow is not None and abs(slope_slow) <= cfg.ema_slope_max_pct
        ),
        "no_macro_event": not cfg.macro_block,
    }

    return {
        "price": price, "atr14": a, "adx14": d,
        "bb_width_pct": (bw * 100.0) if bw is not None else None,
        "ema_fast": fast, "ema_slow": slow,
        "ema_gap_pct": (gap_pct * 100.0) if gap_pct is not None else None,
        "ema_fast_slope_pct": (slope_fast * 100.0) if slope_fast is not None else None,
        "ema_slow_slope_pct": (slope_slow * 100.0) if slope_slow is not None else None,
        "checks": checks,
    }


def find_range(candles_15m, cfg):
    window = list(candles_15m)[-cfg.range_lookback_bars:]
    if not window:
        return {"error": "no 15m candles"}

    raw_high, raw_low = window_high_low(window, cfg.range_lookback_bars)
    price = window[-1].close
    tol = (price or 1.0) * cfg.touch_tolerance_pct

    upper = lower = None
    if cfg.boundary_mode == "validated":
        upper = _validated_upper(window, tol, cfg.min_touches_per_side)
        lower = _validated_lower(window, tol, cfg.min_touches_per_side)

    high = upper if upper is not None else raw_high
    low = lower if lower is not None else raw_low

    mid = (high + low) / 2.0 if high is not None and low is not None else None
    width = (high - low) if (high is not None and low is not None) else None
    width_pct = (width / mid) if (width and mid) else None

    touches_high = count_touches(window, high, tol, "resistance") if high is not None else 0
    touches_low = count_touches(window, low, tol, "support") if low is not None else 0

    checks = {
        "width_above_min": (width_pct is not None and width_pct >= cfg.min_range_width_pct),
        "high_touched_2x": touches_high >= cfg.min_touches_per_side,
        "low_touched_2x": touches_low >= cfg.min_touches_per_side,
    }

    return {
        "bars_used": len(window),
        "lookback_hours": cfg.range_lookback_bars * 15 / 60.0,
        "boundary_mode": cfg.boundary_mode,
        "range_high": high, "range_low": low, "range_mid": mid, "range_width": width,
        "raw_high": raw_high, "raw_low": raw_low,
        "width_pct": (width_pct * 100.0) if width_pct is not None else None,
        "touches_resistance": touches_high, "touches_support": touches_low,
        "checks": checks,
    }


def detect_breakout(candles_15m, rng, candles_1h, cfg):
    """Section 6. 15m close outside the range AND move > 0.5 x ATR AND momentum expands."""
    high, low = rng.get("range_high"), rng.get("range_low")
    if not candles_15m or high is None or low is None:
        return {"breakout": False, "reason": "insufficient data"}

    last = candles_15m[-1]

    atr_ref = None
    if cfg.breakout_atr_source == "1h":
        atr_ref = atr(candles_1h, cfg.adx_period) if candles_1h else None
    if atr_ref is None:
        atr_ref = atr(candles_15m, 14)

    closed_above = last.close > high
    closed_below = last.close < low
    move_up = last.close - high
    move_down = low - last.close
    threshold = (cfg.breakout_atr_mult * atr_ref) if atr_ref else None

    move_ok = threshold is not None and (
        (closed_above and move_up > threshold) or (closed_below and move_down > threshold)
    )

    bodies = [abs(c.close - c.open) for c in candles_15m[-(cfg.breakout_lookback + 1):-1]]
    avg_body = (sum(bodies) / len(bodies)) if bodies else 0.0
    body = abs(last.close - last.open)
    momentum_expands = avg_body > 0 and body >= cfg.breakout_momentum_mult * avg_body

    triggered = (closed_above or closed_below) and move_ok
    if cfg.require_momentum_for_breakout:
        triggered = triggered and momentum_expands

    checks = {
        "closed_outside_range": closed_above or closed_below,
        "move_gt_half_atr": bool(move_ok),
        "momentum_expands": bool(momentum_expands),
    }

    return {
        "breakout": bool(triggered),
        "direction": "UP" if closed_above else ("DOWN" if closed_below else None),
        "checks": checks,
        "atr_ref": atr_ref,
        "atr_source": cfg.breakout_atr_source,
        "move_points": round(move_up if closed_above else move_down, 2) if (closed_above or closed_below) else 0.0,
        "threshold_points": round(threshold, 2) if threshold is not None else None,
        "last_close": last.close,
        "avg_body": round(avg_body, 3),
        "last_body": round(body, 3),
        "reason": "15m close outside range with expanding momentum" if triggered else "no breakout",
    }


def zone_position_pct(price, high, low):
    if high is None or low is None or high <= low or price is None:
        return None
    return (price - low) / (high - low) * 100.0


def take_profit_levels(rng, side, cfg):
    high, low = rng.get("range_high"), rng.get("range_low")
    if high is None or low is None or high <= low:
        return {}
    width = high - low
    if side == "BUY":
        tp1, tp2 = low + cfg.tp1_frac * width, low + cfg.tp2_frac * width
    else:
        tp1, tp2 = high - cfg.tp1_frac * width, high - cfg.tp2_frac * width
    return {
        "tp1": round(tp1, 2), "tp2": round(tp2, 2),
        "tp1_portion": cfg.tp1_portion, "tp2_portion": cfg.tp2_portion,
        "tp1_range_frac": cfg.tp1_frac, "tp2_range_frac": cfg.tp2_frac,
    }


def _cash_risk_stop(entry, side, cfg):
    """Calculate a price stop from the configured cash-loss budget."""
    if not entry or entry <= 0:
        return None
    lev = min(cfg.leverage, cfg.max_leverage)
    exposure = cfg.trade_amount_usd * lev
    if exposure <= 0 or cfg.max_loss_usd <= 0:
        return None
    move_pct = min(0.99, cfg.max_loss_usd / exposure)
    if side == "BUY":
        return entry * (1.0 - move_pct)
    return entry * (1.0 + move_pct)


def _risk_block(entry, strategy_stop, side, cfg):
    if not entry or strategy_stop is None:
        return {}
    lev = min(cfg.leverage, cfg.max_leverage)
    exposure = cfg.trade_amount_usd * lev
    risk_stop = _cash_risk_stop(entry, side, cfg)
    if risk_stop is None:
        return {}
    # Keep the strategy's boundary stop when it is already tighter. Otherwise
    # cap the effective stop at the user's cash-loss budget.
    if side == "BUY":
        effective_stop = max(strategy_stop, risk_stop)
    else:
        effective_stop = min(strategy_stop, risk_stop)
    risk_frac = abs(entry - effective_stop) / entry
    estimated_loss = exposure * risk_frac
    return {
        "leverage": lev,
        "trade_amount_usd": cfg.trade_amount_usd,
        "position_exposure_usd": exposure,
        "max_loss_usd": cfg.max_loss_usd,
        "strategy_stop": round(strategy_stop, 2),
        "risk_stop": round(risk_stop, 2),
        "stop": round(effective_stop, 2),
        "estimated_loss_usd": round(estimated_loss, 2),
        "stop_distance_pct": round(risk_frac * 100.0, 3),
        "risk_pct_of_equity_at_leverage": round(risk_frac * lev * 100.0, 2),
        "leverage_capped": cfg.leverage > cfg.max_leverage,
        "note": "Effective stop is the tighter of the range-boundary stop and the cash-risk stop.",
    }


def make_signal(price, rng, regime, candles_15m, cfg):
    high, low, mid = rng.get("range_high"), rng.get("range_low"), rng.get("range_mid")
    if price is None or high is None or low is None or high <= low:
        return {"action": "WAIT", "reason": "insufficient data"}

    pos = zone_position_pct(price, high, low)
    r = rsi(candles_15m, cfg.rsi_period) if candles_15m else None
    last = candles_15m[-1] if candles_15m else None

    bull_rej = is_bullish_rejection(last, cfg.rejection_wick_frac, cfg.rejection_close_pos) if last else False
    bear_rej = is_bearish_rejection(last, cfg.rejection_wick_frac, cfg.rejection_close_pos) if last else False

    adx_val = regime.get("adx14")
    adx_ok = adx_val is not None and adx_val < cfg.adx_max

    buy_checks = {
        "in_buy_zone_0_15": pos <= cfg.buy_zone_max_pct * 100.0,
        "rsi_below_40": r is not None and r < cfg.rsi_buy_max,
        "bullish_rejection": bull_rej,
        "adx_below_22": adx_ok,
        "no_breakout": True,
    }
    sell_checks = {
        "in_sell_zone_85_100": pos >= cfg.sell_zone_min_pct * 100.0,
        "rsi_above_60": r is not None and r > cfg.rsi_sell_min,
        "bearish_rejection": bear_rej,
        "adx_below_22": adx_ok,
        "no_breakout": True,
    }

    base = {
        "zone_position_pct": round(pos, 2),
        "rsi14": round(r, 2) if r is not None else None,
        "range_high": high, "range_low": low, "range_mid": mid,
        "buy_conditions": buy_checks, "sell_conditions": sell_checks,
    }

    if all(buy_checks.values()):
        stop = low * (1.0 - cfg.stop_buffer_pct)
        return dict(base, action="BUY", reason="all BUY conditions met",
                    entry=price, stop=round(stop, 2),
                    **take_profit_levels(rng, "BUY", cfg),
                    **_risk_block(price, stop, "BUY", cfg))

    if all(sell_checks.values()):
        stop = high * (1.0 + cfg.stop_buffer_pct)
        return dict(base, action="SELL", reason="all SELL conditions met",
                    entry=price, stop=round(stop, 2),
                    **take_profit_levels(rng, "SELL", cfg),
                    **_risk_block(price, stop, "SELL", cfg))

    if pos <= cfg.buy_zone_max_pct * 100.0:
        failed = [k for k, v in buy_checks.items() if not v]
        return dict(base, action="WAIT", reason="in buy zone but: " + ", ".join(failed))
    if pos >= cfg.sell_zone_min_pct * 100.0:
        failed = [k for k, v in sell_checks.items() if not v]
        return dict(base, action="WAIT", reason="in sell zone but: " + ", ".join(failed))

    return dict(base, action="WAIT", reason="price mid-range - no edge here")


def analyze(candles_1h, candles_15m, cfg=None):
    """Full pipeline. Breakout mode overrides everything else."""
    cfg = cfg or ScalperConfig()
    regime = classify_regime(candles_1h, cfg)
    rng = find_range(candles_15m, cfg)
    brk = detect_breakout(candles_15m, rng, candles_1h, cfg)

    gates = dict(regime["checks"])
    gates.update(rng.get("checks", {}))
    range_mode = all(gates.values())

    if brk.get("breakout"):
        mode = "BREAKOUT"
        range_mode = False
    elif range_mode:
        mode = "RANGE"
    else:
        mode = "TREND / STAND ASIDE"

    out = {
        "strategy": f"Adaptive Gold Range Scalper v{VERSION}",
        "mode": mode,
        "range_mode_active": (mode == "RANGE"),
        "gates": gates,
        "regime": regime,
        "range": rng,
        "breakout": brk,
        "config": asdict(cfg),
    }

    price = regime.get("price")
    zone_pos = (
        round(zone_position_pct(price, rng.get("range_high"), rng.get("range_low")), 2)
        if price and rng.get("range_high") and rng.get("range_low") else None
    )

    if mode == "BREAKOUT":
        out["signal"] = {
            "action": "STAND_DOWN",
            "reason": (
                "BREAKOUT MODE - STOP ALL RANGE TRADES. Do not fade an "
                f"overbought/oversold breakout ({brk.get('direction')})."
            ),
            "zone_position_pct": zone_pos,
            "breakout": brk,
        }
    elif mode == "RANGE":
        out["signal"] = make_signal(price, rng, regime, candles_15m, cfg)
    else:
        failed = [k for k, v in gates.items() if not v]
        out["signal"] = {
            "action": "WAIT",
            "reason": "range mode not active; failed gates: " + ", ".join(failed),
            "zone_position_pct": zone_pos,
        }
    return out
