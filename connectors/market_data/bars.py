"""Normalised OHLCV bars. Provider payloads are converted before they leave the connector."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PriceBar:
    timestamp: datetime
    interval: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None
    currency: str
    source: str
    is_delayed: bool | None
    symbol: str = ""
    instrument_id: str = ""
    fetched_at: datetime | None = None


def bar_identity(bar: PriceBar) -> tuple[str, str, datetime, str, str]:
    """Idempotent cache key: instrument, time, interval, origin is applied by the caller, plus source."""
    return (bar.instrument_id, bar.interval, bar.timestamp, bar.source)


def upsert_bars(existing: dict[tuple, PriceBar], incoming: list[PriceBar]) -> dict[tuple, PriceBar]:
    """Replace a bar with the same identity. A second write does not add a second row."""
    merged = dict(existing)
    for bar in incoming:
        merged[bar_identity(bar)] = bar
    return merged
