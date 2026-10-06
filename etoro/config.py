"""Settings loading for the eToro AutoTrader app.

Credentials live in a local env file (``.env`` or ``env``) that is never
committed to git. Values already present in the process environment always win,
which makes it easy to override a single setting for one run:

    set ETORO_DRY_RUN=true && python app.py check

Typical use::

    from etoro.config import load_settings

    settings = load_settings()
    print(settings.describe())   # secrets come back masked
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ``.env`` is the documented name; ``env`` is accepted so an existing
# template file keeps working without being renamed.
ENV_FILENAMES: Sequence[str] = (".env", "env")

API_HOST = "https://public-api.etoro.com"
API_V1 = API_HOST + "/api/v1"

_TRUE = frozenset({"1", "true", "yes", "y", "on"})
_FALSE = frozenset({"0", "false", "no", "n", "off", ""})

DEFAULTS: Mapping[str, str] = {
    "ETORO_API_KEY": "",
    "ETORO_USER_KEY": "",          # real / primary account
    "ETORO_USER_KEY_demo": "",     # demo account (same API key, different user key)
    "ETORO_DRY_RUN": "true",
    "ETORO_ACCOUNT": "demo",
    "ETORO_TRADING_MODE": "paper",
    "ETORO_MAX_ORDER_USD": "500",
    "ETORO_MAX_POSITION_USD": "500",
    "ETORO_MAX_DAILY_LOSS_USD": "50",
    "ETORO_ALLOWED_SYMBOLS": "",
}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


def find_env_file(root: Optional[Path] = None) -> Optional[Path]:
    """Return the first env file that exists in *root*, or ``None``."""
    root = Path(root) if root is not None else PROJECT_ROOT
    for name in ENV_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _strip_value(raw: str) -> str:
    """Trim whitespace, surrounding quotes and trailing inline comments."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    for marker in (" #", "\t#"):
        index = value.find(marker)
        if index != -1:
            value = value[:index]
    return value.strip()


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a ``KEY=VALUE`` env file into a dict. Blank lines and ``#`` are skipped."""
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_value(value)
    return values


def load_env_values(
    env_file: Optional[Path] = None,
    root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Merge defaults, the env file and the process environment (highest priority)."""
    merged: Dict[str, str] = dict(DEFAULTS)

    path = Path(env_file) if env_file is not None else find_env_file(root)
    if path is not None:
        merged.update(parse_env_file(path))

    environ = os.environ if environ is None else environ
    for key in list(merged):
        if key in environ and environ[key] != "":
            merged[key] = environ[key]
    # Also pick up any ETORO_* variable that was not part of the defaults.
    for key, value in environ.items():
        if key.startswith("ETORO_") and value != "":
            merged[key] = value
    return merged


def _as_bool(value: str, key: str) -> bool:
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"{key} must be a boolean-ish value, got {value!r}")


def _as_float(value: str, key: str) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be a number, got {value!r}") from exc


def _as_list(value: str) -> List[str]:
    if not value:
        return []
    cleaned = value.replace(";", ",").replace(" ", ",")
    return [item.strip().upper() for item in cleaned.split(",") if item.strip()]


@dataclass
class Settings:
    """Resolved runtime configuration. Never printed with raw secrets."""

    api_key: str = ""
    user_key: str = ""
    user_key_source: str = ""
    dry_run: bool = True
    account: str = "demo"
    trading_mode: str = "paper"
    max_order_usd: float = 100.0
    max_position_usd: float = 500.0
    max_daily_loss_usd: float = 50.0
    allowed_symbols: List[str] = field(default_factory=list)
    api_host: str = API_HOST
    base_url: str = API_V1
    env_source: Optional[str] = None

    @property
    def is_demo(self) -> bool:
        return self.account.strip().lower() != "real"

    @property
    def is_paper(self) -> bool:
        """True unless TRADING_MODE is explicitly live."""
        return self.trading_mode.strip().lower() != "live"

    @property
    def live_trading_enabled(self) -> bool:
        """True only when the operator deliberately turned demo and dry-run off."""
        return (not self.dry_run) and (not self.is_demo)

    def symbol_allowed(self, symbol: str) -> bool:
        if not self.allowed_symbols:
            return True
        return str(symbol).strip().upper() in self.allowed_symbols

    def __repr__(self) -> str:  # pragma: no cover - formatting helper
        return f"<Settings {self.describe()}>"

    __str__ = __repr__

    def describe(self) -> Dict[str, object]:
        """Safe summary: key material is reduced to a length marker."""
        return {
            "api_key": f"<set: {len(self.api_key)} chars>" if self.api_key else "<missing>",
            "user_key": f"<set: {len(self.user_key)} chars>" if self.user_key else "<missing>",
            "user_key_source": self.user_key_source or "<unknown>",
            "dry_run": self.dry_run,
            "account": self.account,
            "trading_mode": self.trading_mode,
            "is_demo": self.is_demo,
            "live_trading_enabled": self.live_trading_enabled,
            "max_order_usd": self.max_order_usd,
            "max_position_usd": self.max_position_usd,
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "allowed_symbols": list(self.allowed_symbols) or "<any>",
            "base_url": self.base_url,
            "env_source": self.env_source or "<environment only>",
        }


def load_settings(
    env_file: Optional[Path] = None,
    root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    require_keys: bool = True,
) -> Settings:
    """Build a :class:`Settings` object from the env file and environment."""
    values = load_env_values(env_file=env_file, root=root, environ=environ)

    path = Path(env_file) if env_file is not None else find_env_file(root)

    # One API key can pair with several user keys (one per account). Pick the
    # user key that matches the selected account, otherwise a demo run would
    # authenticate as the real account (or vice versa) and get 403s.
    _account = (values.get("ETORO_ACCOUNT", "demo") or "demo").strip()
    _account = _account.lower()
    if _account not in {"demo", "real"}:
        raise ConfigError("ETORO_ACCOUNT must be exactly 'demo' or 'real'")
    _user_key_source = "ETORO_USER_KEY_demo" if _account == "demo" else "ETORO_USER_KEY"
    _user_key = values.get(_user_key_source, "").strip()

    settings = Settings(
        api_key=values.get("ETORO_API_KEY", "").strip(),
        user_key=_user_key,
        dry_run=_as_bool(values.get("ETORO_DRY_RUN", "true"), "ETORO_DRY_RUN"),
        account=(values.get("ETORO_ACCOUNT", "demo") or "demo").strip(),
        trading_mode=(values.get("ETORO_TRADING_MODE", "paper") or "paper").strip(),
        max_order_usd=_as_float(values.get("ETORO_MAX_ORDER_USD", "100"), "ETORO_MAX_ORDER_USD"),
        max_position_usd=_as_float(values.get("ETORO_MAX_POSITION_USD", "500"), "ETORO_MAX_POSITION_USD"),
        max_daily_loss_usd=_as_float(values.get("ETORO_MAX_DAILY_LOSS_USD", "50"), "ETORO_MAX_DAILY_LOSS_USD"),
        allowed_symbols=_as_list(values.get("ETORO_ALLOWED_SYMBOLS", "")),
        env_source=str(path) if path is not None else None,
    )

    if require_keys:
        missing = []
        if not settings.api_key:
            missing.append("ETORO_API_KEY")
        if not settings.user_key:
            missing.append(settings.user_key_source)
        if missing:
            where = settings.env_source or "the process environment"
            raise ConfigError(
                "Missing credential(s): " + ", ".join(missing) +
                f". Add them to {where} (see the 'env' template in the project root)."
            )
    return settings
