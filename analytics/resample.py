"""Aggregate stored candles into a higher timeframe.

A bucket is emitted only when every expected lower bar is present. Missing
opens, highs, lows, or volumes are not replaced with the close.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from analytics.market_stats import NEW_YORK
from connectors.market_data.bars import PriceBar
from core.money import D

_LOWER = {"15min": "5min", "1h": "5min", "1W": "1D"}


def resample_source(interval: str) -> str | None:
    """Lower stored interval that can build this one without another request."""
    return _LOWER.get(interval)


def resample_bars(bars: list[PriceBar], interval: str) -> list[PriceBar]:
    if interval == "15min":
        return _intraday(bars, minutes=15, child_minutes=5)
    if interval == "1h":
        return _intraday(bars, minutes=60, child_minutes=5)
    if interval == "1W":
        return _weeks(bars)
    return []


def _intraday(bars: list[PriceBar], *, minutes: int, child_minutes: int) -> list[PriceBar]:
    expected = minutes // child_minutes
    grouped: dict[datetime, list[PriceBar]] = {}
    for bar in bars:
        if bar.interval != f"{child_minutes}min" or bar.timestamp.tzinfo is None:
            continue
        start = _floor_local(bar.timestamp, minutes)
        grouped.setdefault(start, []).append(bar)
    built = []
    for start in sorted(grouped):
        children = sorted(grouped[start], key=lambda bar: bar.timestamp)
        wanted = [start + timedelta(minutes=child_minutes * step) for step in range(expected)]
        by_time = {child.timestamp.astimezone(timezone.utc): child for child in children}
        complete = [by_time.get(moment.astimezone(timezone.utc)) for moment in wanted]
        if any(child is None for child in complete):
            continue
        candle = _ohlcv(complete, interval=f"{minutes}min" if minutes < 60 else "1h", timestamp=start)
        if candle is not None:
            built.append(candle)
    return built


def _weeks(bars: list[PriceBar]) -> list[PriceBar]:
    grouped: dict = {}
    for bar in bars:
        if bar.interval != "1D" or bar.timestamp.tzinfo is None:
            continue
        day = bar.timestamp.astimezone(timezone.utc).date()
        monday = day - timedelta(days=day.weekday())
        grouped.setdefault(monday, []).append(bar)
    built = []
    for monday in sorted(grouped):
        children = sorted(grouped[monday], key=lambda bar: bar.timestamp)
        candle = _ohlcv(children, interval="1W", timestamp=children[0].timestamp)
        if candle is not None:
            built.append(candle)
    return built


def _floor_local(moment: datetime, minutes: int) -> datetime:
    local = moment.astimezone(NEW_YORK)
    floored = (local.minute // minutes) * minutes
    start = local.replace(minute=floored, second=0, microsecond=0)
    if minutes == 60:
        start = local.replace(minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def _ohlcv(children: list[PriceBar], *, interval: str, timestamp: datetime) -> PriceBar | None:
    if any(bar.open is None or bar.high is None or bar.low is None for bar in children):
        return None
    volume: Decimal | None
    if any(bar.volume is None for bar in children):
        volume = None
    else:
        volume = sum((D(bar.volume) for bar in children), start=D(0))
    delayed = [bar.is_delayed for bar in children]
    if any(flag is True for flag in delayed):
        is_delayed: bool | None = True
    elif all(flag is False for flag in delayed):
        is_delayed = False
    else:
        is_delayed = None
    fetched = [bar.fetched_at for bar in children if bar.fetched_at is not None]
    return PriceBar(
        timestamp=timestamp,
        interval=interval,
        open=children[0].open,
        high=max(D(bar.high) for bar in children),
        low=min(D(bar.low) for bar in children),
        close=children[-1].close,
        volume=volume,
        currency=children[-1].currency or children[0].currency,
        source=children[-1].source,
        is_delayed=is_delayed,
        symbol=children[-1].symbol,
        instrument_id=children[-1].instrument_id,
        fetched_at=max(fetched) if fetched else None,
    )
