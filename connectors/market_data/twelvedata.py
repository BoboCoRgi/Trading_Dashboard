"""Twelve Data historical bars.

The dashboard does not import this module. MarketDataService does.
The API key is sent as a request parameter and is never put in an error.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from connectors.market_data.bars import PriceBar
from core.money import D

_PROVIDER_INTERVAL = {"5min": "5min", "15min": "15min", "1h": "1h", "1D": "1day", "1W": "1week"}
_SOURCE = "twelvedata"


class MarketDataError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class TwelveDataProvider:
    provider_name = "twelvedata"

    def __init__(self, api_key: str, transport=None, timeout: float = 12.0) -> None:
        if not api_key.strip():
            raise MarketDataError("missing_key")
        self._api_key = api_key.strip()
        self._transport = transport
        self._timeout = timeout

    def fetch(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[PriceBar]:
        provider_interval = _PROVIDER_INTERVAL.get(interval)
        if provider_interval is None:
            raise MarketDataError("unsupported")
        params = {
            "symbol": symbol,
            "interval": provider_interval,
            "start_date": _stamp(start),
            "end_date": _stamp(end),
            "timezone": "UTC",
            "order": "ASC",
            "apikey": self._api_key,
        }
        status, body = self._request("/time_series", params)
        payload = _payload(status, body)
        return normalise_time_series(payload, symbol=symbol, interval=interval)

    def _request(self, path: str, params: dict) -> tuple[int, str]:
        if self._transport is None:
            return _request_with_httpx(path, params, self._timeout)
        try:
            return self._transport(path, params)
        except MarketDataError:
            raise
        except TimeoutError:
            raise MarketDataError("timeout") from None
        except Exception:
            raise MarketDataError("unavailable") from None


def normalise_time_series(payload: dict, *, symbol: str, interval: str) -> list[PriceBar]:
    if not isinstance(payload, dict):
        raise MarketDataError("invalid")
    if payload.get("status") == "error":
        code = payload.get("code")
        if code in {400, 404}:
            raise MarketDataError("unsupported")
        raise MarketDataError("invalid")
    values = payload.get("values")
    if not isinstance(values, list):
        raise MarketDataError("invalid")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    currency = str(meta.get("currency") or "")
    bars: list[PriceBar] = []
    for row in values:
        if not isinstance(row, dict) or row.get("close") in (None, ""):
            continue
        bars.append(
            PriceBar(
                timestamp=_parse_time(str(row.get("datetime") or "")),
                interval=interval,
                open=_optional(row.get("open")),
                high=_optional(row.get("high")),
                low=_optional(row.get("low")),
                close=D(str(row["close"])),
                volume=_optional(row.get("volume")),
                currency=currency,
                source=_SOURCE,
                is_delayed=True,
                symbol=symbol,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def _request_with_httpx(path: str, params: dict, timeout: float) -> tuple[int, str]:
    try:
        response = httpx.get(f"https://api.twelvedata.com{path}", params=params, timeout=timeout)
    except httpx.TimeoutException:
        raise MarketDataError("timeout") from None
    except httpx.HTTPError:
        raise MarketDataError("unavailable") from None
    return response.status_code, response.text


def _payload(status: int, body: str) -> dict:
    if status == 429:
        raise MarketDataError("rate_limit")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MarketDataError("invalid") from exc
    if isinstance(payload, dict) and payload.get("code") == 429:
        raise MarketDataError("rate_limit")
    return payload


def _optional(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    return D(str(value))


def _parse_time(value: str) -> datetime:
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        else:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise MarketDataError("invalid") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    utc = moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M:%S")
