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

OPEN_ENDPOINT = "/api/v2/trading/execution/orders"
OPEN_ENDPOINT_DEMO = "/api/v2/trading/execution/demo/orders"
LOOKUP_ENDPOINT = "/api/v2/trading/info/orders:lookup"
CLOSE_ENDPOINT = "/api/v1/trading/execution/market-close-orders/positions/{position_id}"
CLOSE_ENDPOINT_DEMO = "/api/v1/trading/execution/demo/market-close-orders/positions/{position_id}"

STATUS_FILLED = 3
STATUS_PARTIAL = 5
STATUS_REJECTED = 4
STATUS_REJECTED_PARTIAL = 10
IN_FLIGHT = (1, 2, 11, 12)


def build_payload(symbol: str, instrument_id: int, notional: float, leverage: int,
                  stop_loss_price: float, take_profit_price: Optional[float] = None,
                  transaction: str = "buy") -> dict:
    """Construct an open-order payload.

    IMPORTANT: stopLossRate and takeProfitRate are ABSOLUTE PRICES, not
    percentages. Proven empirically: sending 1.07 was rejected with
    "Min pip validation failure ... LimitRate: 1.07 , ValidRate: 4172.46".
    The allowed DISTANCE is governed separately by min/maxStopLossPercentage
    (1%-50% on this instrument).
    """
    if leverage > 1 and stop_loss_price is None:
        raise ValueError("eToro requires stopLossRate when leverage > 1 - refusing to build an unprotected order")
    # eToro rejects the order if BOTH symbol and instrumentId are supplied:
    # "Exactly one of Symbol or InstrumentID must be provided." We use the
    # numeric instrumentId (unambiguous) and send no symbol.
    payload = {
        "action": "open",
        "transaction": transaction,
        "instrumentId": int(instrument_id),
        "orderType": "mkt",
        "leverage": int(leverage),
        "amount": float(notional),
        "orderCurrency": "usd",
    }
    if stop_loss_price is not None:
        payload["stopLossRate"] = float(stop_loss_price)
    if take_profit_price is not None:
        payload["takeProfitRate"] = float(take_profit_price)
    return payload


def _is_demo(settings=None):
    if settings is not None:
        return settings.is_demo
    from etoro.config import load_settings
    return load_settings().is_demo


def place(client, payload: dict, settings=None) -> dict:
    """Submit the order. A 200 means ACCEPTED, not filled.

    Routes to the DEMO order endpoint when ETORO_ACCOUNT=demo.
    """
    endpoint = OPEN_ENDPOINT_DEMO if _is_demo(settings) else OPEN_ENDPOINT
    return client.post(endpoint, json=payload)


def lookup(client, order_id=None, reference_id=None) -> dict:
    params = {}
    if order_id is not None:
        params["orderId"] = order_id
    if reference_id is not None:
        params["referenceId"] = reference_id
    return client.get(LOOKUP_ENDPOINT, params=params or None)


def wait_for_terminal(client, order_id=None, reference_id=None, attempts: int = 6,
                      delay: float = 1.5) -> dict:
    """Poll until the order reaches a terminal state, or we run out of attempts."""
    last = None
    for _ in range(attempts):
        try:
            last = lookup(client, order_id, reference_id)
        except Exception as exc:
            last = {"error": str(exc)[:200]}
            break
        status = extract_status(last)
        if status in (STATUS_FILLED, STATUS_PARTIAL, STATUS_REJECTED, STATUS_REJECTED_PARTIAL):
            break
        time.sleep(delay)
    return last or {}


def extract_status(result: dict) -> Optional[int]:
    """Pull status.id out of whatever shape the lookup returns."""
    if not isinstance(result, dict):
        return None
    for key in ("status", "orderStatus", "orders"):
        v = result.get(key)
        if isinstance(v, dict) and "id" in v:
            try:
                return int(v["id"])
            except (TypeError, ValueError):
                return None
        if isinstance(v, list) and v and isinstance(v[0], dict):
            st = v[0].get("status") or {}
            if isinstance(st, dict) and "id" in st:
                try:
                    return int(st["id"])
                except (TypeError, ValueError):
                    return None
    return None


def extract_positions(result: dict) -> list:
    """positionExecutions -> list of positionId values."""
    if not isinstance(result, dict):
        return []
    out = []
    for key in ("positionExecutions", "positions", "positionIds"):
        v = result.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    pid = item.get("positionId") or item.get("positionID") or item.get("id")
                    if pid is not None:
                        out.append(pid)
                elif item is not None:
                    out.append(item)
    return out


def close(client, position_id, instrument_id: int, units: Optional[float] = None,
          settings=None) -> dict:
    body = {"InstrumentId": int(instrument_id), "UnitsToDeduct": units}
    endpoint = CLOSE_ENDPOINT_DEMO if _is_demo(settings) else CLOSE_ENDPOINT
    return client.post(endpoint.format(position_id=position_id), json=body)


def describe_payload(payload: dict) -> str:
    """Human-readable, safe to print (no credentials in a payload)."""
    return ", ".join(f"{k}={v}" for k, v in payload.items())
