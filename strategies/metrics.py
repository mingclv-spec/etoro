"""Performance metrics - spec section 10.

Win rate alone is meaningless. The spec's example:
    80% win rate, avg win $2, avg loss $10
    EV = 0.8*2 - 0.2*10 = -$0.40   -> loses money
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass
class Trade:
    pnl: float
    entry_time: str = ""
    exit_time: str = ""
    holding_minutes: Optional[float] = None
    spread_cost: float = 0.0
    overnight_cost: float = 0.0


def expected_value(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """EV per trade. avg_loss should be a positive number."""
    return win_rate * avg_win - (1.0 - win_rate) * avg_loss


def max_drawdown(equity_curve: Sequence[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def sharpe(returns: Sequence[float], periods_per_year: float) -> Optional[float]:
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (mean / sd) * math.sqrt(periods_per_year)


def performance_metrics(
    trades: Sequence[Trade],
    starting_equity: float = 5000.0,
    days_span: Optional[float] = None,
) -> dict:
    """Full metric set from the spec, including after-cost figures."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "no trades yet"}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    net = sum(t.pnl for t in trades)
    spread_total = sum(t.spread_cost for t in trades)
    overnight_total = sum(t.overnight_cost for t in trades)

    win_rate = len(wins) / n
    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    equity = starting_equity
    curve = [equity]
    returns: List[float] = []
    for t in trades:
        equity += t.pnl
        curve.append(equity)
        returns.append(t.pnl / starting_equity)

    holdings = [t.holding_minutes for t in trades if t.holding_minutes is not None]
    avg_holding = (sum(holdings) / len(holdings)) if holdings else None

    trades_per_day = (n / days_span) if days_span else None
    periods_per_year = (trades_per_day * 252) if trades_per_day else 252.0

    return {
        "trades": n,
        "win_rate_pct": round(win_rate * 100.0, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expected_value_per_trade": round(expected_value(win_rate, avg_win, avg_loss), 4),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_profit": round(net, 2),
        "max_drawdown": round(max_drawdown(curve), 2),
        "max_drawdown_pct": round(max_drawdown(curve) / starting_equity * 100.0, 2),
        "sharpe": round(sharpe(returns, periods_per_year), 3) if sharpe(returns, periods_per_year) is not None else None,
        "avg_holding_minutes": round(avg_holding, 1) if avg_holding is not None else None,
        "trades_per_day": round(trades_per_day, 2) if trades_per_day else None,
        "spread_cost_total": round(spread_total, 2),
        "overnight_cost_total": round(overnight_total, 2),
        "profit_after_spread": round(net - spread_total, 2),
        "profit_after_spread_and_overnight": round(net - spread_total - overnight_total, 2),
        "final_equity": round(equity, 2),
        "losing_trades": len(losses),
        "winning_trades": len(wins),
    }
