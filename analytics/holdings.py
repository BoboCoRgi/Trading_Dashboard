"""Broker-independent position aggregation.

Account average prices are weighted by quantity. A plain average of the
account prices is not used. GBP cost and native broker averages stay separate.
"""

from dataclasses import dataclass
from decimal import Decimal

from core.money import D, q8


@dataclass(frozen=True)
class AccountLot:
    symbol: str
    company: str
    instrument_id: str
    account_id: str
    broker_label: str
    account_label: str
    quantity: Decimal
    price: Decimal | None
    price_currency: str
    market_value_gbp: Decimal | None
    cost_basis_gbp: Decimal | None
    average_cost_gbp: Decimal | None
    broker_average: Decimal | None
    broker_average_currency: str | None
    unrealised_gbp: Decimal | None
    unrealised_pct: Decimal | None
    weight: Decimal | None
    daily_change_pct: Decimal | None
    daily_pnl_gbp: Decimal | None


@dataclass(frozen=True)
class CostFigure:
    value: Decimal | None
    currency: str
    method: str


@dataclass(frozen=True)
class CombinedPosition:
    symbol: str
    company: str
    instrument_id: str
    quantity: Decimal
    price: Decimal | None
    price_currency: str
    price_note: str
    market_value_gbp: Decimal | None
    weight: Decimal | None
    unrealised_gbp: Decimal | None
    unrealised_pct: Decimal | None
    daily_change_pct: Decimal | None
    daily_pnl_gbp: Decimal | None
    gbp_cost: CostFigure
    native_cost: CostFigure
    lots: tuple[AccountLot, ...]


def _sum_known(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return sum((D(value) for value in values), start=D(0))


def _gbp_cost(lots: list[AccountLot]) -> CostFigure:
    open_lots = [lot for lot in lots if lot.quantity > 0]
    if not open_lots:
        return CostFigure(None, "GBP", "No open quantity.")
    if all(lot.cost_basis_gbp is not None for lot in open_lots):
        total_qty = sum((D(lot.quantity) for lot in open_lots), start=D(0))
        total_cost = sum((D(lot.cost_basis_gbp) for lot in open_lots), start=D(0))
        if total_qty == 0:
            return CostFigure(None, "GBP", "No open quantity.")
        return CostFigure(
            q8(total_cost / total_qty),
            "GBP",
            "Quantity-weighted. Total GBP cost basis divided by total quantity.",
        )
    if all(lot.average_cost_gbp is not None for lot in open_lots):
        total_qty = sum((D(lot.quantity) for lot in open_lots), start=D(0))
        weighted = sum((D(lot.quantity) * D(lot.average_cost_gbp) for lot in open_lots), start=D(0))
        return CostFigure(
            q8(weighted / total_qty),
            "GBP",
            "Quantity-weighted average of account GBP average costs. The account prices were not averaged equally.",
        )
    return CostFigure(
        None,
        "GBP",
        "Combined GBP cost is unavailable because at least one account has no cost basis.",
    )


def _native_cost(lots: list[AccountLot]) -> CostFigure:
    open_lots = [lot for lot in lots if lot.quantity > 0]
    currencies = {lot.broker_average_currency for lot in open_lots if lot.broker_average is not None}
    if any(lot.broker_average is None or not lot.broker_average_currency for lot in open_lots):
        return CostFigure(
            None,
            "",
            "Combined broker average is unavailable because at least one account has no broker average price.",
        )
    if len(currencies) != 1:
        return CostFigure(
            None,
            "",
            "Broker averages are in different currencies, so they were not combined.",
        )
    currency = next(iter(currencies))
    total_qty = sum((D(lot.quantity) for lot in open_lots), start=D(0))
    weighted = sum((D(lot.quantity) * D(lot.broker_average) for lot in open_lots), start=D(0))
    return CostFigure(
        q8(weighted / total_qty),
        currency or "",
        f"Quantity-weighted broker average in {currency}. This is not a GBP cost and not an equal average of the account prices.",
    )


def combine_lots(lots: list[AccountLot]) -> CombinedPosition | None:
    if not lots:
        return None
    symbol = lots[0].symbol
    if any(lot.symbol != symbol for lot in lots):
        raise ValueError("lots must already be grouped by symbol")
    quantity = sum((D(lot.quantity) for lot in lots), start=D(0))
    prices = {(lot.price, lot.price_currency) for lot in lots if lot.price is not None}
    if len(prices) == 1:
        price, price_currency = next(iter(prices))
        price_note = "Same price on every account in this snapshot."
    elif not prices:
        price, price_currency, price_note = None, "", "No snapshot price is stored."
    else:
        price, price_currency = None, ""
        price_note = "Account prices differ, so no single combined price is shown."
    values = [lot.market_value_gbp for lot in lots]
    weights = [lot.weight for lot in lots]
    unrealised = [lot.unrealised_gbp for lot in lots]
    daily_pnl = [lot.daily_pnl_gbp for lot in lots]
    daily_change = {lot.daily_change_pct for lot in lots}
    market_value = _sum_known(values)
    weight = _sum_known(weights)
    unrealised_gbp = _sum_known(unrealised)
    gbp_cost = _gbp_cost(lots)
    unrealised_pct = None
    if unrealised_gbp is not None and gbp_cost.value is not None and gbp_cost.value != 0 and quantity > 0:
        total_cost = gbp_cost.value * quantity
        unrealised_pct = q8(unrealised_gbp / total_cost)
    daily_pnl_gbp = _sum_known(daily_pnl)
    if len(daily_change) == 1:
        change = next(iter(daily_change))
    else:
        change = None
    return CombinedPosition(
        symbol=symbol,
        company=lots[0].company,
        instrument_id=lots[0].instrument_id,
        quantity=q8(quantity),
        price=None if price is None else D(price),
        price_currency=price_currency,
        price_note=price_note,
        market_value_gbp=None if market_value is None else q8(market_value),
        weight=None if weight is None else q8(weight),
        unrealised_gbp=None if unrealised_gbp is None else q8(unrealised_gbp),
        unrealised_pct=unrealised_pct,
        daily_change_pct=None if change is None else D(change),
        daily_pnl_gbp=None if daily_pnl_gbp is None else q8(daily_pnl_gbp),
        gbp_cost=gbp_cost,
        native_cost=_native_cost(lots),
        lots=tuple(lots),
    )
