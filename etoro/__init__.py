"""eToro AutoTrader - minimal, safe client package for the eToro Public API."""

from .client import (
    EtoroApiError,
    EtoroClient,
    EtoroDryRunBlocked,
    EtoroError,
    EtoroRiskLimitExceeded,
    build_client,
)
from .config import ConfigError, Settings, load_settings

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "Settings",
    "load_settings",
    "EtoroClient",
    "EtoroError",
    "EtoroApiError",
    "EtoroDryRunBlocked",
    "EtoroRiskLimitExceeded",
    "build_client",
    "__version__",
]
