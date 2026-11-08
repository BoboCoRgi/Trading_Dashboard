from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from core.money import D, q8


@dataclass
class Fill:
    account_id: str
    instrument_id: str
    action: str
    quantity: Decimal
    fill_price: Decimal
    fx_rate_to_gbp: Decimal
    fees_native: Decimal
    fx_fee_native: Decimal
    executed_at: datetime


@dataclass
class Mark:
    instrument_id: str
    price: Decimal
    fx_rate_to_gbp: Decimal
    currency: str = "USD"


@dataclass
class PositionState:
    account_id: str
    instrument_id: str
    quantity: Decimal = field(default_factory=lambda: D(0))
    cost_basis_gbp: Decimal = field(default_factory=lambda: D(0))
    realised_pnl_gbp: Decimal = field(default_factory=lambda: D(0))
    market_price: Decimal | None = None
    market_fx: Decimal | None = None
    market_currency: str = "USD"
    market_value_gbp: Decimal = field(default_factory=lambda: D(0))
    unrealised_pnl_gbp: Decimal = field(default_factory=lambda: D(0))
    unrealised_pnl_pct: Decimal = field(default_factory=lambda: D(0))
    portfolio_weight: Decimal = field(default_factory=lambda: D(0))

    @property
    def average_cost_gbp(self) -> Decimal:
        if self.quantity == 0:
            return D(0)
        return self.cost_basis_gbp / self.quantity


@dataclass
class PortfolioState:
    positions: list[PositionState]
    cash_gbp: Decimal
    invested_value_gbp: Decimal
    total_value_gbp: Decimal
    realised_pnl_gbp: Decimal


def _gbp(quantity: Decimal, price: Decimal, fx: Decimal) -> Decimal:
    return quantity * price * fx


def weights_by_instrument(state: PortfolioState, labels: dict[str, str]) -> list[tuple[str, Decimal]]:
    """Merge account-level weights so concentration is by instrument."""
    totals: dict[str, Decimal] = {}
    for position in state.positions:
        label = labels.get(position.instrument_id, position.instrument_id)
        totals[label] = totals.get(label, D(0)) + position.portfolio_weight
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def cash_impact(fill: Fill) -> Decimal:
    """Signed GBP cash change. Buys are negative."""
    notional = _gbp(fill.quantity, fill.fill_price, fill.fx_rate_to_gbp)
    fees = (fill.fees_native + fill.fx_fee_native) * fill.fx_rate_to_gbp
    if fill.action == "BUY":
        return -(notional + fees)
    if fill.action == "SELL":
        return notional - fees
    raise ValueError(f"unsupported trade action {fill.action}")


def replay(fills: list[Fill], marks: dict[str, Mark], opening_cash_gbp: Decimal) -> PortfolioState:
    """Replay fills in time order and mark open positions.

    Buy fees and FX fees are added to cost basis. Sell fees reduce proceeds.
    A sell uses average cost. Quantity cannot go short.
    """
    ordered = sorted(fills, key=lambda fill: (fill.executed_at, fill.action != "BUY"))
    books: dict[tuple[str, str], PositionState] = {}
    cash = D(opening_cash_gbp)
    for fill in ordered:
        key = (fill.account_id, fill.instrument_id)
        position = books.setdefault(key, PositionState(account_id=fill.account_id, instrument_id=fill.instrument_id))
        fees_gbp = (fill.fees_native + fill.fx_fee_native) * fill.fx_rate_to_gbp
        notional = _gbp(fill.quantity, fill.fill_price, fill.fx_rate_to_gbp)
        if fill.action == "BUY":
            position.quantity += fill.quantity
            position.cost_basis_gbp += notional + fees_gbp
        elif fill.action == "SELL":
            if fill.quantity > position.quantity:
                raise ValueError("sell quantity exceeds the open position")
            average = position.cost_basis_gbp / position.quantity
            cost_removed = average * fill.quantity
            proceeds = notional - fees_gbp
            position.realised_pnl_gbp += proceeds - cost_removed
            position.quantity -= fill.quantity
            position.cost_basis_gbp -= cost_removed
            if position.quantity == 0:
                position.cost_basis_gbp = D(0)
        else:
            raise ValueError(f"unsupported trade action {fill.action}")
        cash += cash_impact(fill)

    open_positions: list[PositionState] = []
    invested = D(0)
    realised = D(0)
    for position in books.values():
        realised += position.realised_pnl_gbp
        if position.quantity == 0:
            continue
        mark = marks.get(position.instrument_id)
        if mark is None:
            raise ValueError(f"missing mark for {position.instrument_id}")
        position.market_price = mark.price
        position.market_fx = mark.fx_rate_to_gbp
        position.market_currency = mark.currency
        position.market_value_gbp = q8(_gbp(position.quantity, mark.price, mark.fx_rate_to_gbp))
        position.cost_basis_gbp = q8(position.cost_basis_gbp)
        position.unrealised_pnl_gbp = q8(position.market_value_gbp - position.cost_basis_gbp)
        if position.cost_basis_gbp == 0:
            position.unrealised_pnl_pct = D(0)
        else:
            position.unrealised_pnl_pct = position.unrealised_pnl_gbp / position.cost_basis_gbp
        invested += position.market_value_gbp
        open_positions.append(position)

    cash = q8(cash)
    invested = q8(invested)
    total = cash + invested
    for position in open_positions:
        position.portfolio_weight = position.market_value_gbp / total if total else D(0)
    open_positions.sort(key=lambda position: position.market_value_gbp, reverse=True)
    return PortfolioState(
        positions=open_positions,
        cash_gbp=cash,
        invested_value_gbp=invested,
        total_value_gbp=total,
        realised_pnl_gbp=q8(realised),
    )
