"""Deterministic router for the three AutoTrader strategies.

GOLD and BTC have active strategy configurations. Everything else is an
intentional placeholder and does not generate a trading signal.
"""
from __future__ import annotations
from typing import Any
from .bitcoin_trend_rider import BTCConfig
from .gold_range_scalper import ScalperConfig

GOLD_SYMBOLS=frozenset({"GOLD","XAUUSD","XAU"})
BTC_SYMBOLS=frozenset({"BTC","BTCUSD","BITCOIN"})

GOLD_TRADE_AMOUNT_USD = 50.0
GOLD_LEVERAGE = 20
BTC_TRADE_AMOUNT_USD = 20.0
BTC_LEVERAGE = 1

def strategy_name(symbol: str) -> str:
    key=str(symbol).strip().upper()
    if key in GOLD_SYMBOLS: return "GOLD_RANGE_SCALPER"
    if key in BTC_SYMBOLS: return "BTC_TREND_RIDER"
    return "UNIVERSAL_PLACEHOLDER"

def describe(symbol: str) -> dict[str, Any]:
    name=strategy_name(symbol)
    if name=="GOLD_RANGE_SCALPER":
        cfg=ScalperConfig(leverage=GOLD_LEVERAGE, max_leverage=GOLD_LEVERAGE)
        return {"symbol":symbol,"strategy":name,"status":"ACTIVE",
                "trade_amount_usd":GOLD_TRADE_AMOUNT_USD,"leverage":GOLD_LEVERAGE,
                "config":cfg.__dict__}
    if name=="BTC_TREND_RIDER":
        cfg=BTCConfig(max_leverage=1)
        return {"symbol":symbol,"strategy":name,"status":"ACTIVE",
                "trade_amount_usd":BTC_TRADE_AMOUNT_USD,"leverage":BTC_LEVERAGE,
                "config":cfg.__dict__}
    return {"symbol":symbol,"strategy":name,"status":"PLACEHOLDER",
            "trade_amount_usd":None,"leverage":None,
            "message":"No trading logic or order sizing is enabled for this asset class yet."}
