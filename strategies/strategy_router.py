"""Deterministic router for the three AutoTrader strategies."""
from __future__ import annotations
from typing import Any
from .bitcoin_trend_rider import BTCConfig
from .gold_range_scalper import ScalperConfig
from .universal_trend_momentum import UniversalConfig

GOLD_SYMBOLS=frozenset({"GOLD","XAUUSD","XAU"})
BTC_SYMBOLS=frozenset({"BTC","BTCUSD","BITCOIN"})

def strategy_name(symbol: str) -> str:
    key=str(symbol).strip().upper()
    if key in GOLD_SYMBOLS: return "GOLD_RANGE_SCALPER"
    if key in BTC_SYMBOLS: return "BTC_TREND_RIDER"
    return "UNIVERSAL_TREND_MOMENTUM"

def describe(symbol: str) -> dict[str, Any]:
    name=strategy_name(symbol)
    if name=="GOLD_RANGE_SCALPER":
        return {"symbol":symbol,"strategy":name,"config":ScalperConfig().__dict__}
    if name=="BTC_TREND_RIDER":
        return {"symbol":symbol,"strategy":name,"config":BTCConfig().__dict__}
    return {"symbol":symbol,"strategy":name,"config":UniversalConfig().__dict__}
