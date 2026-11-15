"""Execution timing, costs, gaps and look-ahead. Prices are hand-built."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from analytics.backtest import CostModel, RiskModel, Signal, _gaps, _simulate, compare_variants, generate_signals
from analytics.market_stats import MarketBar
from core.money import D

START = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
ZERO = CostModel(Decimal("0"), Decimal("0"), Decimal("0"))
RISK = RiskModel(capital=Decimal("10000"), risk_fraction=Decimal("0.01"), stop_fraction=Decimal("0.02"), target_r=Decimal("2"), max_hold_bars=5)


def test_stop_and_target_in_one_candle_uses_the_stop():
    bars = [
        _day(0, "100", "101", "99", "100"),
        _day(1, "100", "110", "90", "105"),
    ]
    result = _simulate(bars, [Signal(0, "manual")], "1D", ZERO, RISK, [])
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.reason == "same_candle_stop"
    assert trade.entry_price == Decimal("100")
    assert trade.exit_price == Decimal("98")
    assert trade.pnl == Decimal("-100")
    assert trade.r_multiple == Decimal("-1")


def test_a_gap_through_the_stop_fills_at_the_open():
    bars = [
        _day(0, "100", "101", "99", "100"),
        _day(1, "100", "101", "99", "100"),
        _day(2, "90", "92", "89", "91"),
    ]
    trade = _simulate(bars, [Signal(0, "manual")], "1D", ZERO, RISK, []).trades[0]
    assert trade.reason == "gap_stop"
    assert trade.exit_price == Decimal("90")
    assert trade.pnl == Decimal("-500")


def test_target_and_costs_change_the_fill():
    costs = CostModel(Decimal("0"), Decimal("100"), Decimal("0"))
    bars = [
        _day(0, "100", "101", "99", "100"),
        _day(1, "100", "100", "100", "100"),
        _day(2, "120", "121", "119", "120"),
    ]
    trade = _simulate(bars, [Signal(0, "manual")], "1D", costs, RISK, []).trades[0]
    assert trade.entry_price == Decimal("101")
    assert trade.entry_time == bars[1].start
    assert trade.signal_time == bars[0].start
    assert trade.pnl < 0 or trade.exit_price < trade.entry_price or trade.reason in {"target", "time_stop", "stop", "gap_target"}


def test_a_future_bar_does_not_change_an_earlier_signal_or_fill():
    opening = [_intraday(offset, "10", "10.2", "9.8", "10") for offset in range(0, 30, 5)]
    trigger = _intraday(30, "10", "11", "9.9", "10.8")
    after = _intraday(35, "10.8", "10.9", "10.7", "10.8")
    future = _intraday(40, "20", "21", "19", "20.5")
    base = opening + [trigger, after]
    early = generate_signals(base, "orb", timeframe="5m", orb_minutes=30)
    later = generate_signals(base + [future], "orb", timeframe="5m", orb_minutes=30)
    assert [item.index for item in early] == [item.index for item in later if item.index < len(base)]
    assert early
    first = _simulate(base, early, "5m", ZERO, RISK, [])
    second = _simulate(base + [future], later, "5m", ZERO, RISK, [])
    assert first.trades[0].entry_price == second.trades[0].entry_price
    assert first.trades[0].entry_time == after.start


def test_missing_weekday_is_counted_and_not_filled():
    friday = MarketBar(
        start=datetime(2026, 1, 2, tzinfo=timezone.utc),
        interval="1D",
        open=D(10),
        high=D(11),
        low=D(9),
        close=D(10),
        volume=D(100),
        currency="USD",
        source="twelvedata",
    )
    wednesday = MarketBar(
        start=friday.start + timedelta(days=5),
        interval="1D",
        open=D(10),
        high=D(11),
        low=D(9),
        close=D(10),
        volume=D(100),
        currency="USD",
        source="twelvedata",
    )
    assert _gaps([friday, wednesday], "1D") == 2


def test_variants_are_not_ranked_by_the_best_return():
    bars = [_day(offset, "100", "101", "99", "100") for offset in range(40)]
    rows = compare_variants(bars, "breakout", timeframe="1D", costs=ZERO, risk=RISK)
    assert rows
    assert "best" not in rows[0]
    assert "overfit_risk" in rows[0]


def test_position_size_uses_the_risk_budget():
    bars = [
        _day(0, "100", "101", "99", "100"),
        _day(1, "100", "103", "99.5", "102"),
        _day(2, "102", "103", "101", "102"),
    ]
    trade = _simulate(bars, [Signal(0, "manual")], "1D", ZERO, replace_hold(1), []).trades[0]
    assert trade.shares == Decimal("50")
    assert trade.reason == "time_stop"
    assert trade.exit_price == Decimal("102")
    assert trade.pnl == Decimal("100")


def replace_hold(bars_held: int) -> RiskModel:
    return RiskModel(
        capital=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_fraction=Decimal("0.02"),
        target_r=Decimal("2"),
        max_hold_bars=bars_held,
    )


def _day(offset, open_, high, low, close) -> MarketBar:
    return MarketBar(
        start=datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(days=offset),
        interval="1D",
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(1000),
        currency="USD",
        source="twelvedata",
    )


def _intraday(minutes, open_, high, low, close) -> MarketBar:
    return MarketBar(
        start=START + timedelta(minutes=minutes),
        interval="5min",
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(1000),
        currency="USD",
        source="twelvedata",
    )
