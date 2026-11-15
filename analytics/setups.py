"""Deterministic setup rules on completed candles.

``detect_at`` uses only bars up to and including that index. Zones are built
from the bars before the candle being judged. The same functions can be called
once per historical index for a later backtest.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from analytics.market_stats import (
    NEW_YORK,
    MarketBar,
    exponential_moving_average,
    in_regular_session,
    latest_relative_volume,
    simple_moving_average,
)
from analytics.support_resistance import PriceZone, support_resistance_zones
from core.money import D, q8

WATCHING = "WATCHING"
CONFIRMED = "CONFIRMED"
INVALIDATED = "INVALIDATED"

BREAKOUT_WATCH = "BREAKOUT_WATCH"
CONFIRMED_BREAKOUT = "CONFIRMED_BREAKOUT"
PULLBACK_WATCH = "PULLBACK_WATCH"
SUPPORT_BOUNCE = "SUPPORT_BOUNCE"
VOLUME_SPIKE = "VOLUME_SPIKE"
TREND_WARNING = "TREND_WARNING"

BREAKOUT_FAMILY = {BREAKOUT_WATCH, CONFIRMED_BREAKOUT}
USABLE_QUALITY = {"LIVE", "DELAYED", "EOD"}
_INTERVAL_MINUTES = {"5min": 5, "15min": 15, "1h": 60}


@dataclass(frozen=True)
class SetupRules:
    approach_fraction: Decimal = Decimal("0.01")
    breakout_rvol: Decimal = Decimal("1.5")
    volume_spike_rvol: Decimal = Decimal("2")
    pullback_fraction: Decimal = Decimal("0.015")
    trend_lookback: int = 5
    min_bars: int = 25


@dataclass(frozen=True)
class SetupSignal:
    symbol: str
    timeframe: str
    setup_type: str
    status: str
    candle_time: datetime
    level_low: Decimal | None
    level_high: Decimal | None
    close: Decimal
    rvol: Decimal | None
    ema20: Decimal | None
    evidence: str
    invalidation: str
    structure_key: str


@dataclass(frozen=True)
class AdvanceResult:
    active: list[SetupSignal]
    invalidated: list[SetupSignal]
    inserted: list[SetupSignal]


def quality_allows(status: str) -> bool:
    return status in USABLE_QUALITY


def drop_unfinished(bars: list[MarketBar], now: datetime) -> list[MarketBar]:
    """Remove the last bar when its interval has not closed. Earlier bars stay."""
    if not bars or now.tzinfo is None or bars[-1].start.tzinfo is None:
        return list(bars)
    last = bars[-1]
    minutes = _INTERVAL_MINUTES.get(last.interval)
    if minutes is not None and last.start + timedelta(minutes=minutes) > now:
        return list(bars[:-1])
    if last.interval == "1D" and _daily_bar_open(last, now):
        return list(bars[:-1])
    if last.interval == "1W" and _weekly_bar_open(last, now):
        return list(bars[:-1])
    return list(bars)


def detect_at(bars: list[MarketBar], index: int, *, symbol: str, timeframe: str, rules: SetupRules | None = None) -> list[SetupSignal]:
    """Setups confirmed by the candle at ``index``. Later bars are not read."""
    rules = rules or SetupRules()
    if index < rules.min_bars - 1 or index >= len(bars):
        return []
    window = bars[: index + 1]
    candle = window[-1]
    if candle.open is None or candle.high is None or candle.low is None or candle.volume is None:
        return []
    history = window[:-1]
    zones = support_resistance_zones(history) if len(history) >= 5 else []
    closes = [bar.close for bar in window]
    ema = exponential_moving_average(closes, 20)
    sma = simple_moving_average(closes, 50)
    rvol = latest_relative_volume(window)
    found: list[SetupSignal] = []
    prior_close = history[-1].close if history else candle.close
    overhead = [zone for zone in zones if zone.kind == "resistance" and zone.low > prior_close]
    resistance = min(overhead, key=lambda zone: zone.low) if overhead else None
    support = _tested_support(zones, candle)
    if resistance is not None and candle.close > resistance.high and rvol is not None and rvol >= rules.breakout_rvol:
        found.append(_breakout(symbol, timeframe, candle, resistance, rvol, CONFIRMED_BREAKOUT, CONFIRMED, rules))
    elif resistance is not None and candle.close < resistance.low and _near(resistance.low, candle.close, rules.approach_fraction):
        found.append(_breakout(symbol, timeframe, candle, resistance, rvol, BREAKOUT_WATCH, WATCHING, rules))
    if support is not None and candle.low <= support.high and candle.low >= support.low and candle.close > candle.open and candle.close > support.high:
        found.append(_bounce(symbol, timeframe, candle, support, rvol, ema[index]))
    pullback = _pullback(symbol, timeframe, window, ema, sma, rvol, rules)
    if pullback is not None:
        found.append(pullback)
    warning = _trend_warning(symbol, timeframe, window, zones, ema, rvol, rules)
    if warning is not None:
        found.append(warning)
    if rvol is not None and rvol >= rules.volume_spike_rvol:
        found.append(_spike(symbol, timeframe, candle, rvol, ema[index], rules))
    return found


def replay(bars: list[MarketBar], *, symbol: str, timeframe: str, rules: SetupRules | None = None) -> AdvanceResult:
    """Walk completed candles in order. Each step sees only the bars available then."""
    rules = rules or SetupRules()
    state: list[SetupSignal] = []
    closed: dict[str, SetupSignal] = {}
    if len(bars) < rules.min_bars:
        return AdvanceResult(active=[], invalidated=[], inserted=[])
    for index in range(rules.min_bars - 1, len(bars)):
        step = advance(state, bars[: index + 1], symbol=symbol, timeframe=timeframe, rules=rules)
        active_keys = {signal.structure_key for signal in step.active}
        for signal in step.invalidated:
            closed[signal.structure_key] = signal
        for signal in state:
            if signal.structure_key not in active_keys and signal.structure_key not in {row.structure_key for row in step.invalidated}:
                closed[signal.structure_key] = signal
        for signal in step.active:
            closed.pop(signal.structure_key, None)
        state = step.active
    return AdvanceResult(active=state, invalidated=list(closed.values()), inserted=[])


def detect_latest(bars: list[MarketBar], *, symbol: str, timeframe: str, rules: SetupRules | None = None) -> list[SetupSignal]:
    if not bars:
        return []
    return detect_at(bars, len(bars) - 1, symbol=symbol, timeframe=timeframe, rules=rules)


def advance(active: list[SetupSignal], bars: list[MarketBar], *, symbol: str, timeframe: str, rules: SetupRules | None = None) -> AdvanceResult:
    """Move stored setups forward one completed candle. The same structure is not inserted twice."""
    rules = rules or SetupRules()
    if not bars:
        return AdvanceResult(active=list(active), invalidated=[], inserted=[])
    index = len(bars) - 1
    latest = bars[index]
    invalidated: list[SetupSignal] = []
    surviving: list[SetupSignal] = []
    for signal in active:
        if signal.setup_type == VOLUME_SPIKE and signal.candle_time != latest.start:
            continue
        if signal.setup_type == SUPPORT_BOUNCE and signal.candle_time != latest.start:
            if _is_invalid(signal, bars, index, rules):
                invalidated.append(replace(signal, status=INVALIDATED, candle_time=latest.start, close=latest.close))
            continue
        if _is_invalid(signal, bars, index, rules):
            invalidated.append(replace(signal, status=INVALIDATED, candle_time=latest.start, close=latest.close))
            continue
        surviving.append(signal)
    triggered = detect_at(bars, index, symbol=symbol, timeframe=timeframe, rules=rules)
    inserted: list[SetupSignal] = []
    refreshed: list[SetupSignal] = []
    used: set[int] = set()
    for signal in triggered:
        match_at = _match_index(surviving, signal, used)
        if match_at is None:
            inserted.append(signal)
            continue
        used.add(match_at)
        refreshed.append(_merge(surviving[match_at], signal))
    kept = [signal for position, signal in enumerate(surviving) if position not in used]
    return AdvanceResult(active=[*refreshed, *kept, *inserted], invalidated=invalidated, inserted=inserted)


def _breakout(symbol, timeframe, candle, zone: PriceZone, rvol, setup_type, status, rules: SetupRules) -> SetupSignal:
    if status == CONFIRMED:
        evidence = (
            f"On {_when(candle)}, the completed candle closed at {_num(candle.close)} above resistance {_num(zone.low)}–{_num(zone.high)}. "
            f"Relative volume was {_num(rvol)}, at or above {_num(rules.breakout_rvol)}."
        )
        invalidation = "Invalid if a later completed candle closes back below the zone low. This is not an instruction to buy."
    else:
        evidence = (
            f"On {_when(candle)}, the completed close {_num(candle.close)} is within {_num(rules.approach_fraction)} of resistance {_num(zone.low)}–{_num(zone.high)} "
            "and has not closed through it."
        )
        invalidation = "Invalid if price moves away from the zone or closes through it without the relative-volume rule. This is not an instruction to buy."
    return _signal(
        symbol, timeframe, setup_type, status, candle, zone.low, zone.high, rvol, None, evidence, invalidation, _zone_key(zone)
    )


def _bounce(symbol, timeframe, candle, zone: PriceZone, rvol, ema20) -> SetupSignal:
    evidence = (
        f"On {_when(candle)}, the completed candle traded into support {_num(zone.low)}–{_num(zone.high)} and closed at {_num(candle.close)}, "
        "back above the zone and above its open."
    )
    invalidation = "Invalid if a later completed candle closes below the zone low. This is not an instruction to buy."
    return _signal(
        symbol, timeframe, SUPPORT_BOUNCE, CONFIRMED, candle, zone.low, zone.high, rvol, ema20, evidence, invalidation, _zone_key(zone)
    )


def _pullback(symbol, timeframe, window, ema, sma, rvol, rules: SetupRules) -> SetupSignal | None:
    index = len(window) - 1
    candle = window[-1]
    current = ema[index]
    earlier = ema[index - rules.trend_lookback] if index >= rules.trend_lookback else None
    slow = sma[index]
    if current is None or earlier is None or current <= earlier:
        return None
    if slow is not None and candle.close < slow:
        return None
    if candle.close < current:
        return None
    if not _near(current, candle.close, rules.pullback_fraction):
        return None
    evidence = (
        f"On {_when(candle)}, EMA20 is {_num(current)} and has risen over {rules.trend_lookback} completed candles. "
        f"The close {_num(candle.close)} is back within {_num(rules.pullback_fraction)} of that average."
    )
    invalidation = "Invalid if a completed candle closes outside the EMA20 band or EMA20 stops rising. This is not an instruction to buy."
    return _signal(
        symbol, timeframe, PULLBACK_WATCH, WATCHING, candle, current, current, rvol, current, evidence, invalidation, "ema20"
    )


def _trend_warning(symbol, timeframe, window, zones, ema, rvol, rules: SetupRules) -> SetupSignal | None:
    index = len(window) - 1
    candle = window[-1]
    broken = [zone for zone in zones if zone.kind == "support" and candle.close < zone.low]
    if broken:
        zone = max(broken, key=lambda item: item.low)
        evidence = f"On {_when(candle)}, the completed close {_num(candle.close)} is below support {_num(zone.low)}–{_num(zone.high)}."
        invalidation = "Invalid if a later completed candle closes back above the zone. This is not an instruction to sell."
        return _signal(
            symbol, timeframe, TREND_WARNING, CONFIRMED, candle, zone.low, zone.high, rvol, ema[index], evidence, invalidation, _zone_key(zone)
        )
    current = ema[index]
    earlier = ema[index - rules.trend_lookback] if index >= rules.trend_lookback else None
    previous = window[-2].close if len(window) >= 2 else None
    previous_ema = ema[index - 1] if index else None
    if current is None or earlier is None or previous is None or previous_ema is None:
        return None
    if previous >= previous_ema and candle.close < current and current < earlier:
        evidence = (
            f"On {_when(candle)}, the close {_num(candle.close)} is below EMA20 {_num(current)}, and that average has fallen "
            f"over the last {rules.trend_lookback} completed candles."
        )
        invalidation = "Invalid if a completed candle closes back above a rising EMA20. This is not an instruction to sell."
        return _signal(
            symbol, timeframe, TREND_WARNING, WATCHING, candle, current, current, rvol, current, evidence, invalidation, "ema20-weak"
        )
    return None


def _spike(symbol, timeframe, candle, rvol, ema20, rules: SetupRules) -> SetupSignal:
    evidence = f"On {_when(candle)}, relative volume was {_num(rvol)}, at or above {_num(rules.volume_spike_rvol)}."
    invalidation = "The observation applies to this completed candle only. It is not an instruction to trade."
    key = f"rvol:{candle.start.isoformat()}"
    return _signal(symbol, timeframe, VOLUME_SPIKE, CONFIRMED, candle, None, None, rvol, ema20, evidence, invalidation, key)


def _is_invalid(signal: SetupSignal, bars: list[MarketBar], index: int, rules: SetupRules) -> bool:
    candle = bars[index]
    close = candle.close
    if signal.setup_type == VOLUME_SPIKE:
        return False
    if signal.setup_type == BREAKOUT_WATCH:
        rvol = latest_relative_volume(bars[: index + 1])
        if signal.level_high is not None and close > signal.level_high and (rvol is None or rvol < rules.breakout_rvol):
            return True
        if signal.level_low is not None and close < signal.level_low * (D(1) - rules.approach_fraction):
            return True
        return False
    if signal.setup_type == CONFIRMED_BREAKOUT:
        return signal.level_low is not None and close < signal.level_low
    if signal.setup_type == SUPPORT_BOUNCE:
        return signal.level_low is not None and close < signal.level_low
    if signal.setup_type == TREND_WARNING and signal.structure_key.startswith("zone:"):
        return signal.level_high is not None and close > signal.level_high
    if signal.setup_type in {PULLBACK_WATCH, TREND_WARNING}:
        ema = exponential_moving_average([bar.close for bar in bars[: index + 1]], 20)
        current = ema[index]
        earlier = ema[index - rules.trend_lookback] if index >= rules.trend_lookback else None
        if current is None or earlier is None:
            return False
        if signal.setup_type == PULLBACK_WATCH:
            away = close < current * (D(1) - rules.pullback_fraction) or close > current * (D(1) + rules.pullback_fraction)
            return away or current <= earlier
        return close > current and current > earlier
    return False


def _signal(symbol, timeframe, setup_type, status, candle, low, high, rvol, ema20, evidence, invalidation, key) -> SetupSignal:
    return SetupSignal(
        symbol=symbol,
        timeframe=timeframe,
        setup_type=setup_type,
        status=status,
        candle_time=candle.start,
        level_low=None if low is None else q8(D(low)),
        level_high=None if high is None else q8(D(high)),
        close=q8(D(candle.close)),
        rvol=None if rvol is None else q8(D(rvol)),
        ema20=None if ema20 is None else q8(D(ema20)),
        evidence=evidence,
        invalidation=invalidation,
        structure_key=key,
    )


def _zone_key(zone: PriceZone) -> str:
    return f"zone:{_key_price(zone.low)}:{_key_price(zone.high)}"


def _num(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    number = D(value)
    if abs(number) >= 1:
        return f"{number:.2f}"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _when(candle: MarketBar) -> str:
    return candle.start.date().isoformat()


def _key_price(value: Decimal) -> str:
    return f"{D(value).quantize(Decimal('0.0001'))}"


def _near(level: Decimal, close: Decimal, fraction: Decimal) -> bool:
    if level == 0:
        return False
    return abs(D(level) - D(close)) / abs(D(level)) <= fraction


def _tested_support(zones: list[PriceZone], candle: MarketBar) -> PriceZone | None:
    if candle.low is None:
        return None
    chosen = [zone for zone in zones if zone.kind == "support" and candle.low <= zone.high and candle.low >= zone.low]
    if not chosen:
        return None
    return min(chosen, key=lambda zone: abs(zone.low - candle.low))


def _match_index(active: list[SetupSignal], signal: SetupSignal, used: set[int]) -> int | None:
    for position, existing in enumerate(active):
        if position in used or existing.structure_key != signal.structure_key:
            continue
        same = existing.setup_type == signal.setup_type
        family = existing.setup_type in BREAKOUT_FAMILY and signal.setup_type in BREAKOUT_FAMILY
        if same or family:
            return position
    return None


def _merge(existing: SetupSignal, triggered: SetupSignal) -> SetupSignal:
    status = CONFIRMED if triggered.status == CONFIRMED or existing.status == CONFIRMED else triggered.status
    setup_type = triggered.setup_type if triggered.status == CONFIRMED else existing.setup_type
    if existing.setup_type == CONFIRMED_BREAKOUT:
        setup_type = CONFIRMED_BREAKOUT
        status = CONFIRMED
    return replace(
        triggered,
        setup_type=setup_type,
        status=status,
        evidence=triggered.evidence,
    )


def _daily_bar_open(bar: MarketBar, now: datetime) -> bool:
    session_day = bar.start.astimezone(now.tzinfo).date()
    if now.astimezone(NEW_YORK).date() != session_day:
        return False
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 5 and in_regular_session(now)


def _weekly_bar_open(bar: MarketBar, now: datetime) -> bool:
    start_week = bar.start.astimezone(now.tzinfo).date().isocalendar()[:2]
    now_week = now.astimezone(now.tzinfo).date().isocalendar()[:2]
    if start_week != now_week:
        return False
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 4 or (local.weekday() == 4 and in_regular_session(now))
