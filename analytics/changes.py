from dataclasses import dataclass
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class PortfolioChange:
    code: str
    message: str
    evidence: str


@dataclass(frozen=True)
class HeldLine:
    symbol: str
    weight: Decimal
    market_value_gbp: Decimal


def describe_changes(
    previous: list[HeldLine] | None,
    current: list[HeldLine],
    previous_total: Decimal | None,
    current_total: Decimal,
    weight_threshold: Decimal = D("0.01"),
) -> list[PortfolioChange]:
    """Deterministic description of what changed between two snapshots."""
    changes: list[PortfolioChange] = []
    if previous_total is not None:
        delta = current_total - previous_total
        changes.append(
            PortfolioChange(
                code="TOTAL_VALUE",
                message="Portfolio value changed versus the previous snapshot.",
                evidence=f"previous={previous_total} current={current_total} delta_gbp={delta}",
            )
        )
    previous_map = {line.symbol: line for line in previous} if previous else {}
    current_map = {line.symbol: line for line in current}
    for symbol, line in current_map.items():
        prior = previous_map.get(symbol)
        if prior is None:
            changes.append(
                PortfolioChange(
                    code="NEW_POSITION",
                    message=f"{symbol} is present and was not in the previous snapshot.",
                    evidence=f"weight={line.weight} value_gbp={line.market_value_gbp}",
                )
            )
            continue
        delta_weight = line.weight - prior.weight
        if abs(delta_weight) >= weight_threshold:
            changes.append(
                PortfolioChange(
                    code="WEIGHT_CHANGE",
                    message=f"{symbol} weight moved by {delta_weight}.",
                    evidence=f"previous_weight={prior.weight} current_weight={line.weight}",
                )
            )
    for symbol, prior in previous_map.items():
        if symbol not in current_map:
            changes.append(
                PortfolioChange(
                    code="CLOSED_POSITION",
                    message=f"{symbol} was in the previous snapshot and is now flat.",
                    evidence=f"previous_weight={prior.weight}",
                )
            )
    return changes
