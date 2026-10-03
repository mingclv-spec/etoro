"""Daily kill switch - spec section 10.

    daily loss > 1%                    -> STOP
    3 consecutive losses               -> PAUSE 60 minutes
    range breaks                       -> STOP range strategy
    major economic event approaching   -> NO NEW TRADES
    spread > normal threshold          -> NO TRADE

State persists to journal/killswitch.json so a restart cannot reset the day.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal", "killswitch.json")


@dataclass
class KillSwitchConfig:
    daily_loss_limit_pct: float = 0.01      # spec: daily loss > 1% -> STOP
    consecutive_loss_pause: int = 3         # spec: 3 consecutive losses
    pause_minutes: int = 60                 # spec: PAUSE 60 minutes
    max_spread_pct: float = 0.0005          # spread wider than this -> NO TRADE


@dataclass
class KillSwitchState:
    day: str = ""
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    paused_until: Optional[str] = None
    range_broken: bool = False
    macro_block: bool = False
    last_spread_pct: Optional[float] = None
    last_equity: Optional[float] = None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_state(path: str = DEFAULT_PATH) -> KillSwitchState:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            state = KillSwitchState(**{k: v for k, v in data.items() if k in KillSwitchState.__annotations__})
            if state.day != _today():
                state = KillSwitchState(day=_today())   # new day resets P&L and streak
            return state
        except Exception:
            pass
    return KillSwitchState(day=_today())


def save_state(state: KillSwitchState, path: str = DEFAULT_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(asdict(state), fh, indent=2)
    return path


def evaluate(
    state: KillSwitchState,
    cfg: KillSwitchConfig,
    account_equity: float,
    observed_spread_pct: Optional[float] = None,
    macro_block: bool = False,
    range_broken: bool = False,
) -> dict:
    """Return every active block and whether new trades are allowed."""
    blocks: List[str] = []

    limit = account_equity * cfg.daily_loss_limit_pct
    if state.daily_pnl <= -abs(limit):
        blocks.append(
            f"DAILY STOP: daily P&L {state.daily_pnl:.2f} hit the -{limit:.2f} limit "
            f"({cfg.daily_loss_limit_pct*100:.2f}% of equity)"
        )

    if state.paused_until:
        try:
            until = datetime.fromisoformat(state.paused_until)
            if datetime.now(timezone.utc) < until:
                blocks.append(f"PAUSED until {until.isoformat()} after {state.consecutive_losses} consecutive losses")
            else:
                state.paused_until = None
        except Exception:
            state.paused_until = None

    if range_broken or state.range_broken:
        blocks.append("RANGE BROKEN: stop range strategy, wait for a new validated range")

    if macro_block or state.macro_block:
        blocks.append("MACRO EVENT APPROACHING: no new trades")

    spread = observed_spread_pct if observed_spread_pct is not None else state.last_spread_pct
    if spread is not None and spread > cfg.max_spread_pct:
        blocks.append(
            f"SPREAD BLOCK: observed {spread*100:.4f}% exceeds {cfg.max_spread_pct*100:.4f}% threshold"
        )

    return {
        "allowed": not blocks,
        "blocks": blocks,
        "daily_pnl": round(state.daily_pnl, 2),
        "daily_loss_limit": round(limit, 2),
        "consecutive_losses": state.consecutive_losses,
        "paused_until": state.paused_until,
        "observed_spread_pct": spread,
    }


def record_trade(state: KillSwitchState, pnl: float, cfg: KillSwitchConfig) -> KillSwitchState:
    """Update daily P&L and the consecutive-loss streak after a closed trade."""
    state.daily_pnl += pnl
    if pnl < 0:
        state.consecutive_losses += 1
        if state.consecutive_losses >= cfg.consecutive_loss_pause:
            state.paused_until = (datetime.now(timezone.utc) + timedelta(minutes=cfg.pause_minutes)).isoformat()
    else:
        state.consecutive_losses = 0
    return state
