"""Holding-chart indicators, resampling, and session boundaries. No network."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from analytics.chart_ranges import history_cap_days, history_window, timeframe_interval
from analytics.market_stats import (
    MarketBar,
    exponential_moving_average,
    latest_relative_volume,
    previous_session_high_low,
    session_vwap_series,
    simple_moving_average,
)
from analytics.resample import resample_bars
from connectors.market_data.bars import PriceBar
from core.money import D
from services.market_data import MarketDataService, bar_status

NOW = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)
INSTRUMENT = "11111111-1111-1111-1111-111111111111"


def test_ema_and_sma_ignore_later_closes():
    closes = [D(str(index)) for index in range(1, 31)]
    ema = exponential_moving_average(closes, 9)
    sma = simple_moving_average(closes, 9)
    assert ema[7] is None
    assert sma[7] is None
    assert ema[8] is not None
    changed = closes[:-1] + [D("1000")]
    assert exponential_moving_average(changed, 9)[-2] == ema[-2]
    assert simple_moving_average(changed, 9)[-2] == sma[-2]
    assert exponential_moving_average(closes[:8], 9) == [None] * 8


def test_session_vwap_resets_on_the_regular_session_and_skips_premarket():
    monday = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [
        _market(monday - timedelta(minutes=30), "5min", "9", "9", "9", "50"),
        _market(monday, "5min", "10", "10", "10", "100"),
        _market(monday + timedelta(minutes=5), "5min", "16", "10", "16", "100"),
        _market(monday + timedelta(days=1), "5min", "8", "8", "8", "50"),
    ]
    series = session_vwap_series(bars)
    assert series[0] is None
    assert series[1] == D("10")
    assert series[2] == D("12")
    assert series[3] == D("8")
    assert session_vwap_series([_market(monday, "1D", "10", "10", "10", "100")]) == [None]


def test_previous_session_uses_only_the_prior_regular_session():
    monday = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars = [
        _market(monday, "5min", "12", "10", "11", "100"),
        _market(monday + timedelta(days=1), "5min", "20", "18", "19", "100"),
        _market(monday + timedelta(days=1, minutes=5), "5min", "21", "17", "19", "100"),
    ]
    high, low = previous_session_high_low(bars)
    assert high == D("12")
    assert low == D("10")
    assert previous_session_high_low(bars[:1]) == (None, None)
    daily = [
        _market(datetime(2026, 1, 5, tzinfo=timezone.utc), "1D", "12", "10", "11", "100"),
        _market(datetime(2026, 1, 6, tzinfo=timezone.utc), "1D", "20", "18", "19", "100"),
    ]
    assert previous_session_high_low(daily) == (D("12"), D("10"))


def test_intraday_rvol_needs_the_same_clock_and_ignores_later_bars():
    opens = _weekdays(21)
    bars = [_market(moment, "5min", "11", "10", "11", "10") for moment in opens[:-1]]
    bars.append(_market(opens[-1], "5min", "11", "10", "11", "20"))
    bars.append(_market(opens[-1] + timedelta(minutes=5), "5min", "11", "10", "11", "999"))
    assert latest_relative_volume(bars[:-1]) == D("2")
    sparse = [bar for bar in bars[:-1] if bar.start != opens[0]]
    assert latest_relative_volume(sparse) is None


def test_fifteen_minutes_requires_three_complete_five_minute_bars():
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    complete = [_price(start + timedelta(minutes=5 * step), "5min") for step in range(3)]
    built = resample_bars(complete, "15min")
    assert len(built) == 1
    assert built[0].open == D("10")
    assert built[0].close == D("12")
    assert built[0].high == D("13")
    assert built[0].low == D("9")
    assert built[0].volume == D("30")
    missing_middle = [complete[0], complete[2]]
    assert resample_bars(missing_middle, "15min") == []
    incomplete = PriceBar(**{**complete[1].__dict__, "high": None})
    assert resample_bars([complete[0], incomplete, complete[2]], "15min") == []


def test_weekly_bars_aggregate_available_days_and_do_not_invent_volume():
    monday = datetime(2026, 1, 5, tzinfo=timezone.utc)
    days = [monday, monday + timedelta(days=2), monday + timedelta(days=4)]
    bars = [
        _price(days[0], "1D", close=D("10"), high=D("11"), low=D("9"), open_=D("10"), volume=D("5")),
        _price(days[1], "1D", close=D("12"), high=D("14"), low=D("8"), open_=D("11"), volume=None),
        _price(days[2], "1D", close=D("13"), high=D("13"), low=D("12"), open_=D("12"), volume=D("7")),
    ]
    week = resample_bars(bars, "1W")
    assert len(week) == 1
    assert week[0].timestamp == days[0]
    assert week[0].open == D("10")
    assert week[0].close == D("13")
    assert week[0].high == D("14")
    assert week[0].low == D("8")
    assert week[0].volume is None
    without_wednesday = resample_bars([bars[0], bars[2]], "1W")
    assert len(without_wednesday) == 1
    assert without_wednesday[0].volume == D("12")


def test_chart_series_resamples_cached_bars_without_calling_the_provider():
    start, _end = history_window("1D", "15m", NOW)
    calls = []
    rows = []
    for bucket in (start, NOW - timedelta(minutes=15)):
        for step in range(3):
            rows.append(_price(bucket + timedelta(minutes=5 * step), "5min"))
    service = MarketDataService(_MemoryCache(rows), _Provider(calls), now=NOW)
    result = service.chart_series(
        instrument_id=INSTRUMENT,
        symbol="RKLB",
        range_code="1D",
        timeframe="15m",
        currency="USD",
    )
    assert calls == []
    assert result.status == "ready"
    assert result.aggregated_from == "5min"
    assert len(result.bars) == 2
    assert all(bar.interval == "15min" for bar in result.bars)


def test_missing_cache_does_not_invent_candles():
    calls = []
    service = MarketDataService(_MemoryCache(), _Provider(calls), now=NOW)
    empty = service.chart_series(
        instrument_id=INSTRUMENT,
        symbol="RKLB",
        range_code="1D",
        timeframe="15m",
        currency="USD",
    )
    assert empty.bars == []
    assert calls == ["15min"]
    offline = MarketDataService(_MemoryCache(), None, now=NOW).chart_series(
        instrument_id=INSTRUMENT,
        symbol="RKLB",
        range_code="1M",
        timeframe="1D",
        currency="USD",
    )
    assert offline.status == "missing_key"
    assert offline.bars == []


def test_timeframe_windows_and_freshness_labels():
    assert timeframe_interval("5m") == "5min"
    assert timeframe_interval("1W") == "1W"
    assert history_cap_days("ALL", "5m") == 10
    assert history_cap_days("3M", "5m") == 10
    assert history_cap_days("1D", "5m") is None
    start, end = history_window("1W", "1D", NOW)
    assert end - start == timedelta(days=7)
    fresh = [_price(NOW - timedelta(minutes=5), "5min")]
    assert bar_status(fresh, "5min", NOW) == "DELAYED"
    stale = [_price(NOW - timedelta(hours=2), "5min")]
    assert bar_status(stale, "5min", NOW) == "STALE"


class _Provider:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def fetch(self, symbol, interval, start, end):
        self.calls.append(interval)
        return []


class _MemoryCache:
    def __init__(self, rows: list[PriceBar] | None = None) -> None:
        self.rows = list(rows or [])

    def load(self, instrument_id, interval, start, end):
        return [
            bar for bar in self.rows
            if bar.instrument_id == instrument_id and bar.interval == interval and start <= bar.timestamp <= end
        ]

    def save(self, bars):
        self.rows.extend(bars)


def _weekdays(count: int) -> list[datetime]:
    cursor = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    found = []
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor)
        cursor += timedelta(days=1)
    return found


def _market(moment, interval, high, low, close, volume) -> MarketBar:
    return MarketBar(
        start=moment,
        interval=interval,
        open=D(close),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(volume),
        currency="USD",
        source="test",
    )


def _price(moment, interval, close=D("11"), volume=D("10"), high=None, low=None, open_=None) -> PriceBar:
    step = int((moment - datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)) / timedelta(minutes=5))
    close = close if close != D("11") or interval == "1D" else D(str(10 + step))
    return PriceBar(
        timestamp=moment,
        interval=interval,
        open=open_ if open_ is not None else D("10"),
        high=high if high is not None else D("13"),
        low=low if low is not None else D("9"),
        close=close if interval != "5min" else D(str(10 + max(step, 0))),
        volume=volume,
        currency="USD",
        source="twelvedata",
        is_delayed=True,
        symbol="RKLB",
        instrument_id=INSTRUMENT,
        fetched_at=moment,
    )
