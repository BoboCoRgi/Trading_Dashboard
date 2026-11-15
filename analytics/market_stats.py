"""Volume, VWAP, and relative-performance maths.

Daily relative volume uses full-session volume only.
Intraday relative volume uses cumulative volume up to the same clock.
VWAP is computed from intraday bars only. Daily bars are refused.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.money import D, q8

NEW_YORK = ZoneInfo("America/New_York")
_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(16, 0)


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


def vwap_series(bars: list[MarketBar]) -> list[Decimal | None]:
    """Cumulative intraday VWAP, reset on each UTC date.

    Daily bars are refused: every point is None. A bar missing high, low, or
    volume stops the session series instead of being filled from the close.
    """
    if not bars or any(bar.interval == "1D" for bar in bars):
        return [None] * len(bars)
    results: list[Decimal | None] = []
    current_day = None
    weighted = D(0)
    volume = D(0)
    blocked = False
    for bar in bars:
        if bar.start.tzinfo is None:
            results.append(None)
            blocked = True
            continue
        day = bar.start.astimezone(timezone.utc).date()
        if day != current_day:
            current_day = day
            weighted = D(0)
            volume = D(0)
            blocked = False
        if blocked or bar.high is None or bar.low is None or bar.volume is None:
            blocked = True
            results.append(None)
            continue
        typical = (D(bar.high) + D(bar.low) + D(bar.close)) / D(3)
        weighted += typical * D(bar.volume)
        volume += D(bar.volume)
        results.append(None if volume == 0 else q8(weighted / volume))
    return results


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


def exponential_moving_average(closes: list[Decimal], window: int) -> list[Decimal | None]:
    """Causal EMA. The seed is the SMA of the first window. Later closes are not used."""
    if window < 1:
        raise ValueError("window must be at least 1")
    averages: list[Decimal | None] = [None] * len(closes)
    if len(closes) < window:
        return averages
    seed = sum((D(value) for value in closes[:window]), start=D(0)) / D(window)
    averages[window - 1] = q8(seed)
    alpha = D(2) / D(window + 1)
    previous = seed
    for index in range(window, len(closes)):
        previous = alpha * D(closes[index]) + (D(1) - alpha) * previous
        averages[index] = q8(previous)
    return averages


def in_regular_session(moment: datetime) -> bool:
    """US cash session, 09:30 inclusive to 16:00 exclusive in America/New_York."""
    if moment.tzinfo is None:
        return False
    local = moment.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return False
    clock = local.timetz().replace(tzinfo=None)
    return _SESSION_OPEN <= clock < _SESSION_CLOSE


def session_date(moment: datetime):
    return moment.astimezone(NEW_YORK).date()


def minutes_from_open(moment: datetime) -> int | None:
    if not in_regular_session(moment):
        return None
    local = moment.astimezone(NEW_YORK)
    opened = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return int((local - opened) / timedelta(minutes=1))


def session_vwap_series(bars: list[MarketBar]) -> list[Decimal | None]:
    """VWAP that resets at each regular US session. Extended-hours bars stay empty.

    A bar missing high, low, or volume blocks the rest of that session. The
    next session starts again. Nothing is filled from the close.
    """
    if not bars or any(bar.interval == "1D" or bar.interval == "1W" for bar in bars):
        return [None] * len(bars)
    results: list[Decimal | None] = []
    current = None
    weighted = D(0)
    volume = D(0)
    blocked = False
    for bar in bars:
        if bar.start.tzinfo is None or not in_regular_session(bar.start):
            results.append(None)
            continue
        key = session_date(bar.start)
        if key != current:
            current = key
            weighted = D(0)
            volume = D(0)
            blocked = False
        if blocked or bar.high is None or bar.low is None or bar.volume is None:
            blocked = True
            results.append(None)
            continue
        typical = (D(bar.high) + D(bar.low) + D(bar.close)) / D(3)
        weighted += typical * D(bar.volume)
        volume += D(bar.volume)
        results.append(None if volume == 0 else q8(weighted / volume))
    return results


def previous_session_high_low(bars: list[MarketBar]) -> tuple[Decimal | None, Decimal | None]:
    """High and low of the period before the latest one.

    Daily and weekly charts use the previous bar. Intraday charts use the
    previous regular US session. A missing high or low is not copied from the close.
    """
    if bars and bars[-1].interval in {"1D", "1W"}:
        complete = [bar for bar in bars if bar.interval == bars[-1].interval and bar.high is not None and bar.low is not None]
        if len(complete) < 2:
            return None, None
        prior = complete[-2]
        return D(prior.high), D(prior.low)
    sessions: dict = {}
    for bar in bars:
        if bar.start.tzinfo is None or not in_regular_session(bar.start):
            continue
        if bar.high is None or bar.low is None:
            continue
        sessions.setdefault(session_date(bar.start), []).append(bar)
    if len(sessions) < 2:
        return None, None
    prior = sessions[sorted(sessions)[-2]]
    return max(D(bar.high) for bar in prior), min(D(bar.low) for bar in prior)


def latest_relative_volume(bars: list[MarketBar]) -> Decimal | None:
    """RVOL at the last bar, using only earlier comparable sessions.

    Daily bars use the previous 20 full sessions. Intraday bars use cumulative
    volume up to the same minute-from-open. A session missing that clock is
    skipped, and fewer than 20 comparable sessions returns None.
    """
    if not bars:
        return None
    if bars[-1].interval == "1D":
        volumes = [D(bar.volume) for bar in bars if bar.interval == "1D" and bar.volume is not None]
        if len(volumes) < 21 or any(bar.interval == "1D" and bar.volume is None for bar in bars):
            return None
        return daily_relative_volume(session_volume=volumes[-1], prior_session_volumes=volumes[-21:-1])
    if bars[-1].interval in {"1W", "1D"} or bars[-1].start.tzinfo is None:
        return None
    offset = minutes_from_open(bars[-1].start)
    if offset is None:
        return None
    current_day = session_date(bars[-1].start)
    current = _cumulative_to_offset(bars, current_day, offset)
    if current is None:
        return None
    priors = []
    for day in sorted({session_date(bar.start) for bar in bars if bar.start.tzinfo and in_regular_session(bar.start)}):
        if day >= current_day:
            continue
        value = _cumulative_to_offset(bars, day, offset)
        if value is None:
            continue
        priors.append(value)
    if len(priors) < 20:
        return None
    return intraday_relative_volume(
        cumulative_to_clock=current,
        prior_cumulative_to_same_clock=priors[-20:],
    )


def _cumulative_to_offset(bars: list[MarketBar], day, offset: int) -> Decimal | None:
    chosen = []
    saw_clock = False
    for bar in bars:
        if bar.start.tzinfo is None or not in_regular_session(bar.start) or session_date(bar.start) != day:
            continue
        bar_offset = minutes_from_open(bar.start)
        if bar_offset is None or bar_offset > offset:
            continue
        if bar.volume is None:
            return None
        chosen.append(bar)
        if bar_offset == offset:
            saw_clock = True
    if not saw_clock or not chosen:
        return None
    return q8(sum((D(bar.volume) for bar in chosen), start=D(0)))
