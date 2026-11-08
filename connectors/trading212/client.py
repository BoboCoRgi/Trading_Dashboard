"""Read-only Trading 212 HTTP client.

This module fetches account data. It does not place orders and it does not
write to the database.
"""

import logging
import time
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from core.config import Settings, get_settings

logger = logging.getLogger("connectors.trading212")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_TIMEOUT = httpx.Timeout(15.0)
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.0)
_RETRY_AFTER_CAP = 2.0
_MAX_PAGES = 200
_HISTORY_INTERVAL = 10.5


class Trading212Error(Exception):
    """Base error for the Trading 212 client."""


class Trading212ConfigError(Trading212Error):
    """Settings are missing or the client was asked to do something it cannot."""


class Trading212AuthError(Trading212Error):
    """The API rejected the key. These responses are not retried."""


class Trading212HTTPError(Trading212Error):
    """A non-retryable HTTP failure."""


class Trading212TransientError(Trading212Error):
    """A 429 or 5xx response that persisted after limited retries."""


class Trading212Client:
    """HTTP Basic client for the Trading 212 public API."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        history_interval: float = _HISTORY_INTERVAL,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self._sleeper = sleeper
        self._history_interval = history_interval

    def get_account_summary(self) -> dict[str, Any]:
        payload = self._get("/equity/account/summary")
        if not isinstance(payload, dict):
            raise Trading212HTTPError("Account summary was not an object")
        return payload

    def get_positions(self) -> list[dict[str, Any]]:
        payload = self._get("/equity/positions")
        if not isinstance(payload, list):
            raise Trading212HTTPError("Positions response was not a list")
        return payload

    def get_historical_orders(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._paginate(f"/equity/history/orders?limit={limit}")

    def get_transactions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._paginate(f"/equity/history/transactions?limit={limit}")

    def get_dividends(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._paginate(f"/equity/history/dividends?limit={limit}")

    def get_instruments(self) -> list[dict[str, Any]]:
        payload = self._get("/equity/metadata/instruments")
        if isinstance(payload, list):
            logger.info("Trading 212 instruments records %s", len(payload))
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            logger.info("Trading 212 instruments records %s", len(payload["items"]))
            return payload["items"]
        raise Trading212HTTPError("Instrument catalogue was not a list")

    def _paginate(self, path: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        current = path
        for page in range(1, _MAX_PAGES + 1):
            if current in seen:
                raise Trading212HTTPError("Pagination repeated a page")
            seen.add(current)
            if page > 1 and self._history_interval:
                self._sleeper(self._history_interval)
            payload = self._get(current)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise Trading212HTTPError("Historical page had no items list")
            batch = payload["items"]
            logger.info("Trading 212 page %s records %s", page, len(batch))
            collected.extend(batch)
            nxt = payload.get("nextPagePath")
            if not nxt:
                return collected
            if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("//"):
                raise Trading212HTTPError("Malformed next page")
            current = nxt
        raise Trading212HTTPError("Pagination exceeded the page limit")

    def _url(self, path: str) -> str:
        base = urlparse(self.settings.t212_base_url)
        if path.startswith("http://") or path.startswith("https://"):
            target = urlparse(path)
            if target.netloc != base.netloc or target.username or target.password:
                raise Trading212HTTPError("Refusing a pagination path outside the configured host")
            return path
        if path.startswith("/api/"):
            return f"{base.scheme}://{base.netloc}{path}"
        return f"{self.settings.t212_base_url.rstrip('/')}{path}"

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _request(self, method: str, path: str) -> Any:
        if method.upper() != "GET":
            raise Trading212ConfigError("This client only sends read requests")
        key = self.settings.t212_api_key.strip()
        secret = self.settings.t212_api_secret.strip()
        if not key or not secret:
            raise Trading212ConfigError("Trading 212 API key and secret are required")
        url = self._url(path)
        auth = httpx.BasicAuth(key, secret)
        with httpx.Client(
            auth=auth,
            timeout=_TIMEOUT,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    response = client.request(method, url)
                except httpx.TimeoutException as exc:
                    self._retry_or_raise(attempt, path, "timed out", exc)
                    continue
                except httpx.TransportError as exc:
                    self._retry_or_raise(attempt, path, "could not connect", exc)
                    continue
                status = response.status_code
                logger.info("Trading 212 %s %s -> %s", method, path.split("?", 1)[0], status)
                if status in {401, 403}:
                    raise Trading212AuthError(f"Trading 212 rejected the credentials (HTTP {status})")
                if status == 429 or status >= 500:
                    if attempt >= _MAX_ATTEMPTS:
                        raise Trading212TransientError(f"Trading 212 request failed (HTTP {status})")
                    self._sleeper(_delay(response, attempt))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise Trading212HTTPError(f"Trading 212 request failed (HTTP {status})") from exc
                return response.json()
        raise Trading212TransientError("Trading 212 request failed")

    def _retry_or_raise(self, attempt: int, path: str, reason: str, exc: Exception) -> None:
        logger.info("Trading 212 GET %s %s", path, reason)
        if attempt >= _MAX_ATTEMPTS:
            raise Trading212TransientError(f"Trading 212 request {reason}") from exc
        self._sleeper(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])


def position_ticker(row: dict[str, Any]) -> str:
    direct = row.get("ticker")
    if direct:
        return str(direct)
    instrument = row.get("instrument")
    if isinstance(instrument, dict) and instrument.get("ticker"):
        return str(instrument["ticker"])
    if isinstance(instrument, str):
        return instrument
    return ""


def display_symbol(ticker: str) -> str:
    """Ticker prefix. This is not the current market symbol when the broker code is historical."""
    head = ticker.split("_", 1)[0].strip()
    return head or ticker


def explicit_current_symbol(meta: dict | None) -> str | None:
    """Current ticker from broker metadata. A historical ticker prefix is not used."""
    if not isinstance(meta, dict):
        return None
    short = str(meta.get("shortName") or "").strip()
    if short and " " not in short and len(short) <= 32:
        return short
    return None


def current_display_symbol(ticker: str, meta: dict | None = None) -> str:
    return explicit_current_symbol(meta) or display_symbol(ticker)


def listing_metadata(payload: dict | None, catalogue_row: dict | None) -> dict:
    """Catalogue fields win. The order payload fills gaps and is not rewritten."""
    merged: dict = {}
    if isinstance(catalogue_row, dict):
        merged.update(catalogue_row)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if value in (None, ""):
                continue
            if key == "shortName" and merged.get("shortName"):
                continue
            merged.setdefault(key, value)
    return merged


def sanitised_connection_summary(summary: dict[str, Any], positions: list[dict[str, Any]]) -> str:
    """A short status block. Account ids and credentials are omitted."""
    currency = str(summary.get("currency") or "—")
    total = summary.get("totalValue")
    symbols = sorted({display_symbol(position_ticker(row)) for row in positions if position_ticker(row)})
    lines = [
        "Trading 212 connection: OK",
        f"Currency: {currency}",
        f"Total account value: {_money(currency, total)}",
        f"Open positions: {len(positions)}",
        "Symbols: " + (", ".join(symbols) if symbols else "none"),
    ]
    return "\n".join(lines)


def _money(currency: str, value: object) -> str:
    if value is None:
        return "unavailable"
    amount = Decimal(str(value))
    if currency == "GBP":
        return f"£{amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def _delay(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), _RETRY_AFTER_CAP)
        except ValueError:
            pass
    index = min(attempt - 1, len(_BACKOFF_SECONDS) - 1)
    return _BACKOFF_SECONDS[index]
