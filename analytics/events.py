"""Deterministic move detection. No news and no benchmark are inferred."""

from dataclasses import dataclass
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class MoveInput:
    daily_return: Decimal
    prior_returns: tuple[Decimal, ...]
    weight: Decimal
    portfolio_value_gbp: Decimal


@dataclass(frozen=True)
class MoveAssessment:
    triggers: tuple[str, ...]
    abnormality: Decimal | None
    portfolio_impact_pct: Decimal
    portfolio_impact_gbp: Decimal
    priority: str


def zscore(current: Decimal, history: tuple[Decimal, ...] | list[Decimal], *, minimum: int = 20) -> Decimal | None:
    rows = [D(value) for value in history]
    if len(rows) < minimum:
        return None
    count = D(len(rows))
    mean = sum(rows, D(0)) / count
    variance = sum((value - mean) ** 2 for value in rows) / count
    if variance == 0:
        return None
    return (D(current) - mean) / variance.sqrt()


def assess_move(
    move: MoveInput,
    *,
    abs_threshold: Decimal,
    impact_threshold: Decimal,
    z_threshold: Decimal,
    critical_impact: Decimal,
    z_minimum: int = 20,
) -> MoveAssessment | None:
    daily = D(move.daily_return)
    weight = D(move.weight)
    impact_pct = weight * daily
    impact_gbp = impact_pct * D(move.portfolio_value_gbp)
    score = zscore(daily, move.prior_returns, minimum=z_minimum)
    triggers: list[str] = []
    if abs(daily) >= D(abs_threshold):
        triggers.append("ABSOLUTE_MOVE")
    if abs(impact_pct) >= D(impact_threshold):
        triggers.append("PORTFOLIO_IMPACT")
    if score is not None and abs(score) >= D(z_threshold):
        triggers.append("ABNORMAL_MOVE")
    if not triggers:
        return None
    if abs(impact_pct) >= D(critical_impact):
        level = "CRITICAL"
    elif "PORTFOLIO_IMPACT" in triggers or (score is not None and abs(score) >= D(z_threshold) and weight >= D("0.10")):
        level = "HIGH"
    else:
        level = "WATCH"
    return MoveAssessment(tuple(triggers), score, impact_pct, impact_gbp, level)


def rebase_to_start(prices: list[Decimal] | tuple[Decimal, ...]) -> list[Decimal] | None:
    """Rebase a stored price path to 100. Missing prices are not filled in."""
    rows = [D(price) for price in prices]
    if len(rows) < 2 or rows[0] == 0:
        return None
    start = rows[0]
    return [price / start * D(100) for price in rows]


def portfolio_fit(weight: Decimal, theme_weight: Decimal, cash_weight: Decimal, cash_floor: Decimal) -> tuple[str, str]:
    if D(theme_weight) >= D("0.25"):
        return "LOW", "The theme is already a large share of the portfolio."
    if D(weight) >= D("0.15"):
        return "LOW", "This name is already a large holding."
    if D(cash_weight) < D(cash_floor):
        return "LOW", "Cash is below the configured floor."
    if D(weight) == 0:
        return "HIGH", "Not currently held, and the theme is not already dominant."
    return "MEDIUM", "A smaller existing holding. Any add is still a separate decision."
