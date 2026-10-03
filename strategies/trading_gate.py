"""Trading gate - spec: "monitor 24/7, trade only when conditions are right".

Combines every precondition into ONE answer. Monitoring, risk control and
analysis keep running regardless; this gate only decides whether NEW TRADES
are permitted right now.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional


@dataclass
class GateConfig:
    require_range_regime: bool = True      # only trade RANGE, never TREND/BREAKOUT
    max_price_age_seconds: float = 10.0    # stale data -> no new trades
    max_spread_pct: float = 0.05           # spread blowout -> no trade (percent)
    require_connection: bool = True


def evaluate(
    regime_mode: Optional[str],
    killswitch_result: Optional[dict],
    spread_pct: Optional[float],
    price_age_seconds: Optional[float],
    connected: Optional[bool],
    cfg: Optional[GateConfig] = None,
) -> dict:
    """Return whether new trades are allowed, and every reason they are not."""
    cfg = cfg or GateConfig()
    blocks: List[str] = []

    if cfg.require_range_regime and regime_mode != "RANGE":
        blocks.append(f"regime is {regime_mode or 'unknown'} - range strategy inactive")

    if cfg.require_connection and connected is False:
        blocks.append("market feed disconnected")

    if price_age_seconds is None:
        blocks.append("price freshness unknown")
    elif price_age_seconds > cfg.max_price_age_seconds:
        blocks.append(
            f"price data stale ({price_age_seconds:.1f}s > {cfg.max_price_age_seconds:.0f}s)"
        )

    if spread_pct is not None and spread_pct > cfg.max_spread_pct:
        blocks.append(f"spread {spread_pct:.4f}% exceeds {cfg.max_spread_pct:.4f}%")

    if killswitch_result is not None and not killswitch_result.get("allowed", True):
        blocks.extend(killswitch_result.get("blocks", []))

    return {
        "trading_enabled": not blocks,
        "blocks": blocks,
        "monitoring": True,      # always on
        "risk_control": True,    # always on
        "analysis": True,        # always on
    }
