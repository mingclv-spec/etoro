"""eToro market-order execution adapter.

Execution uses eToro's current unified v2 order API. Demo and Real use
separate execution paths; the caller's Settings object determines which path
is selected. POSTs are never retried by the HTTP client to avoid duplicate
orders.
"""
from __future__ import annotations

from typing import Any, Optional

OPEN_ENDPOINT = "/api/v2/trading/execution/orders"
OPEN_ENDPOINT_DEMO = "/api/v2/trading/execution/demo/orders"

# Legacy v1 close endpoint retained because the current eToro close-position
# guide documents this route explicitly.
CLOSE_ENDPOINT = "/api/v1/trading/execution/market-close-orders/positions/{position_id}"
CLOSE_ENDPOINT_DEMO = "/api/v1/trading/execution/demo/market-close-orders/positions/{position_id}"


def build_payload(
    symbol: str,
    instrument_id: int,
    notional: float,
    leverage: int,
    stop_loss_price: Optional[float],
    take_profit_price: Optional[float] = None,
    transaction: str = "buy",
) -> dict:
    """Build the current unified v2 market-open order payload."""
    if leverage < 1:
        raise ValueError("leverage must be >= 1")

    action = str(transaction).strip().lower()
    if action not in {"buy", "sell"}:
        raise ValueError("transaction must be buy or sell")

    payload = {
        "action": "open",
        "transaction": action,
        "instrumentId": int(instrument_id),
        "orderType": "mkt",
        "amount": float(notional),
        "orderCurrency": "usd",
        "leverage": int(leverage),
    }

    # Only include optional risk fields when the caller supplied them.
    if stop_loss_price is not None:
        payload["stopLossRate"] = float(stop_loss_price)
    if take_profit_price is not None:
        payload["takeProfitRate"] = float(take_profit_price)

    return payload


def place(client: Any, payload: dict, settings: Any) -> Any:
    """Submit an open order to the environment selected by Settings."""
    endpoint = OPEN_ENDPOINT_DEMO if settings.is_demo else OPEN_ENDPOINT
    client.log.info(
        "POST %s account=%s instrumentId=%s amount=%s leverage=%s",
        endpoint,
        settings.account,
        payload.get("instrumentId"),
        payload.get("amount"),
        payload.get("leverage"),
    )
    return client.post(endpoint, json=payload, retries=0)


def close(
    client: Any,
    position_id: int,
    instrument_id: int,
    units_to_deduct: Optional[float],
    settings: Any,
) -> Any:
    """Close all or part of a position using the documented close endpoint."""
    endpoint_template = CLOSE_ENDPOINT_DEMO if settings.is_demo else CLOSE_ENDPOINT
    endpoint = endpoint_template.format(position_id=int(position_id))
    payload = {"UnitsToDeduct": units_to_deduct}
    client.log.info(
        "POST %s account=%s instrumentId=%s units=%s",
        endpoint,
        settings.account,
        instrument_id,
        units_to_deduct,
    )
    return client.post(endpoint, json=payload, retries=0)
