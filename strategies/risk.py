"""Risk management and position sizing - spec section 7.

Size from ACCOUNT RISK, never "always $100 at 20x".

    Account = $5,000
    Risk per trade = 0.25%  ->  max loss $12.50
    position size = risk_amount / stop_distance

At 20x a 0.5% gold move is ~10% of the MARGIN committed, not 10% of the
position - which is exactly why margin must be derived from the stop.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class RiskConfig:
    account_equity: float = 5000.0
    risk_per_trade_pct: float = 0.0025      # spec example: 0.25%
    leverage: int = 20                       # eToro caps gold at 20:1
    max_leverage: int = 20
    max_margin_pct_of_equity: float = 0.20   # never tie up more than 20% of equity as margin
    min_notional: float = 10.0


def position_size(entry: float, stop: float, cfg: RiskConfig) -> dict:
    """Derive notional and margin from the stop distance and account risk."""
    warnings = []
    leverage = min(cfg.leverage, cfg.max_leverage)
    if cfg.leverage > cfg.max_leverage:
        warnings.append(f"leverage {cfg.leverage}x capped to {cfg.max_leverage}x")

    if not entry or stop is None or entry <= 0:
        return {"error": "entry/stop required"}

    stop_distance = abs(entry - stop) / entry
    if stop_distance <= 0:
        return {"error": "stop distance is zero - refusing to size a trade with no risk"}

    risk_amount = cfg.account_equity * cfg.risk_per_trade_pct
    notional = risk_amount / stop_distance
    margin = notional / leverage
    margin_pct = margin / cfg.account_equity if cfg.account_equity else 0.0

    capped = False
    if margin_pct > cfg.max_margin_pct_of_equity:
        capped = True
        margin = cfg.account_equity * cfg.max_margin_pct_of_equity
        notional = margin * leverage
        warnings.append(
            "margin capped at "
            f"{cfg.max_margin_pct_of_equity*100:.0f}% of equity; "
            "effective risk is now below target"
        )

    if notional < cfg.min_notional:
        warnings.append(f"notional {notional:.2f} is below the {cfg.min_notional} minimum")

    actual_loss = notional * stop_distance

    return {
        "account_equity": round(cfg.account_equity, 2),
        "risk_per_trade_pct": round(cfg.risk_per_trade_pct * 100.0, 3),
        "risk_amount": round(risk_amount, 2),
        "stop_distance_pct": round(stop_distance * 100.0, 3),
        "leverage": leverage,
        "notional": round(notional, 2),
        "margin_required": round(margin, 2),
        "margin_pct_of_equity": round(margin_pct * 100.0, 2),
        "max_loss_at_stop": round(actual_loss, 2),
        "margin_capped": capped,
        "warnings": warnings,
    }


def pnl_table(notional: float, moves=(-0.05, -0.01, -0.005, 0.001, 0.0025, 0.005, 0.01)):
    """Small helper mirroring the spec's leverage table."""
    return [
        {"move_pct": round(m * 100.0, 2), "pl": round(notional * m, 2)}
        for m in moves
    ]


def describe(cfg: RiskConfig) -> dict:
    return asdict(cfg)
