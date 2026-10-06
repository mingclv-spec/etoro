"""Thin, dependency-light HTTP client for the eToro Public API.

Authentication uses the pair of headers eToro requires on every call:

* ``x-api-key``  - identifies the application (Public API Key)
* ``x-user-key`` - identifies the user account (User Key)
* ``x-request-id`` - a fresh UUID per request, for tracing

Design notes:

* Read requests (GET/HEAD) are retried automatically on network errors,
  HTTP 429 and 5xx, honouring ``Retry-After``.
* Write requests are **not** retried by default, so a flaky connection can
  never place the same order twice.
* Order placement is guarded by the ``ETORO_DRY_RUN`` / ``ETORO_ACCOUNT``
  settings and the configured risk limits.
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Dict, Iterable, Mapping, Optional

import requests

from .config import Settings, load_settings

__all__ = [
    "EtoroError",
    "EtoroApiError",
    "EtoroDryRunBlocked",
    "EtoroRiskLimitExceeded",
    "EtoroClient",
]

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 0.5
USER_AGENT = "etoro-autotrader/0.1 (+python-requests)"
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class EtoroError(RuntimeError):
    """Base class for every error raised by this package."""


class EtoroApiError(EtoroError):
    """The API answered with a non-2xx status code."""

    def __init__(self, status: int, message: str, *, url: str = "", body: Any = None,
                 request_id: Optional[str] = None) -> None:
        self.status = status
        self.url = url
        self.body = body
        self.request_id = request_id
        detail = f"HTTP {status} from {url}" if url else f"HTTP {status}"
        if message:
            detail = f"{detail}: {message}"
        if request_id:
            detail = f"{detail} (x-request-id: {request_id})"
        super().__init__(detail)


class EtoroDryRunBlocked(EtoroError):
    """A trading call was attempted while dry-run mode was active."""


class EtoroRiskLimitExceeded(EtoroError):
    """A trading call would have breached a configured risk limit."""


def _extract_error_message(response: "requests.Response") -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()[:400]
    if isinstance(payload, Mapping):
        for key in ("message", "error", "errorMessage", "detail", "title"):
            value = payload.get(key)
            if value:
                return str(value)[:400]
        return str(payload)[:400]
    return str(payload)[:400]


class EtoroClient:
    """Small wrapper around ``requests`` with eToro auth, retries and guards."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        session: Optional["requests.Session"] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF,
        min_interval: float = 0.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.settings = settings if settings is not None else load_settings()
        if not self.settings.api_key or not self.settings.user_key:
            raise EtoroError("Both ETORO_API_KEY and ETORO_USER_KEY are required.")
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        self.min_interval = max(0.0, float(min_interval))
        self.log = logger or logging.getLogger("etoro.client")

        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        self._last_request_at = 0.0
        self.last_request_id: Optional[str] = None
        self.request_count = 0

    # ------------------------------------------------------------------ utils
    def _resolve_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if path.startswith("/api/"):
            return self.settings.api_host + path
        if not path.startswith("/"):
            path = "/" + path
        return self.settings.base_url + path

    def _headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        request_id = str(uuid.uuid4())
        self.last_request_id = request_id
        headers = {
            "x-api-key": self.settings.api_key,
            "x-user-key": self.settings.user_key,
            "x-request-id": request_id,
        }
        if extra:
            headers.update({k: v for k, v in extra.items() if v is not None})
        return headers

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _retry_after(response: "requests.Response") -> Optional[float]:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None

    def _sleep_backoff(self, attempt: int, response: Optional["requests.Response"] = None) -> None:
        delay = self.backoff_factor * (2 ** attempt)
        if response is not None:
            server_delay = self._retry_after(response)
            if server_delay is not None:
                delay = max(delay, server_delay)
        time.sleep(delay + random.uniform(0, 0.1))

    # ---------------------------------------------------------------- request
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        data: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        expect_json: bool = True,
        retries: Optional[int] = None,
        raw: bool = False,
    ) -> Any:
        """Perform a request and return parsed JSON (or the raw response)."""
        method = method.upper()
        url = self._resolve_url(path)
        attempts_allowed = self.max_retries if retries is None else max(0, int(retries))
        if method not in _IDEMPOTENT_METHODS:
            attempts_allowed = 0 if retries is None else attempts_allowed

        attempt = 0
        while True:
            self._throttle()
            request_headers = self._headers(headers)
            try:
                response = self._session.request(
                    method, url, params=params, json=json, data=data,
                    headers=request_headers, timeout=timeout or self.timeout,
                )
            except requests.RequestException as exc:
                if attempt < attempts_allowed:
                    self.log.warning("%s %s failed (%s); retrying", method, url, exc)
                    self._sleep_backoff(attempt)
                    attempt += 1
                    continue
                raise EtoroError(f"{method} {url} failed: {exc}") from exc

            self._last_request_at = time.monotonic()
            self.request_count += 1
            self.log.debug("%s %s -> %s", method, url, response.status_code)

            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < attempts_allowed:
                    self.log.warning("%s %s -> %s; backing off", method, url, response.status_code)
                    self._sleep_backoff(attempt, response)
                    attempt += 1
                    continue

            if not response.ok:
                raise EtoroApiError(
                    response.status_code,
                    _extract_error_message(response),
                    url=url,
                    body=response.text[:2000],
                    request_id=request_headers.get("x-request-id"),
                )

            if raw:
                return response
            if not expect_json:
                return response.text
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return response.text

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    # ------------------------------------------------------------- diagnostics
    def ping(self) -> Dict[str, Any]:
        """Cheap authenticated call used as a connectivity check."""
        started = time.perf_counter()
        payload = self.get("/watchlists")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        count: Optional[int] = None
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, Mapping):
            items = payload.get("watchlists") or payload.get("items")
            if isinstance(items, list):
                count = len(items)
        return {
            "ok": True,
            "status": "authenticated",
            "elapsed_ms": elapsed_ms,
            "watchlists": count,
            "account": self.settings.account,
            "dry_run": self.settings.dry_run,
            "x_request_id": self.last_request_id,
        }

    # ---------------------------------------------------------------- helpers
    def get_watchlists(self, ensure_builtin: Optional[bool] = None) -> Any:
        params = None
        if ensure_builtin is not None:
            params = {"ensureBuiltinWatchlists": str(ensure_builtin).lower()}
        return self.get("/watchlists", params=params)

    def search_instruments(self, symbol: str) -> Any:
        """Search eToro's live instrument catalog by symbol/name.

        No local instrument whitelist is used. The eToro market-data catalog is
        the source of truth, so newly supported stocks, ETFs, crypto, and
        commodities can be selected without a code change.
        """
        wanted = str(symbol).strip()
        payload = self.get("/market-data/search", params={"internalSymbolFull": wanted})
        items = payload.get("items") if isinstance(payload, Mapping) else payload
        if items:
            return payload
        return self.get("/market-data/search", params={"search": wanted})

    def resolve_instrument_id(self, symbol: str) -> int:
        """Resolve a symbol dynamically from eToro's current instrument catalog."""
        payload = self.search_instruments(symbol)
        items = payload.get("items") if isinstance(payload, Mapping) else payload
        if not items:
            raise EtoroError(f"No eToro-supported instrument found for {symbol!r}")

        wanted = symbol.strip().upper()
        for item in items:
            for key in ("internalSymbolFull", "symbol", "symbolName", "ticker"):
                if str(item.get(key, "")).strip().upper() == wanted:
                    return int(item["instrumentId"])

        if len(items) == 1:
            return int(items[0]["instrumentId"])

        raise EtoroError(
            f"Multiple instruments matched {symbol!r}; use an exact eToro symbol."
        )

    # ---------------------------------------------------------------- trading
    def _assert_trading_allowed(self, amount_usd: Optional[float], symbol: Optional[str],
                                force: bool) -> None:
        if self.settings.is_paper and not force:
            raise EtoroError(
                "TRADING_MODE=paper: refusing to send a real order. "
                "Paper fills are recorded by strategies.paper. "
                "Set ETORO_TRADING_MODE=live to enable real execution."
            )
        if self.settings.dry_run and not force:
            raise EtoroDryRunBlocked(
                "Dry-run is ON (ETORO_DRY_RUN=true). No order was sent. "
                "Set ETORO_DRY_RUN=false (and ETORO_ACCOUNT=real) when you truly mean it."
            )
        # Instrument selection is dynamic: eToro's live market-data catalog is
        # the source of truth. Risk limits below remain active regardless of
        # which supported instrument is selected.
        if amount_usd is not None and amount_usd > self.settings.max_order_usd:
            raise EtoroRiskLimitExceeded(
                f"Order of {amount_usd} USD exceeds ETORO_MAX_ORDER_USD="
                f"{self.settings.max_order_usd}"
            )

    def open_market_order(
        self,
        symbol: str,
        amount_usd: float,
        *,
        instrument_id: Optional[int] = None,
        transaction: str = "buy",
        leverage: int = 1,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        force: bool = False,
    ) -> Any:
        """Open a market position sized in cash. Blocked in dry-run mode."""
        symbol = symbol.strip().upper()
        self._assert_trading_allowed(float(amount_usd), symbol, force)
        instrument_id = instrument_id or self.resolve_instrument_id(symbol)
        self.log.info("Opening %s %s for %s USD", transaction, symbol, amount_usd)
        from strategies import live_exec
        payload = live_exec.build_payload(
            symbol,
            int(instrument_id),
            float(amount_usd),
            int(leverage),
            stop_loss,
            take_profit,
            transaction=transaction,
        )
        return live_exec.place(self, payload, self.settings)

    def close_position(
        self,
        position_id: int,
        *,
        instrument_id: Optional[int] = None,
        units_to_deduct: Optional[float] = None,
        force: bool = False,
    ) -> Any:
        """Fully or partially close an open position. Blocked in dry-run mode."""
        self._assert_trading_allowed(None, None, force)
        if instrument_id is None:
            raise EtoroError("instrument_id is required to close a position.")
        payload = {"InstrumentId": int(instrument_id), "UnitsToDeduct": units_to_deduct}
        self.log.info("Closing position %s (instrument %s)", position_id, instrument_id)
        from strategies import live_exec
        return live_exec.close(
            self, position_id, int(instrument_id), units_to_deduct, self.settings
        )


def build_client(**kwargs: Any) -> EtoroClient:
    """Convenience factory: load settings, then build a client."""
    settings = kwargs.pop("settings", None) or load_settings()
    return EtoroClient(settings, **kwargs)
