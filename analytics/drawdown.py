from dataclasses import dataclass
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class DrawdownReport:
    series: tuple[Decimal, ...]
    current: Decimal
    maximum: Decimal
    ath: Decimal
    ath_index: int
    current_ath: Decimal


def drawdown_series(values: list[Decimal] | tuple[Decimal, ...]) -> DrawdownReport:
    """Drawdown from a chronological value series.

    Each point is (value - running ATH) / running ATH. The result is zero at
    a peak and negative below it. `maximum` is the most negative point.
    """
    if not values:
        raise ValueError("drawdown requires at least one portfolio value")
    points: list[Decimal] = []
    ath = D(values[0])
    ath_index = 0
    maximum = D(0)
    for index, raw in enumerate(values):
        value = D(raw)
        if value > ath:
            ath = value
            ath_index = index
        if ath == 0:
            raise ValueError("portfolio ATH cannot be zero")
        drawdown = (value - ath) / ath
        points.append(drawdown)
        if drawdown < maximum:
            maximum = drawdown
    return DrawdownReport(
        series=tuple(points),
        current=points[-1],
        maximum=maximum,
        ath=ath,
        ath_index=ath_index,
        current_ath=ath,
    )
