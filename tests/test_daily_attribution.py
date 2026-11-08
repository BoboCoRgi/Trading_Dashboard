"""Daily market P&L does not depend on portfolio snapshots."""

import inspect
from decimal import Decimal

from analytics.attribution import (
    IntradayExecution,
    OpeningPosition,
    calculate_daily_pnl_contribution,
)
from analytics.drawdown import drawdown_series
from analytics.performance import daily_pnl
from app.dashboard.series import contribution_rows
from core.money import D
from db.models import PortfolioSnapshot, PositionSnapshot


def _open(symbol, quantity, previous, current, fx=Decimal("1"), currency="GBP") -> OpeningPosition:
    return OpeningPosition(
        instrument_id=symbol,
        symbol=symbol,
        quantity=D(quantity),
        previous_close=None if previous is None else D(previous),
        current_price=None if current is None else D(current),
        currency=currency,
        fx_to_reporting=None if fx is None else D(fx),
    )


def _fill(symbol, side, quantity, execution, current, fx=Decimal("1"), currency="GBP") -> IntradayExecution:
    return IntradayExecution(
        instrument_id=symbol,
        symbol=symbol,
        side=side,
        quantity=D(quantity),
        execution_price=D(execution),
        current_price=D(current),
        currency=currency,
        fx_to_reporting=None if fx is None else D(fx),
    )


def test_attribution_does_not_require_snapshot_history():
    signature = inspect.signature(calculate_daily_pnl_contribution)
    assert "snapshot" not in signature.parameters
    result = calculate_daily_pnl_contribution([_open("MU", 10, 100, 110)], [])
    assert result["ready"] is True
    assert result["rows"][0]["contribution_gbp"] == D(100)


def test_beginning_of_day_contribution_is_quantity_times_price_gap():
    result = calculate_daily_pnl_contribution(
        [_open("MU", "10", "100", "110", fx="0.80", currency="USD")],
        [],
    )
    row = result["rows"][0]
    assert row["contribution_native"] == D(100)
    assert row["contribution_gbp"] == D(80)
    assert row["day_change_pct"] == D("0.10000000")


def test_intraday_buy_uses_execution_price():
    result = calculate_daily_pnl_contribution([], [_fill("RKLB", "BUY", "4", "90", "100")])
    assert result["rows"][0]["contribution_gbp"] == D(40)


def test_intraday_sell_uses_negative_quantity():
    result = calculate_daily_pnl_contribution(
        [_open("MU", "10", "100", "105")],
        [_fill("MU", "SELL", "4", "110", "105")],
    )
    assert result["rows"][0]["contribution_native"] == D(70)


def test_buying_shares_is_not_profit_by_itself():
    result = calculate_daily_pnl_contribution([], [_fill("NOK", "BUY", "100", "50", "50")])
    assert result["ready"] is True
    assert result["rows"][0]["contribution_gbp"] == D(0)


def test_missing_market_data_is_unavailable():
    result = calculate_daily_pnl_contribution([_open("MU", "10", None, "110")], [])
    assert result["ready"] is False
    assert result["rows"] == []
    assert "market price data" in result["reason"]


def test_today_page_drops_snapshot_sections():
    from app.dashboard import event_panel
    from app.dashboard.views import today

    source = inspect.getsource(today.render)
    assert "Portfolio value" not in source
    assert "How far am I from the high?" not in source
    assert "two snapshots" not in source
    assert "portfolio_value_chart" not in source
    assert "drawdown_chart" not in source
    assert "What's driving my P&L today?" in source
    panel = inspect.getsource(event_panel.render_event_dive)
    assert "two snapshots" not in panel


def test_snapshot_storage_and_existing_analytics_remain():
    assert PortfolioSnapshot.__tablename__ == "portfolio_snapshot"
    assert PositionSnapshot.__tablename__ == "position_snapshot"
    assert callable(contribution_rows)
    assert callable(drawdown_series)
    report = drawdown_series([D(100), D(80)])
    assert report.current == Decimal("-0.2")
    assert daily_pnl(D(110), D(100))[0] == D(10)
