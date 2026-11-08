from dataclasses import dataclass
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class RiskAlert:
    code: str
    severity: str
    message: str
    evidence: str


def risk_alerts(
    *,
    largest_symbol: str | None,
    top1: Decimal,
    top1_limit: Decimal,
    drawdown: Decimal,
    drawdown_limit: Decimal,
    cash_weight: Decimal,
    cash_limit: Decimal,
    worst_single_name: tuple[str, Decimal] | None,
    impact_limit: Decimal,
) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    if top1 >= top1_limit and largest_symbol:
        alerts.append(
            RiskAlert(
                code="CONCENTRATION",
                severity="BREACH",
                message=f"{largest_symbol} is {top1} of the portfolio, at or above the {top1_limit} concentration threshold.",
                evidence=f"top1={top1} threshold={top1_limit}",
            )
        )
    if drawdown <= drawdown_limit:
        alerts.append(
            RiskAlert(
                code="DRAWDOWN",
                severity="BREACH",
                message=f"Drawdown from the portfolio high is {drawdown}, through the {drawdown_limit} threshold.",
                evidence=f"drawdown={drawdown} threshold={drawdown_limit}",
            )
        )
    if cash_weight < cash_limit:
        alerts.append(
            RiskAlert(
                code="CASH",
                severity="WATCH",
                message=f"Cash is {cash_weight} of the portfolio, below the {cash_limit} floor.",
                evidence=f"cash_weight={cash_weight} threshold={cash_limit}",
            )
        )
    if worst_single_name is not None and worst_single_name[1] <= impact_limit:
        symbol, impact = worst_single_name
        alerts.append(
            RiskAlert(
                code="SCENARIO",
                severity="WATCH",
                message=f"A move already calculated for {symbol} would change the portfolio by {impact}.",
                evidence=f"impact={impact} threshold={impact_limit}",
            )
        )
    if not alerts:
        alerts.append(
            RiskAlert(
                code="CLEAR",
                severity="INFO",
                message="No configured risk threshold is breached on the latest snapshot.",
                evidence="thresholds checked: concentration, drawdown, cash, single-name scenario",
            )
        )
    return alerts
