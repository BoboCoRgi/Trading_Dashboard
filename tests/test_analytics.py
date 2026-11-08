from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from analytics.alerts import risk_alerts
from analytics.changes import HeldLine, describe_changes
from analytics.concentration import concentration
from analytics.drawdown import drawdown_series
from analytics.outcomes import price_return_markers
from analytics.portfolio import Fill, Mark, cash_impact, replay
from analytics.scenarios import ShockPosition, position_impact, standard_stress
from core.enums import OutcomeHorizon, OutcomeStatus
from core.money import D


def test_drawdown_from_a_peak():
    report = drawdown_series([D(100), D(120), D(90), D(110)])
    assert report.ath == D(120)
    assert report.ath_index == 1
    assert report.series[0] == D(0)
    assert report.series[1] == D(0)
    assert report.series[2] == Decimal("-0.25")
    assert report.current == (D(110) - D(120)) / D(120)
    assert report.maximum == Decimal("-0.25")


def test_concentration_merges_callers_responsibility():
    result = concentration([("RKLB", D("0.32")), ("META", D("0.16")), ("MU", D("0.11")), ("NOK", D("0.04"))])
    assert result.largest_key == "RKLB"
    assert result.top1 == D("0.32")
    assert result.top3 == D("0.59")
    assert result.top5 == D("0.63")


def test_position_impact_is_weight_times_shock():
    assert position_impact(D("0.25"), D("-0.20")) == D("-0.05")


def test_average_cost_fees_and_cash():
    when = datetime(2026, 2, 10, tzinfo=timezone.utc)
    buy = Fill("a", "i", "BUY", D(10), D(100), D("0.5"), D(2), D(0), when)
    sell = Fill("a", "i", "SELL", D(4), D(110), D("0.5"), D(1), D(0), when.replace(day=11))
    state = replay([buy, sell], {"i": Mark("i", D(110), D("0.5"))}, D(1000))
    position = state.positions[0]
    assert position.quantity == D(6)
    assert position.cost_basis_gbp == D("300.6")
    assert position.average_cost_gbp == D("50.1")
    assert position.realised_pnl_gbp == D("19.1")
    assert position.market_value_gbp == D(330)
    assert position.unrealised_pnl_gbp == D("29.4")
    assert state.cash_gbp == D("718.5")
    assert state.total_value_gbp == state.cash_gbp + state.invested_value_gbp
    assert cash_impact(buy) == D(-501)


def test_sell_cannot_exceed_position():
    when = datetime(2026, 2, 10, tzinfo=timezone.utc)
    buy = Fill("a", "i", "BUY", D(1), D(10), D(1), D(0), D(0), when)
    sell = Fill("a", "i", "SELL", D(2), D(10), D(1), D(0), D(0), when)
    with pytest.raises(ValueError):
        replay([buy, sell], {"i": Mark("i", D(10), D(1))}, D(100))


def test_weights_sum_with_cash():
    when = datetime(2026, 2, 10, tzinfo=timezone.utc)
    fills = [
        Fill("a1", "rklb", "BUY", D(2), D(50), D(1), D(0), D(0), when),
        Fill("a2", "rklb", "BUY", D(2), D(50), D(1), D(0), D(0), when),
    ]
    marks = {"rklb": Mark("rklb", D(50), D(1))}
    state = replay(fills, marks, D(100))
    assert sum((position.portfolio_weight for position in state.positions), start=D(0)) + (
        state.cash_gbp / state.total_value_gbp
    ) == D(1)


def test_equity_shock_leaves_cash_out_of_the_hit():
    positions = [ShockPosition("RKLB", D("0.40"), "Space"), ShockPosition("MU", D("0.10"), "Semiconductors")]
    results = {item.name: item for item in standard_stress(positions)}
    nasdaq = results["Nasdaq -10% (beta = 1)"]
    assert nasdaq.portfolio_impact == D("-0.05")
    assert "beta" in nasdaq.assumption.lower() or "Beta" in nasdaq.assumption
    semi = results["Semiconductor theme -20%"]
    assert semi.portfolio_impact == D("-0.02")
    rklb = results["RKLB -30%"]
    assert rklb.portfolio_impact == D("-0.12")


def test_price_return_marker_pending_when_the_horizon_is_not_finished():
    closes = {date(2026, 9, 24): D(10), date(2026, 9, 25): D(11)}
    markers = price_return_markers(date(2026, 9, 25), closes)
    by_horizon = {marker.horizon: marker for marker in markers}
    assert by_horizon[OutcomeHorizon.D1].status is OutcomeStatus.PENDING
    assert by_horizon[OutcomeHorizon.D1].start_price == D(11)


def test_price_return_marker_uses_trading_days_not_a_guess():
    closes = {
        date(2026, 1, 5): D(10),
        date(2026, 1, 6): D(12),
        date(2026, 1, 7): D(8),
    }
    markers = price_return_markers(date(2026, 1, 5), closes)
    first = next(marker for marker in markers if marker.horizon is OutcomeHorizon.D1)
    assert first.status is OutcomeStatus.EVALUATED
    assert first.end_price == D(12)
    assert first.instrument_return_pct == D("0.2")


def test_changes_detect_weight_and_closed_names():
    changes = describe_changes(
        [HeldLine("RKLB", D("0.20"), D(20)), HeldLine("NOK", D("0.05"), D(5))],
        [HeldLine("RKLB", D("0.32"), D(32))],
        D(100),
        D(110),
    )
    codes = {change.code for change in changes}
    assert "WEIGHT_CHANGE" in codes
    assert "CLOSED_POSITION" in codes


def test_risk_alert_fires_on_concentration():
    alerts = risk_alerts(
        largest_symbol="RKLB",
        top1=D("0.31"),
        top1_limit=D("0.25"),
        drawdown=D("-0.02"),
        drawdown_limit=D("-0.10"),
        cash_weight=D("0.12"),
        cash_limit=D("0.05"),
        worst_single_name=("RKLB", D("-0.06")),
        impact_limit=D("-0.05"),
    )
    assert any(alert.code == "CONCENTRATION" for alert in alerts)
    assert any(alert.code == "SCENARIO" for alert in alerts)
