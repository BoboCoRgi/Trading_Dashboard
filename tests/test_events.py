from decimal import Decimal

from analytics.events import MoveInput, assess_move, portfolio_fit, rebase_to_start, zscore
from analytics.screens import ScreenInput, screen
from core.money import D


def test_quiet_day_is_not_an_event():
    history = tuple(D("0.010") if index % 2 == 0 else D("0.012") for index in range(30))
    score = zscore(D("0.011"), history)
    assert score is not None and abs(score) < 2
    assert (
        assess_move(
            MoveInput(D("0.011"), history, D("0.10"), D("100000")),
            abs_threshold=D("0.05"),
            impact_threshold=D("0.005"),
            z_threshold=D("2"),
            critical_impact=D("0.02"),
        )
        is None
    )


def test_large_weight_makes_a_smaller_move_matter():
    history = tuple(D("0.002") for _ in range(25))
    quiet = assess_move(
        MoveInput(D("-0.03"), history, D("0.002"), D("100000")),
        abs_threshold=D("0.05"),
        impact_threshold=D("0.005"),
        z_threshold=D("2"),
        critical_impact=D("0.02"),
    )
    heavy = assess_move(
        MoveInput(D("-0.03"), history, D("0.36"), D("100000")),
        abs_threshold=D("0.05"),
        impact_threshold=D("0.005"),
        z_threshold=D("2"),
        critical_impact=D("0.02"),
    )
    assert quiet is None or "PORTFOLIO_IMPACT" not in quiet.triggers
    assert heavy is not None
    assert "PORTFOLIO_IMPACT" in heavy.triggers
    assert heavy.priority in {"HIGH", "CRITICAL"}
    assert heavy.portfolio_impact_pct == Decimal("-0.0108")


def test_abnormal_move_uses_history_not_a_fixed_percent():
    history = tuple(D("0.001") if index % 2 == 0 else D("-0.001") for index in range(30))
    assessment = assess_move(
        MoveInput(D("-0.04"), history, D("0.01"), D("100000")),
        abs_threshold=D("0.05"),
        impact_threshold=D("0.02"),
        z_threshold=D("2"),
        critical_impact=D("0.05"),
    )
    assert assessment is not None
    assert "ABNORMAL_MOVE" in assessment.triggers
    assert assessment.abnormality is not None
    assert abs(assessment.abnormality) > 2


def test_zero_variance_history_is_not_called_abnormal():
    history = tuple(D("-0.01") for _ in range(30))
    assert zscore(D("-0.01"), history) is None


def test_screen_flags_a_drawdown_without_calling_it_cheap():
    result = screen(
        ScreenInput("MU", D("-0.02"), D("-0.12"), D("-0.01"), None, True),
        drawdown_limit=D("-0.08"),
        peer_gap=D("-0.03"),
        z_threshold=D("2"),
    )
    assert result.opportunity_type == "THESIS_INTACT_RESET"
    assert "cheap" not in result.hits[0].reason.lower()
    assert "undervalued" not in result.hits[0].reason.lower()


def test_rebase_uses_only_the_supplied_prices():
    rebased = rebase_to_start([D("50"), D("40")])
    assert rebased == [D("100"), D("80")]
    assert rebase_to_start([D("10")]) is None


def test_portfolio_fit_is_separate_from_the_signal():
    level, reason = portfolio_fit(D("0.02"), D("0.40"), D("0.10"), D("0.05"))
    assert level == "LOW"
    assert "theme" in reason.lower()
