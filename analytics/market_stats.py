"""Volume, VWAP, and relative-performance maths.

Daily relative volume uses full-session volume only.
Intraday relative volume uses cumulative volume up to the same clock.
VWAP is computed from intraday bars only. Daily bars are refused.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.money import D, q8


@dataclass(frozen=True)
class MarketBar:
    start: datetime
    interval: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None
    currency: str
    source: str


def daily_relative_volume(*, session_volume: Decimal, prior_session_volumes: list[Decimal]) -> Decimal | None:
    """Today's full-day volume divided by the average of the previous 20 full days.

    ``prior_session_volumes`` must be full-session totals, oldest or newest
    order does not matter. Fewer than 20 days returns None.
    """
    if len(prior_session_volumes) != 20:
        return None
    if any(volume < 0 for volume in prior_session_volumes) or session_volume < 0:
        return None
    average = sum((D(volume) for volume in prior_session_volumes), start=D(0)) / D(20)
    if average == 0:
        return None
    return q8(D(session_volume) / average)


def intraday_relative_volume(
    *,
    cumulative_to_clock: Decimal,
    prior_cumulative_to_same_clock: list[Decimal],
) -> Decimal | None:
    """Cumulative volume up to a clock, divided by the 20-session average up to that same clock.

    The prior values must already be cumulative-to-clock figures. This function
    does not accept full-day volume.
    """
    if len(prior_cumulative_to_same_clock) != 20:
        return None
    if cumulative_to_clock < 0 or any(volume < 0 for volume in prior_cumulative_to_same_clock):
        return None
    average = sum((D(volume) for volume in prior_cumulative_to_same_clock), start=D(0)) / D(20)
    if average == 0:
        return None
    return q8(D(cumulative_to_clock) / average)


def cumulative_intraday_volume(bars: list[MarketBar], *, as_of: datetime) -> Decimal | None:
    """Sum intraday bar volume on the same UTC date, up to and including ``as_of``.

    A daily bar makes the result None. A missing bar volume makes the result None.
    Full-day volume is not substituted.
    """
    if as_of.tzinfo is None:
        return None
    day = as_of.astimezone(as_of.tzinfo).date()
    chosen = []
    for bar in bars:
        if bar.interval == "1D":
            return None
        if bar.start.tzinfo is None:
            return None
        start = bar.start.astimezone(as_of.tzinfo)
        if start.date() != day or start > as_of:
            continue
        chosen.append(bar)
    if not chosen:
        return None
    if any(bar.volume is None for bar in chosen):
        return None
    return q8(sum((D(bar.volume) for bar in chosen), start=D(0)))


def intraday_vwap(bars: list[MarketBar]) -> Decimal | None:
    """Volume-weighted average of typical price. Daily bars are refused.

    Typical price is (high + low + close) / 3. A bar without high, low, or
    volume is not approximated from the close.
    """
    if not bars or any(bar.interval == "1D" for bar in bars):
        return None
    if any(bar.high is None or bar.low is None or bar.volume is None for bar in bars):
        return None
    volume = sum((D(bar.volume) for bar in bars), start=D(0))
    if volume == 0:
        return None
    weighted = D(0)
    for bar in bars:
        typical = (D(bar.high) + D(bar.low) + D(bar.close)) / D(3)
        weighted += typical * D(bar.volume)
    return q8(weighted / volume)


def simple_moving_average(closes: list[Decimal], window: int) -> list[Decimal | None]:
    if window < 1:
        raise ValueError("window must be at least 1")
    averages: list[Decimal | None] = []
    for index in range(len(closes)):
        if index + 1 < window:
            averages.append(None)
            continue
        chunk = closes[index + 1 - window : index + 1]
        averages.append(q8(sum((D(value) for value in chunk), start=D(0)) / D(window)))
    return averages
