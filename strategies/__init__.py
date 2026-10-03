"""Strategy package for the eToro AutoTrader (signal-only)."""
from .gold_range_scalper import ScalperConfig, analyze, VERSION

__all__ = ["ScalperConfig", "analyze", "VERSION"]
