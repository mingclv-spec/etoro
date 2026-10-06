"""Live execution adapter - REAL orders on eToro.

Only reachable when TRADING_MODE=live. Key facts from the official schema:

  * stopLossRate is REQUIRED when leverage > 1  -> every 20x order carries a
    broker-side stop. That stop lives at eToro, not in our process, so an
    agent restart cannot lose it.
  * Orders are ASYNCHRONOUS. A 200 means "accepted", not filled. status.id:
      3 Filled | 5 PartiallyFilled  -> executed
      4 Rejected | 10 RejectedPartiallyFilled -> failed (reason in errorCode)
      1,2,11,12 -> still in flight, poll again
  * positionExecutions lists the positionId(s) the order produced.
  * X-Request-Id (GUID) provides idempotency - the client sends a fresh one
    per request and never retries POSTs.
  * stop-loss / take-profit are expressed as PERCENTAGES
    (eligibility: minStopLossPercentage 0, max 50, default 50).
"""
from __future__ import annotations

import time
from typing import Optional

OPEN_ENDPOINT = "/api/v1/trading/execution/market-open-orders/by-amount"
OPEN_ENDPOINT_DEMO = "/api/v1/trading/execution/demo/market-open-orders/by-amount"
LOOKUP_ENDPOINT = "/api/v2/trading/info/orders:lookup"
CLOSE_ENDPOINT = "/api/v1/trading/execution/market-close-orders/positions/{position_id}"
CLOSE_ENDPOINT_DEMO = "/api/v1/trading/execution/demo/market-close-orders/positions/{position_id}"

STATUS_FILLED = 3
STATUS_PARTIAL = 5
STATUS_REJECTED = 4
STATUS_REJECTED_PARTIAL = 10
IN_FLIGHT = (1, 2, 11, 12)


def build_payload(symbol: str, instrument_id: int, notional: float, leverage: int,
                  stop_loss_price: Optional[float], take_profit_price: Optional[float] = None,
                  transaction: str = "buy") -> dict:
    """Build the current eToro v1 market-open-by-amount payload."""
    if leverage < 1:
        raise ValueError("leverage must be >= 1")
    action = str(transaction).strip().lower()
    if action not in {"buy", "sell"}:
        raise ValueError("transaction must be buy or sell")
    payload = {
        "InstrumentId": int(instrument_id),
        "Amount": float(notional),
        "Leverage": int(leverage),
        "IsBuy": action == "buy",
    }
    if stop_loss_price is not None:
        payload["StopLossRate"] = float(stop_loss_price)
    if take_profit_price is not None:
        payload["TakeProfitRate"] = float(take_profit_price)
    return payload

