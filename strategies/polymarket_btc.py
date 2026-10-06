"""Read-only Polymarket BTC short-horizon signal adapter.

This module never trades on Polymarket. It only reads public market metadata
and price history and converts an active BTC Up/Down market into a compact
confirmation signal for the eToro BTC strategy.

The adapter prefers an approximately 30-minute BTC market. If none is active,
it can fall back to the nearest active short-duration BTC market (normally
15 minutes) so the strategy remains observable.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional
import requests

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_URL = "https://clob.polymarket.com"
DEFAULT_TIMEOUT = 6.0
DEFAULT_RETRIES = 1


class PolymarketError(RuntimeError):
    """Raised when Polymarket market data cannot be read or parsed."""


@dataclass(frozen=True)
class PolymarketConfig:
    preferred_duration_minutes: int = 30
    fallback_max_duration_minutes: int = 60
    min_volume_usd: float = 10_000.0
    max_spread: float = 0.05
    min_probability: float = 0.30
    strong_probability: float = 0.65
    history_minutes: int = 15


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _market_duration_minutes(market: dict[str, Any]) -> Optional[float]:
    start = _parse_dt(market.get("startDate") or market.get("startDateIso"))
    end = _parse_dt(market.get("endDate") or market.get("endDateIso"))
    if start and end and end > start:
        return (end - start).total_seconds() / 60.0
    return None


def _is_btc_updown(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(key, ""))
        for key in ("question", "title", "slug", "description")
    ).lower()
    btc = "btc" in text or "bitcoin" in text
    updown = ("up or down" in text or "up/down" in text or
              "updown" in text)
    return btc and updown


def _outcome_map(market: dict[str, Any]) -> dict[str, float]:
    outcomes = _parse_json_list(market.get("outcomes"))
    prices = _parse_json_list(market.get("outcomePrices"))
    result: dict[str, float] = {}
    for outcome, price in zip(outcomes, prices):
        p = _as_float(price)
        if p is not None:
            result[str(outcome).strip().lower()] = p
    return result


def _token_map(market: dict[str, Any]) -> dict[str, str]:
    outcomes = _parse_json_list(market.get("outcomes"))
    tokens = _parse_json_list(
        market.get("clobTokenIds") or market.get("clobTokenIDs")
    )
    result: dict[str, str] = {}
    for outcome, token in zip(outcomes, tokens):
        if token:
            result[str(outcome).strip().lower()] = str(token)
    return result


def _market_volume(market: dict[str, Any]) -> float:
    for key in ("volumeNum", "volume", "volume24hr"):
        value = _as_float(market.get(key))
        if value is not None:
            return value
    return 0.0


def _fetch_history(token_id: Optional[str], timeout: float) -> Optional[float]:
    if not token_id:
        return None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            response = requests.get(
                f"{CLOB_URL}/prices-history",
                params={"market": token_id, "interval": "1h", "fidelity": 5},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except (requests.RequestException, ValueError):
            if attempt == DEFAULT_RETRIES:
                return None
            time.sleep(0.25)

    history = payload.get("history") if isinstance(payload, dict) else payload
    if not isinstance(history, list) or len(history) < 2:
        return None

    now = time.time()
    cutoff = now - 15 * 60
    recent = []
    for point in history:
        if not isinstance(point, dict):
            continue
        ts = _as_float(point.get("t"))
        price = _as_float(point.get("p"))
        if ts is not None and price is not None and ts >= cutoff:
            recent.append((ts, price))
    if len(recent) < 2:
        recent = [
            (_as_float(x.get("t")) or 0.0, _as_float(x.get("p")))
            for x in history[-4:]
            if isinstance(x, dict) and _as_float(x.get("p")) is not None
        ]
    if len(recent) < 2:
        return None
    recent.sort()
    return recent[-1][1] - recent[0][1]


def fetch_btc_signal(
    cfg: PolymarketConfig = PolymarketConfig(),
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Return the best active BTC short-duration Up/Down market signal."""
    last_error: Optional[Exception] = None
    payload: Any = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            response = requests.get(
                GAMMA_URL,
                params={"active": "true", "closed": "false", "limit": 100},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            last_error = None
            break
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < DEFAULT_RETRIES:
                time.sleep(0.35)
    if last_error is not None:
        raise PolymarketError(
            f"unable to read Polymarket markets after retry: {last_error}"
        ) from last_error

    markets = payload if isinstance(payload, list) else payload.get("markets", [])
    candidates: list[tuple[float, dict[str, Any]]] = []
    now = datetime.now(timezone.utc)

    for market in markets:
        if not isinstance(market, dict) or not _is_btc_updown(market):
            continue
        if market.get("closed") is True or market.get("active") is False:
            continue
        end = _parse_dt(market.get("endDate") or market.get("endDateIso"))
        if not end or end <= now:
            continue
        duration = _market_duration_minutes(market)
        if duration is None or duration <= 0 or duration > cfg.fallback_max_duration_minutes:
            continue
        volume = _market_volume(market)
        if volume < cfg.min_volume_usd:
            continue
        distance = abs(duration - cfg.preferred_duration_minutes)
        # Prefer duration closest to 30m, then the highest-volume market.
        candidates.append((distance + 1.0 / max(volume, 1.0), market))

    if not candidates:
        return {
            "available": False,
            "source": "polymarket",
            "reason": "no active BTC Up/Down market met duration/liquidity filters",
            "config": asdict(cfg),
        }

    candidates.sort(key=lambda item: item[0])
    market = candidates[0][1]
    end = _parse_dt(market.get("endDate") or market.get("endDateIso"))
    outcomes = _outcome_map(market)
    tokens = _token_map(market)

    up = outcomes.get("up")
    down = outcomes.get("down")
    if up is None or down is None:
        return {
            "available": False,
            "source": "polymarket",
            "reason": "selected market has no UP/DOWN outcome prices",
            "market": market.get("question") or market.get("title"),
        }

    spread = None
    try:
        # Gamma prices are probabilities; use their deviation from a fair
        # complement as a conservative data-quality check.
        spread = abs(1.0 - (up + down))
    except TypeError:
        pass

    momentum = _fetch_history(tokens.get("up"), timeout)
    score = 0
    if up >= cfg.strong_probability:
        score = 2
    elif up >= 0.58:
        score = 1
    elif up < 0.35:
        score = -2
    elif up < 0.45:
        score = -1

    if spread is not None and spread > cfg.max_spread:
        score = 0

    remaining = max(0.0, (end - now).total_seconds() / 60.0)
    return {
        "available": True,
        "source": "polymarket",
        "market_id": market.get("id"),
        "question": market.get("question") or market.get("title"),
        "slug": market.get("slug"),
        "duration_minutes": _market_duration_minutes(market),
        "time_remaining_minutes": round(remaining, 2),
        "up_probability": round(up, 4),
        "down_probability": round(down, 4),
        "volume_usd": round(_market_volume(market), 2),
        "probability_change_15m": round(momentum, 4) if momentum is not None else None,
        "score": score,
        "bias": "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL",
        "data_quality_ok": spread is None or spread <= cfg.max_spread,
    }
