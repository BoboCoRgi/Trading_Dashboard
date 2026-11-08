from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from core.enums import OutcomeHorizon, OutcomeStatus
from core.money import D

HORIZONS: tuple[tuple[OutcomeHorizon, int], ...] = (
    (OutcomeHorizon.D1, 1),
    (OutcomeHorizon.D5, 5),
    (OutcomeHorizon.D20, 20),
    (OutcomeHorizon.D60, 60),
)


@dataclass(frozen=True)
class OutcomeMarker:
    horizon: OutcomeHorizon
    trading_days: int
    status: OutcomeStatus
    evaluated_on: date | None
    start_price: Decimal | None
    end_price: Decimal | None
    instrument_return_pct: Decimal | None
    notes: str


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def price_return_markers(
    decision_day: date,
    closes: dict[date, Decimal],
) -> list[OutcomeMarker]:
    """Instrument close-to-close return after N trading days.

    This is a marker so the journal can later be evaluated. It is not
    benchmark-relative performance, alpha, or a trading score.
    """
    trading_days = sorted(day for day in closes if day >= decision_day)
    markers: list[OutcomeMarker] = []
    if not trading_days:
        for horizon, length in HORIZONS:
            markers.append(
                OutcomeMarker(
                    horizon=horizon,
                    trading_days=length,
                    status=OutcomeStatus.PENDING,
                    evaluated_on=None,
                    start_price=None,
                    end_price=None,
                    instrument_return_pct=None,
                    notes="No close on or after the decision date.",
                )
            )
        return markers

    start_day = trading_days[0]
    start_price = D(closes[start_day])
    for horizon, length in HORIZONS:
        target_index = trading_days.index(start_day) + length
        if target_index >= len(trading_days):
            markers.append(
                OutcomeMarker(
                    horizon=horizon,
                    trading_days=length,
                    status=OutcomeStatus.PENDING,
                    evaluated_on=None,
                    start_price=start_price,
                    end_price=None,
                    instrument_return_pct=None,
                    notes="Not enough later closes to mark this horizon.",
                )
            )
            continue
        end_day = trading_days[target_index]
        end_price = D(closes[end_day])
        instrument_return = (end_price - start_price) / start_price if start_price else D(0)
        markers.append(
            OutcomeMarker(
                horizon=horizon,
                trading_days=length,
                status=OutcomeStatus.EVALUATED,
                evaluated_on=end_day,
                start_price=start_price,
                end_price=end_price,
                instrument_return_pct=instrument_return,
                notes="Close-to-close instrument return. Not a portfolio or benchmark result.",
            )
        )
    return markers


def decision_day_from(value: datetime) -> date:
    return _as_date(value)
