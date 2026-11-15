"""Position size, allocation, fills and trading statistics. No database and no broker."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from analytics.trading import (
    Execution,
    Score,
    accept_execution,
    position_result,
    size_position,
    split_allocation,
    trading_performance,
)

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_long_and_short_size_use_risk_and_the_worst_entry():
    long = size_position(direction="LONG", entry_low=Decimal("10"), entry_high=Decimal("12"), stop=Decimal("8"), target=Decimal("16"), risk_amount=Decimal("100"))
    assert long.entry_used == Decimal("12")
    assert long.shares == Decimal("25")
    assert long.risk_reward == Decimal("1")
    assert long.potential_loss == Decimal("100")
    assert long.potential_profit == Decimal("100")
    assert long.required_capital == Decimal("300")
    short = size_position(direction="SHORT", entry=Decimal("10"), stop=Decimal("12"), target=Decimal("7"), risk_fraction=Decimal("0.01"), capital=Decimal("10000"))
    assert short.shares == Decimal("50")
    assert short.risk_reward == Decimal("1.5")
    assert short.potential_profit == Decimal("150")
    assert short.required_capital == Decimal("500")


def test_a_stop_on_the_wrong_side_is_rejected():
    with pytest.raises(ValueError):
        size_position(direction="LONG", entry=Decimal("10"), stop=Decimal("11"), risk_amount=Decimal("50"))


def test_allocation_cannot_exceed_the_broker_position():
    split = split_allocation(Decimal("10"), Decimal("4"))
    assert split.core_quantity == Decimal("6")
    assert split.trading_quantity == Decimal("4")
    with pytest.raises(ValueError):
        split_allocation(Decimal("10"), Decimal("11"))


def test_partial_fills_scale_in_and_partial_close():
    scaled = position_result(
        [
            _fill(BUY, "10", "100", days=0),
            _fill(BUY, "10", "120", days=1),
            _fill(SELL, "4", "130", days=2),
        ],
        "LONG",
        mark=Decimal("90"),
    )
    assert scaled.open_quantity == Decimal("16")
    assert scaled.average_price == Decimal("110")
    assert scaled.realised_native == Decimal("80")
    assert scaled.unrealised_native == Decimal("-320")
    assert scaled.closed_at is None


def test_a_larger_closing_fill_does_not_invent_pnl_for_the_extra_shares():
    result = position_result([_fill(BUY, "2", "10"), _fill(SELL, "5", "12", days=1)], "LONG")
    assert result.open_quantity == Decimal("0")
    assert result.realised_native == Decimal("4")
    assert result.unmatched_quantity == Decimal("3")
    assert "unmatched" in result.note


def test_short_cover_and_fees():
    covered = position_result([_fill(SELL, "10", "50"), _fill(BUY, "10", "40", days=1, fees="2")], "SHORT")
    assert covered.realised_native == Decimal("98")
    assert covered.open_quantity == Decimal("0")
    assert covered.fees_native == Decimal("2")


def test_mixed_currencies_and_missing_fx_stay_incomplete():
    mixed = position_result([_fill(BUY, "1", "10", currency="USD"), _fill(SELL, "1", "12", days=1, currency="EUR")], "LONG")
    assert mixed.native_complete is False
    assert mixed.realised_native is None
    missing = position_result([_fill(BUY, "1", "10", fx=None), _fill(SELL, "1", "12", days=1, fx=None)], "LONG", mark=Decimal("12"))
    assert missing.realised_native == Decimal("2")
    assert missing.realised_gbp is None
    assert missing.gbp_complete is False
    converted = position_result(
        [_fill(BUY, "1", "10", fx="0.8"), _fill(SELL, "1", "12", days=1, fx="0.8")],
        "LONG",
    )
    assert converted.realised_gbp == Decimal("1.60")


def test_the_same_execution_id_is_not_recorded_twice():
    first = _fill(BUY, "1", "10")
    first = Execution(**{**first.__dict__, "execution_id": "t212-1"})
    stored = accept_execution([], first)
    again = accept_execution(stored, first)
    assert len(again) == 1
    manual = accept_execution(again, _fill(BUY, "1", "11", days=1))
    assert len(manual) == 2


def test_performance_separates_open_trades_and_refuses_a_small_sample():
    scores = [
        Score("Breakout", "1D", True, Decimal("10"), Decimal("2"), Decimal("1"), NOW),
        Score("Breakout", "1D", True, Decimal("-30"), Decimal("2"), Decimal("-3"), NOW + timedelta(days=1)),
        Score("Pullback", "1H", True, Decimal("5"), Decimal("1"), Decimal("0.5"), NOW + timedelta(days=2)),
        Score("Pullback", "1H", False, None, Decimal("1"), None, None),
    ]
    result = trading_performance(scores)
    assert result.closed_count == 3
    assert result.open_count == 1
    assert result.net_realised_gbp == Decimal("-15")
    assert result.max_drawdown_gbp == Decimal("-30")
    assert result.profit_factor == Decimal("0.5")
    assert result.sample_is_small is True
    assert "Too small" in result.note
    assert result.by_strategy["Breakout"]["closed"] == 2
    assert result.by_timeframe["1H"]["closed"] == 1


def test_empty_broker_history_does_not_create_a_result():
    empty = position_result([], "LONG")
    assert empty.realised_native == Decimal("0")
    assert empty.gbp_complete is False
    assert "not estimated from the plan" in empty.note
    stats = trading_performance([])
    assert stats.closed_count == 0
    assert stats.net_realised_gbp is None
    assert stats.max_drawdown_gbp is None


BUY = "BUY"
SELL = "SELL"


def _fill(action, quantity, price, *, days=0, fees="0", currency="USD", fx="1"):
    return Execution(
        action=action,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees_native=Decimal(fees),
        currency=currency,
        fx_rate_to_gbp=None if fx is None else Decimal(fx),
        executed_at=NOW + timedelta(days=days),
    )
