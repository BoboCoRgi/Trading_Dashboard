from dataclasses import dataclass
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class ExposureLine:
    key: str
    weight: Decimal
    market_value_gbp: Decimal


def exposure_by(rows: list[tuple[str, Decimal, Decimal]]) -> list[ExposureLine]:
    """Sum (key, weight, market value) rows. Used for theme and account exposure."""
    totals: dict[str, list[Decimal]] = {}
    for key, weight, value in rows:
        bucket = totals.setdefault(key, [D(0), D(0)])
        bucket[0] += D(weight)
        bucket[1] += D(value)
    lines = [ExposureLine(key=key, weight=pair[0], market_value_gbp=pair[1]) for key, pair in totals.items()]
    lines.sort(key=lambda line: line.weight, reverse=True)
    return lines
