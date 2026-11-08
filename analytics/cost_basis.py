"""Reconstructed average cost from canonical trades and corporate actions.

Methodology
-----------
This is weighted average cost in the trade's native currency. It is the same
rule the mock ledger uses for GBP: a buy adds price times quantity plus fees
to remaining cost, and a sell removes the current average cost times the
quantity sold. Trading 212 tax amounts are negative cash flows, so the
absolute fee is added. A sell fee does not stay in the remaining cost.

A corporate action, including a stock split, changes quantity and leaves the
remaining cost unchanged. Split fill prices are not purchases or sales, and
an intermediate quantity of zero between the two legs of one split does not
wipe the cost. A sell that closes the position does.

The result is labelled reconstructed cost basis. Trading 212 ``averagePricePaid``
is broker cost basis. They are stored and reported separately. This module
does not adjust either figure to force them to match, and it does not assume
the broker uses FIFO.

GBP cost is filled in only when every contributing trade has an FX rate to
GBP. Missing historical FX is left unknown.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class CostEvent:
    at: datetime
    kind: str
    quantity: Decimal
    price: Decimal | None = None
    fees_native: Decimal = D(0)
    currency: str = ""
    fx_rate_to_gbp: Decimal | None = None


@dataclass(frozen=True)
class CostState:
    quantity: Decimal
    cost_native: Decimal
    average_native: Decimal | None
    currency: str
    cost_gbp: Decimal | None
    average_gbp: Decimal | None
    gbp_complete: bool


def reconstruct_cost(events: list[CostEvent]) -> CostState:
    """Replay one instrument. Kinds are BUY, SELL, and CORPORATE."""
    ordered = sorted(events, key=lambda event: (event.at, _kind_order(event.kind)))
    quantity = D(0)
    cost_native = D(0)
    cost_gbp = D(0)
    gbp_complete = True
    currency = ""
    for event in ordered:
        if event.kind == "CORPORATE":
            quantity += D(event.quantity)
            continue
        if event.kind not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported cost event {event.kind}")
        if event.currency and currency and event.currency != currency:
            raise ValueError("mixed trade currencies")
        currency = event.currency or currency
        if event.fx_rate_to_gbp is None:
            gbp_complete = False
        if event.kind == "BUY":
            if event.price is None:
                raise ValueError("buy had no price")
            quantity += D(event.quantity)
            fee = abs(D(event.fees_native))
            cost_native += D(event.quantity) * D(event.price) + fee
            if event.fx_rate_to_gbp is not None:
                cost_gbp += (D(event.quantity) * D(event.price) + fee) * D(event.fx_rate_to_gbp)
        else:
            if quantity <= 0 or D(event.quantity) > quantity:
                raise ValueError("sell quantity exceeds the open position")
            average = cost_native / quantity
            cost_native -= average * D(event.quantity)
            if gbp_complete and quantity != 0:
                cost_gbp -= (cost_gbp / quantity) * D(event.quantity)
            quantity -= D(event.quantity)
            if quantity == 0:
                cost_native = D(0)
                cost_gbp = D(0)
    if quantity == 0:
        cost_native = D(0)
        cost_gbp = D(0)
    average_native = cost_native / quantity if quantity else None
    average_gbp = cost_gbp / quantity if quantity and gbp_complete else None
    return CostState(
        quantity=quantity,
        cost_native=cost_native,
        average_native=average_native,
        currency=currency,
        cost_gbp=cost_gbp if gbp_complete else None,
        average_gbp=average_gbp,
        gbp_complete=gbp_complete,
    )


def compare_average(reconstructed: Decimal | None, broker: Decimal | None) -> str:
    """PASS, WARN, or UNAVAILABLE. A missing broker price is not a pass."""
    if reconstructed is None or broker is None:
        return "UNAVAILABLE"
    difference = abs(D(reconstructed) - D(broker))
    tolerance = max(D("0.01"), abs(D(broker)) * D("0.001"))
    if difference <= tolerance:
        return "PASS"
    return "WARN"


def overall_cost_status(statuses: list[str]) -> str:
    if not statuses:
        return "UNAVAILABLE"
    if "WARN" in statuses:
        return "WARN"
    if "UNAVAILABLE" in statuses:
        return "UNAVAILABLE"
    return "PASS"


def _kind_order(kind: str) -> int:
    return {"BUY": 0, "CORPORATE": 1, "SELL": 2}.get(kind, 9)
