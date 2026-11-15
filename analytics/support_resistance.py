"""Deterministic support and resistance zones from swing pivots.

Zones need repeated touches. A single extreme is not a zone.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from analytics.market_stats import MarketBar
from core.money import D, q8


@dataclass(frozen=True)
class PriceZone:
    kind: str
    low: Decimal
    high: Decimal
    touches: int
    lookback_start: datetime
    lookback_end: datetime


def support_resistance_zones(
    bars: list[MarketBar],
    *,
    lookback: int = 80,
    min_touches: int = 2,
    band_fraction: Decimal = Decimal("0.015"),
    max_zones: int = 2,
) -> list[PriceZone]:
    window = bars[-lookback:]
    if len(window) < 5 or min_touches < 2:
        return []
    swings = _swings(window)
    if not swings:
        return []
    clusters = _clusters(swings, band_fraction)
    last = D(window[-1].close)
    support: list[PriceZone] = []
    resistance: list[PriceZone] = []
    for prices in clusters:
        if len(prices) < min_touches:
            continue
        low, high = _band(prices, band_fraction)
        if high < last:
            support.append(_zone("support", low, high, len(prices), window))
        elif low > last:
            resistance.append(_zone("resistance", low, high, len(prices), window))
    support.sort(key=lambda zone: (-zone.touches, zone.low))
    resistance.sort(key=lambda zone: (-zone.touches, zone.low))
    return support[:max_zones] + resistance[:max_zones]


def _swings(bars: list[MarketBar]) -> list[tuple[Decimal, str]]:
    found = []
    for index in range(2, len(bars) - 2):
        highs = [_high(bars[cursor]) for cursor in range(index - 2, index + 3)]
        lows = [_low(bars[cursor]) for cursor in range(index - 2, index + 3)]
        high = highs[2]
        low = lows[2]
        if high == max(highs) and highs.count(high) == 1:
            found.append((high, "high"))
        if low == min(lows) and lows.count(low) == 1:
            found.append((low, "low"))
    return found


def _clusters(swings: list[tuple[Decimal, str]], band_fraction: Decimal) -> list[list[Decimal]]:
    grouped: dict[str, list[Decimal]] = {"high": [], "low": []}
    for price, kind in swings:
        grouped[kind].append(price)
    clusters: list[list[Decimal]] = []
    for prices in grouped.values():
        remaining = sorted(prices)
        while remaining:
            anchor = remaining[0]
            members = [price for price in remaining if _near(price, anchor, band_fraction)]
            clusters.append(members)
            remaining = [price for price in remaining if price not in members]
    return clusters


def _near(price: Decimal, anchor: Decimal, band_fraction: Decimal) -> bool:
    if anchor == 0:
        return price == 0
    return abs(D(price) - D(anchor)) / abs(D(anchor)) <= band_fraction


def _band(prices: list[Decimal], band_fraction: Decimal) -> tuple[Decimal, Decimal]:
    low = min(prices)
    high = max(prices)
    mid = (low + high) / D(2)
    if mid == 0:
        return q8(low), q8(high)
    if (high - low) < mid * band_fraction:
        half = mid * band_fraction / D(2)
        return q8(mid - half), q8(mid + half)
    return q8(low), q8(high)


def _zone(kind: str, low: Decimal, high: Decimal, touches: int, window: list[MarketBar]) -> PriceZone:
    return PriceZone(
        kind=kind,
        low=low,
        high=high,
        touches=touches,
        lookback_start=window[0].start,
        lookback_end=window[-1].start,
    )


def _high(bar: MarketBar) -> Decimal:
    return D(bar.high if bar.high is not None else bar.close)


def _low(bar: MarketBar) -> Decimal:
    return D(bar.low if bar.low is not None else bar.close)
