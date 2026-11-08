from dataclasses import dataclass
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class Concentration:
    top1: Decimal
    top3: Decimal
    top5: Decimal
    largest_key: str | None


def concentration(weights: list[tuple[str, Decimal]]) -> Concentration:
    """Largest, top-3 and top-5 weights.

    `weights` are fractions of total portfolio value. Callers that have the
    same instrument in more than one account should merge those weights first.
    """
    if not weights:
        return Concentration(top1=D(0), top3=D(0), top5=D(0), largest_key=None)
    ordered = sorted(weights, key=lambda item: item[1], reverse=True)
    values = [D(weight) for _, weight in ordered]

    def _sum(limit: int) -> Decimal:
        return sum(values[:limit], start=D(0))

    return Concentration(
        top1=values[0],
        top3=_sum(3),
        top5=_sum(5),
        largest_key=ordered[0][0],
    )
