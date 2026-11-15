"""Setup rules, lifecycle, and unfinished candles. No network and no database."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from analytics.market_stats import MarketBar
from analytics.setups import (
    CONFIRMED,
    CONFIRMED_BREAKOUT,
    INVALIDATED,
    VOLUME_SPIKE,
    WATCHING,
    SetupRules,
    advance,
    detect_at,
    detect_latest,
    drop_unfinished,
    quality_allows,
    replay,
)
from analytics.support_resistance import support_resistance_zones
from core.money import D

START = datetime(2026, 1, 5, tzinfo=timezone.utc)
RULES = SetupRules()


def test_breakout_watch_and_confirmed_breakout_use_only_earlier_zones():
    history = _pivots()
    resistance = _resistance(history)
    near = _append(history, close=resistance.low - D("0.2"), high=resistance.low - D("0.1"), low=resistance.low - D("1"), volume=D("100"))
    watch = _only(detect_latest(near, symbol="RKLB", timeframe="1D", rules=RULES), "BREAKOUT_WATCH")
    assert watch.status == WATCHING
    assert "not an instruction" in watch.invalidation
    confirmed_bars = _append(
        history,
        close=resistance.high + D("1"),
        high=resistance.high + D("1.2"),
        low=resistance.low,
        volume=D("250"),
    )
    confirmed = _only(detect_latest(confirmed_bars, symbol="RKLB", timeframe="1D", rules=RULES), "CONFIRMED_BREAKOUT")
    assert confirmed.status == CONFIRMED
    assert confirmed.rvol is not None and confirmed.rvol >= RULES.breakout_rvol


def test_false_breakout_without_volume_is_not_confirmed():
    history = _pivots()
    resistance = _resistance(history)
    bars = _append(history, close=resistance.high + D("1"), high=resistance.high + D("1.2"), low=resistance.low, volume=D("100"))
    kinds = {signal.setup_type for signal in detect_latest(bars, symbol="RKLB", timeframe="1D", rules=RULES)}
    assert "CONFIRMED_BREAKOUT" not in kinds


def test_support_bounce_and_failed_test():
    history = _pivots()
    support = _support(history)
    bounce = _append(
        history,
        open_=support.low + D("0.2"),
        high=support.high + D("1"),
        low=support.low + D("0.05"),
        close=support.high + D("0.4"),
        volume=D("100"),
    )
    signal = _only(detect_latest(bounce, symbol="RKLB", timeframe="1D", rules=RULES), "SUPPORT_BOUNCE")
    assert signal.status == CONFIRMED
    failed = _append(
        history,
        open_=support.high,
        high=support.high,
        low=support.low + D("0.05"),
        close=support.low + D("0.1"),
        volume=D("100"),
    )
    kinds = {row.setup_type for row in detect_latest(failed, symbol="RKLB", timeframe="1D", rules=RULES)}
    assert "SUPPORT_BOUNCE" not in kinds


def test_volume_spike_and_pullback_and_trend_warning():
    quiet = _trend(last_volume=D("100"))
    assert all(signal.setup_type != VOLUME_SPIKE for signal in detect_latest(quiet, symbol="RKLB", timeframe="1D", rules=RULES))
    spiked = _trend(last_volume=D("400"))
    spike = _only(detect_latest(spiked, symbol="RKLB", timeframe="1D", rules=RULES), VOLUME_SPIKE)
    assert spike.status == CONFIRMED
    pullback = _pullback_series()
    assert any(signal.setup_type == "PULLBACK_WATCH" for signal in detect_latest(pullback, symbol="RKLB", timeframe="1D", rules=RULES))
    warning = _break_support()
    assert any(signal.setup_type == "TREND_WARNING" and signal.status == CONFIRMED for signal in detect_latest(warning, symbol="RKLB", timeframe="1D", rules=RULES))


def test_the_same_structure_is_not_inserted_twice_and_can_be_invalidated():
    history = _pivots()
    resistance = _resistance(history)
    watching = _append(history, close=resistance.low - D("0.2"), high=resistance.low - D("0.1"), low=resistance.low - D("1"), volume=D("100"))
    first = advance([], watching, symbol="RKLB", timeframe="1D", rules=RULES)
    assert len(first.inserted) == 1
    second = advance(first.active, watching, symbol="RKLB", timeframe="1D", rules=RULES)
    assert second.inserted == []
    assert len(second.active) == len(first.active)
    false_break = _append(history, close=resistance.high + D("1"), high=resistance.high + D("1.2"), low=resistance.low, volume=D("100"))
    failed = advance(first.active, false_break, symbol="RKLB", timeframe="1D", rules=RULES)
    assert any(signal.status == INVALIDATED for signal in failed.invalidated)
    assert all(signal.setup_type != CONFIRMED_BREAKOUT for signal in failed.active)
    broken = _append(history, close=resistance.low - D("5"), high=resistance.low - D("4"), low=resistance.low - D("6"), volume=D("100"))
    ended = advance(first.active, broken, symbol="RKLB", timeframe="1D", rules=RULES)
    assert any(signal.status == INVALIDATED for signal in ended.invalidated)
    upgraded_bars = _append(history, close=resistance.high + D("1"), high=resistance.high + D("1.2"), low=resistance.low, volume=D("250"))
    upgraded = advance(first.active, upgraded_bars, symbol="RKLB", timeframe="1D", rules=RULES)
    assert any(signal.setup_type == CONFIRMED_BREAKOUT and signal.status == CONFIRMED for signal in upgraded.active)
    assert not any(signal.setup_type == CONFIRMED_BREAKOUT for signal in upgraded.inserted if signal.structure_key != first.active[0].structure_key)


def test_a_later_candle_does_not_change_an_earlier_signal():
    history = _pivots()
    resistance = _resistance(history)
    bars = _append(history, close=resistance.high + D("1"), high=resistance.high + D("1.2"), low=resistance.low, volume=D("250"))
    original = detect_at(bars, len(bars) - 1, symbol="RKLB", timeframe="1D", rules=RULES)
    future = bars + [_candle(bars[-1].start + timedelta(days=1), D("10"), D("12"), D("9"), D("11"), D("100"))]
    again = detect_at(future, len(bars) - 1, symbol="RKLB", timeframe="1D", rules=RULES)
    assert [(signal.setup_type, signal.status, signal.evidence) for signal in again] == [
        (signal.setup_type, signal.status, signal.evidence) for signal in original
    ]


def test_a_support_bounce_leaves_the_active_list_on_the_next_candle():
    history = _pivots()
    support = _support(history)
    bounce = _append(
        history,
        open_=support.low + D("0.2"),
        high=support.high + D("1"),
        low=support.low + D("0.05"),
        close=support.high + D("0.4"),
        volume=D("100"),
    )
    fresh = replay(bounce, symbol="RKLB", timeframe="5m", rules=RULES)
    assert any(signal.setup_type == "SUPPORT_BOUNCE" and signal.status == CONFIRMED for signal in fresh.active)
    later = bounce + [
        _candle(
            bounce[-1].start + timedelta(days=1),
            support.high + D("1"),
            support.high + D("1.4"),
            support.high + D("0.2"),
            support.high + D("1"),
            D("100"),
        )
    ]
    aged = replay(later, symbol="RKLB", timeframe="5m", rules=RULES)
    assert all(signal.setup_type != "SUPPORT_BOUNCE" for signal in aged.active)
    assert any(signal.setup_type == "SUPPORT_BOUNCE" and signal.status == CONFIRMED for signal in aged.invalidated)


def test_replay_keeps_a_setup_until_a_later_candle_invalidates_it():
    warning = _break_support()
    support = _support(_pivots())
    held = warning + [_candle(warning[-1].start + timedelta(days=1), support.low - D("0.5"), support.low, support.low - D("1"), support.low - D("0.5"), D("100"))]
    result = replay(held, symbol="RKLB", timeframe="1D", rules=RULES)
    assert any(signal.setup_type == "TREND_WARNING" and signal.status == CONFIRMED for signal in result.active)
    spiked = _trend(last_volume=D("400"))
    after = spiked + [_candle(spiked[-1].start + timedelta(days=1), D("140"), D("141"), D("139"), D("140"), D("100"))]
    aged = replay(after, symbol="RKLB", timeframe="1D", rules=RULES)
    assert all(signal.setup_type != VOLUME_SPIKE for signal in aged.active)
    assert any(signal.setup_type == VOLUME_SPIKE and signal.status == CONFIRMED for signal in aged.invalidated)


def test_unfinished_and_unusable_data_do_not_produce_setups():
    start = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)
    bars = [_candle(start, D("10"), D("11"), D("9"), D("10"), D("100"), interval="5min")]
    assert drop_unfinished(bars, start + timedelta(minutes=1)) == []
    assert drop_unfinished(bars, start + timedelta(minutes=5)) == bars
    assert quality_allows("STALE") is False
    assert quality_allows("EOD") is True
    assert quality_allows("DELAYED") is True
    assert detect_latest([], symbol="RKLB", timeframe="1D", rules=RULES) == []
    assert detect_latest(_pivots()[:10], symbol="RKLB", timeframe="1D", rules=RULES) == []


def _pivots() -> list[MarketBar]:
    bars = []
    for index in range(39):
        low, high, close = D("49"), D("51"), D("50")
        if index in {5, 12, 20}:
            low, high, close = D("46.2"), D("47.0"), D("46.6")
        if index in {8, 15, 24}:
            low, high, close = D("55.4"), D("56.4"), D("56.0")
        bars.append(_candle(START + timedelta(days=index), close, high, low, close, D("100")))
    return bars


def _resistance(history: list[MarketBar]):
    zones = [zone for zone in support_resistance_zones(history) if zone.kind == "resistance"]
    assert zones
    return min(zones, key=lambda zone: zone.low)


def _support(history: list[MarketBar]):
    zones = [zone for zone in support_resistance_zones(history) if zone.kind == "support"]
    assert zones
    return max(zones, key=lambda zone: zone.high)


def _append(history, *, close, high, low, volume, open_=None):
    return history + [_candle(history[-1].start + timedelta(days=1), open_ or close, high, low, close, volume)]


def _trend(last_volume: Decimal) -> list[MarketBar]:
    bars = []
    for index in range(40):
        price = D(100 + index)
        volume = last_volume if index == 39 else D("100")
        bars.append(_candle(START + timedelta(days=index), price, price + D("1"), price - D("1"), price, volume))
    return bars


def _pullback_series() -> list[MarketBar]:
    bars = _trend(D("100"))
    from analytics.market_stats import exponential_moving_average

    earlier = exponential_moving_average([bar.close for bar in bars[:-1]], 20)[-1]
    assert earlier is not None
    last = bars[-1]
    bars[-1] = _candle(last.start, earlier, earlier + D("0.2"), earlier - D("0.2"), earlier, D("100"))
    return bars


def _break_support() -> list[MarketBar]:
    history = _pivots()
    support = _support(history)
    return _append(history, close=support.low - D("1"), high=support.low, low=support.low - D("1.5"), volume=D("100"))


def _only(signals, setup_type):
    chosen = [signal for signal in signals if signal.setup_type == setup_type]
    assert len(chosen) == 1
    return chosen[0]


def _candle(moment, open_, high, low, close, volume, interval="1D") -> MarketBar:
    return MarketBar(
        start=moment,
        interval=interval,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(volume),
        currency="USD",
        source="twelvedata",
    )
