"""Fixed-weight portfolio rebalancing logic for historical daily total-return prices.

This module is deliberately independent from the eToro order/execution stack.
It never places orders. Inputs are daily adjusted/total-return-like close series.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import mean, stdev
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class PricePoint:
    date: str
    close: float


def _validate_series(rows: Sequence[PricePoint], label: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in rows:
        date = str(row.date)[:10]
        price = float(row.close)
        if not isfinite(price) or price <= 0:
            raise ValueError(f"{label}: close must be finite and positive on {date}")
        if date in out:
            raise ValueError(f"{label}: duplicate date {date}")
        out[date] = price
    if len(out) < 2:
        raise ValueError(f"{label}: at least two daily prices are required")
    return out


def _max_drawdown(equities: Sequence[float]) -> float:
    peak = float("-inf")
    worst = 0.0
    for value in equities:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _metrics(equities: Sequence[float], dates: Sequence[str], initial_capital: float) -> dict:
    if not equities:
        return {}
    returns = [equities[0] / initial_capital - 1.0]
    returns.extend(equities[i] / equities[i - 1] - 1.0 for i in range(1, len(equities)))
    n = max(1, len(equities) - 1)
    try:
        first = dates[0].split("-")
        last = dates[-1].split("-")
        from datetime import date
        days = max(1, (date.fromisoformat(dates[-1][:10]) - date.fromisoformat(dates[0][:10])).days)
    except Exception:
        days = n
    years = days / 365.25
    final = equities[-1]
    cagr = (final / initial_capital) ** (1.0 / years) - 1.0 if years > 0 and final > 0 else -1.0
    daily_returns = returns[1:]
    vol = stdev(daily_returns) * sqrt(252) if len(daily_returns) > 1 else 0.0
    sharpe = (mean(daily_returns) / stdev(daily_returns) * sqrt(252)
              if len(daily_returns) > 1 and stdev(daily_returns) > 0 else None)
    return {
        "start_date": dates[0],
        "end_date": dates[-1],
        "observations": len(equities),
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final, 2),
        "net_pnl": round(final - initial_capital, 2),
        "total_return_pct": round((final / initial_capital - 1.0) * 100, 4),
        "cagr_pct": round(cagr * 100, 4),
        "annualized_volatility_pct": round(vol * 100, 4),
        "sharpe_ratio_rf0": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown_pct": round(_max_drawdown(equities) * 100, 4),
    }


def backtest_rebalancing(
    spy_rows: Sequence[PricePoint],
    gld_rows: Sequence[PricePoint],
    initial_capital: float = 100_000.0,
    target_spy: float = 0.5,
    target_gld: float = 0.5,
    cost_bps: float = 5.0,
) -> dict:
    """Backtest daily close rebalancing and buy-and-hold benchmarks.

    Returns from close[t-1] to close[t] are earned by the weights held after
    close[t-1]. Only after that return is realized do we rebalance at close[t].
    Thus today's close never changes the weights used to earn today's return.
    Costs are charged on both buy and sell notional (sum of absolute trade
    values), at cost_bps per $ traded. Price inputs should represent daily
    adjusted closes / total-return-like prices so cash distributions are not
    silently omitted from ETF returns.
    """
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be finite and positive")
    if not isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost_bps must be finite and non-negative")
    if not isfinite(target_spy) or not isfinite(target_gld) or target_spy < 0 or target_gld < 0:
        raise ValueError("target weights must be non-negative")
    if abs(target_spy + target_gld - 1.0) > 1e-9:
        raise ValueError("target weights must sum to 1.0")

    spy = _validate_series(spy_rows, "SPY")
    gld = _validate_series(gld_rows, "GLD")
    dates = sorted(set(spy) & set(gld))
    if len(dates) < 2:
        raise ValueError("SPY and GLD must have at least two overlapping dates")

    rate = cost_bps / 10_000.0
    # All approaches start with a fully invested portfolio; apply the same
    # initial transaction-cost assumption to each benchmark.
    initial_cost = initial_capital * rate
    reb_equity = initial_capital - initial_cost
    spy_bh = initial_capital - initial_cost
    gld_bh = initial_capital - initial_cost
    static_mix = initial_capital - initial_cost

    weights = {"SPY": target_spy, "GLD": target_gld}
    daily = [{
        "date": dates[0],
        "equity": round(reb_equity, 6),
        "spy_weight_after_close": target_spy,
        "gld_weight_after_close": target_gld,
        "turnover_dollars": round(initial_capital, 6),
        "cost_dollars": round(initial_cost, 6),
        "spy_return_pct": 0.0,
        "gld_return_pct": 0.0,
    }]
    trade_log = [{
        "date": dates[0], "action": "INITIAL_ALLOCATION",
        "spy_weight_before": 0.0, "gld_weight_before": 0.0,
        "spy_weight_after": target_spy, "gld_weight_after": target_gld,
        "turnover_dollars": round(initial_capital, 6),
        "cost_dollars": round(initial_cost, 6),
    }]
    equity_series = [reb_equity]
    spy_bh_series = [spy_bh]
    gld_bh_series = [gld_bh]
    static_mix_series = [static_mix]
    total_turnover = initial_capital
    total_costs = initial_cost

    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        r_spy = spy[d1] / spy[d0] - 1.0
        r_gld = gld[d1] / gld[d0] - 1.0

        # Existing weights earn this interval's returns.
        gross_factor = weights["SPY"] * (1.0 + r_spy) + weights["GLD"] * (1.0 + r_gld)
        if gross_factor <= 0:
            raise ValueError(f"Portfolio value became non-positive on {d1}")
        reb_equity *= gross_factor
        drift_spy = weights["SPY"] * (1.0 + r_spy) / gross_factor
        drift_gld = weights["GLD"] * (1.0 + r_gld) / gross_factor

        # At this close, rebalance the drifted portfolio to target weights.
        traded_fraction = abs(target_spy - drift_spy) + abs(target_gld - drift_gld)
        turnover = reb_equity * traded_fraction
        cost = turnover * rate
        reb_equity -= cost
        total_turnover += turnover
        total_costs += cost
        weights = {"SPY": target_spy, "GLD": target_gld}

        if turnover > 1e-8:
            trade_log.append({
                "date": d1, "action": "REBALANCE",
                "spy_weight_before": round(drift_spy, 8),
                "gld_weight_before": round(drift_gld, 8),
                "spy_weight_after": target_spy, "gld_weight_after": target_gld,
                "turnover_dollars": round(turnover, 6),
                "cost_dollars": round(cost, 6),
            })

        # Benchmarks hold their initial shares; no rebalancing after entry.
        spy_bh *= (1.0 + r_spy)
        gld_bh *= (1.0 + r_gld)
        static_mix = (initial_capital - initial_cost) * (
            target_spy * spy[d1] / spy[dates[0]] +
            target_gld * gld[d1] / gld[dates[0]]
        )

        equity_series.append(reb_equity)
        spy_bh_series.append(spy_bh)
        gld_bh_series.append(gld_bh)
        static_mix_series.append(static_mix)
        daily.append({
            "date": d1,
            "equity": round(reb_equity, 6),
            "spy_weight_after_close": target_spy,
            "gld_weight_after_close": target_gld,
            "drifted_spy_weight_before_rebalance": round(drift_spy, 8),
            "drifted_gld_weight_before_rebalance": round(drift_gld, 8),
            "turnover_dollars": round(turnover, 6),
            "cost_dollars": round(cost, 6),
            "spy_return_pct": round(r_spy * 100, 6),
            "gld_return_pct": round(r_gld * 100, 6),
        })

    return {
        "strategy": "FIXED_RATIO_REBALANCING",
        "assets": ["SPY", "GLD"],
        "target_weights": {"SPY": target_spy, "GLD": target_gld},
        "frequency": "daily_close",
        "cost_bps_per_dollar_traded": cost_bps,
        "execution_assumption": (
            "Close-to-close returns are earned by weights held from the prior close; "
            "rebalancing and costs are applied only after that day's return. "
            "Close fills are an approximation, not a broker fill guarantee."
        ),
        "initial_cost_dollars": round(initial_cost, 2),
        "total_costs_dollars": round(total_costs, 2),
        "total_turnover_dollars": round(total_turnover, 2),
        "turnover_multiple_of_initial_capital": round(total_turnover / initial_capital, 4),
        "metrics": _metrics(equity_series, dates, initial_capital),
        "benchmarks": {
            "SPY_buy_and_hold": _metrics(spy_bh_series, dates, initial_capital),
            "GLD_buy_and_hold": _metrics(gld_bh_series, dates, initial_capital),
            "static_50_50_buy_and_hold": _metrics(static_mix_series, dates, initial_capital),
        },
        "daily": daily,
        "trades": trade_log,
    }
