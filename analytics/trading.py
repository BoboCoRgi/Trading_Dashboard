"""Position size, execution P&L and trading-book statistics.

Prices come from the caller. This module does not read a broker and does not
invent a missing fill, fee or exchange rate.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from core.money import D, q8

LONG = "LONG"
SHORT = "SHORT"
BUY = "BUY"
SELL = "SELL"
STATUSES = ("DRAFT", "PLANNED", "ACTIVE", "CLOSED", "CANCELLED")
RATIO = Decimal("0.0001")
_OPEN = {LONG: BUY, SHORT: SELL}


@dataclass(frozen=True)
class SizeResult:
    entry_used: Decimal
    shares: Decimal
    risk_per_share: Decimal
    reward_per_share: Decimal | None
    risk_amount: Decimal
    risk_reward: Decimal | None
    potential_loss: Decimal
    potential_profit: Decimal | None
    required_capital: Decimal


@dataclass(frozen=True)
class Execution:
    action: str
    quantity: Decimal
    price: Decimal
    fees_native: Decimal = Decimal("0")
    currency: str = "USD"
    fx_rate_to_gbp: Decimal | None = None
    executed_at: datetime | None = None
    execution_id: str = ""


@dataclass(frozen=True)
class PositionResult:
    open_quantity: Decimal
    average_price: Decimal | None
    realised_native: Decimal | None
    fees_native: Decimal
    currency: str | None
    realised_gbp: Decimal | None
    unrealised_native: Decimal | None
    unrealised_gbp: Decimal | None
    unmatched_quantity: Decimal
    native_complete: bool
    gbp_complete: bool
    opened_at: datetime | None
    closed_at: datetime | None
    note: str


@dataclass(frozen=True)
class Allocation:
    broker_quantity: Decimal
    trading_quantity: Decimal
    core_quantity: Decimal


@dataclass(frozen=True)
class Score:
    strategy: str
    timeframe: str
    closed: bool
    realised_gbp: Decimal | None
    planned_rr: Decimal | None
    r_multiple: Decimal | None
    exit_at: datetime | None


@dataclass(frozen=True)
class Performance:
    closed_count: int
    open_count: int
    win_rate: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    average_r: Decimal | None
    profit_factor: Decimal | None
    net_realised_gbp: Decimal | None
    average_planned_rr: Decimal | None
    average_actual_r: Decimal | None
    max_drawdown_gbp: Decimal | None
    by_strategy: dict[str, dict]
    by_timeframe: dict[str, dict]
    sample_is_small: bool
    note: str


def q4(value: Decimal) -> Decimal:
    return D(value).quantize(RATIO, rounding=ROUND_HALF_UP)


def size_position(
    *,
    direction: str,
    stop: Decimal,
    entry: Decimal | None = None,
    entry_low: Decimal | None = None,
    entry_high: Decimal | None = None,
    target: Decimal | None = None,
    risk_amount: Decimal | None = None,
    risk_fraction: Decimal | None = None,
    capital: Decimal | None = None,
) -> SizeResult:
    """Size from the worst entry in a zone. Long uses the high. Short uses the low."""
    if direction not in {LONG, SHORT}:
        raise ValueError("direction must be LONG or SHORT")
    used = _entry(direction, entry, entry_low, entry_high)
    stop = D(stop)
    if direction == LONG:
        risk_per_share = used - stop
        reward = None if target is None else D(target) - used
    else:
        risk_per_share = stop - used
        reward = None if target is None else used - D(target)
    if risk_per_share <= 0:
        raise ValueError("the stop must sit beyond the entry")
    if reward is not None and reward <= 0:
        raise ValueError("the target must sit beyond the entry")
    amount = _risk_budget(risk_amount, risk_fraction, capital)
    shares = q8(amount / risk_per_share)
    if shares <= 0:
        raise ValueError("the risk budget does not buy a position")
    loss = q8(shares * risk_per_share)
    profit = None if reward is None else q8(shares * reward)
    ratio = None if reward is None else q4(reward / risk_per_share)
    return SizeResult(
        entry_used=used,
        shares=shares,
        risk_per_share=q8(risk_per_share),
        reward_per_share=None if reward is None else q8(reward),
        risk_amount=q8(amount),
        risk_reward=ratio,
        potential_loss=loss,
        potential_profit=profit,
        required_capital=q8(shares * used),
    )


def split_allocation(broker_quantity: Decimal, trading_quantity: Decimal) -> Allocation:
    held = D(broker_quantity)
    trading = D(trading_quantity)
    if held < 0:
        raise ValueError("broker quantity is missing")
    if trading < 0:
        raise ValueError("trading quantity cannot be negative")
    if trading > held:
        raise ValueError("trading shares cannot exceed the broker position")
    return Allocation(broker_quantity=held, trading_quantity=trading, core_quantity=held - trading)


def accept_execution(existing: list[Execution], incoming: Execution) -> list[Execution]:
    """A repeated broker execution id is ignored. A blank id is a new manual leg."""
    if incoming.execution_id and any(row.execution_id == incoming.execution_id for row in existing):
        return list(existing)
    return [*existing, incoming]


def position_result(fills: list[Execution], direction: str, *, mark: Decimal | None = None, mark_fx: Decimal | None = None) -> PositionResult:
    """Average-cost P&L from fills. Mixed currencies are left incomplete."""
    if direction not in {LONG, SHORT}:
        raise ValueError("direction must be LONG or SHORT")
    ordered = sorted(fills, key=_when)
    if not ordered:
        return PositionResult(
            open_quantity=Decimal("0"),
            average_price=None,
            realised_native=Decimal("0"),
            fees_native=Decimal("0"),
            currency=None,
            realised_gbp=None,
            unrealised_native=None,
            unrealised_gbp=None,
            unmatched_quantity=Decimal("0"),
            native_complete=True,
            gbp_complete=False,
            opened_at=None,
            closed_at=None,
            note="No executions are stored. P&L is not estimated from the plan.",
        )
    currencies = {row.currency for row in ordered}
    if len(currencies) != 1:
        return PositionResult(
            open_quantity=Decimal("0"),
            average_price=None,
            realised_native=None,
            fees_native=sum((D(row.fees_native) for row in ordered), Decimal("0")),
            currency=None,
            realised_gbp=None,
            unrealised_native=None,
            unrealised_gbp=None,
            unmatched_quantity=Decimal("0"),
            native_complete=False,
            gbp_complete=False,
            opened_at=ordered[0].executed_at,
            closed_at=None,
            note="Executions use more than one currency, so they are not combined.",
        )
    currency = ordered[0].currency
    opening = _OPEN[direction]
    quantity = Decimal("0")
    cost = Decimal("0")
    realised = Decimal("0")
    realised_gbp = Decimal("0")
    fees = Decimal("0")
    unmatched = Decimal("0")
    gbp_ok = True
    opened_at = None
    closed_at = None
    for row in ordered:
        fees += D(row.fees_native)
        if D(row.fees_native) != 0:
            fee_gbp, fee_ok = _to_gbp(D(row.fees_native), row.fx_rate_to_gbp)
            gbp_ok = gbp_ok and fee_ok
            if fee_ok and fee_gbp is not None:
                realised_gbp -= fee_gbp
        if row.action == opening:
            if opened_at is None:
                opened_at = row.executed_at
            quantity += D(row.quantity)
            cost += D(row.quantity) * D(row.price)
            continue
        if row.action not in {BUY, SELL}:
            gbp_ok = False
            continue
        close_qty = min(D(row.quantity), quantity)
        unmatched += D(row.quantity) - close_qty
        if close_qty <= 0 or quantity <= 0:
            continue
        average = cost / quantity
        move = (D(row.price) - average) if direction == LONG else (average - D(row.price))
        gain = close_qty * move
        realised += gain
        gain_gbp, gain_ok = _to_gbp(gain, row.fx_rate_to_gbp)
        gbp_ok = gbp_ok and gain_ok
        if gain_ok and gain_gbp is not None:
            realised_gbp += gain_gbp
        cost -= average * close_qty
        quantity -= close_qty
        if quantity == 0:
            closed_at = row.executed_at
            cost = Decimal("0")
    average_price = None if quantity == 0 else q8(cost / quantity)
    unrealised = None
    unrealised_gbp = None
    if quantity > 0 and mark is not None:
        move = (D(mark) - average_price) if direction == LONG else (average_price - D(mark))
        unrealised = q8(quantity * move)
        if mark_fx is not None:
            unrealised_gbp, _ = _to_gbp(unrealised, mark_fx)
    note = "Estimated from stored executions."
    if not gbp_ok:
        note = "Native P&L is estimated from fills. GBP is incomplete because an exchange rate is missing."
    if unmatched > 0:
        note = "A closing fill was larger than the open quantity. The extra shares are unmatched and were not given a P&L."
    return PositionResult(
        open_quantity=q8(quantity),
        average_price=None if average_price is None else q8(average_price),
        realised_native=q8(realised - fees),
        fees_native=q8(fees),
        currency=currency,
        realised_gbp=None if not gbp_ok else q8(realised_gbp),
        unrealised_native=unrealised,
        unrealised_gbp=None if unrealised_gbp is None else q8(unrealised_gbp),
        unmatched_quantity=q8(unmatched),
        native_complete=True,
        gbp_complete=gbp_ok,
        opened_at=opened_at,
        closed_at=closed_at if quantity == 0 else None,
        note=note,
    )


def trading_performance(scores: list[Score]) -> Performance:
    closed = [row for row in scores if row.closed]
    open_count = sum(1 for row in scores if not row.closed)
    money = [row for row in closed if row.realised_gbp is not None]
    wins = [D(row.realised_gbp) for row in money if D(row.realised_gbp) > 0]
    losses = [D(row.realised_gbp) for row in money if D(row.realised_gbp) < 0]
    rs = [D(row.r_multiple) for row in closed if row.r_multiple is not None]
    planned = [D(row.planned_rr) for row in closed if row.planned_rr is not None]
    net = None if not money else q8(sum((D(row.realised_gbp) for row in money), Decimal("0")))
    factor = None
    if wins and losses:
        factor = q4(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0"))))
    sample = len(closed)
    note = f"{sample} closed trades, {open_count} open."
    if sample < 5:
        note = f"Sample of {sample} closed trades. Too small to treat as evidence of an edge."
    if closed and len(money) < len(closed):
        note = f"{note} GBP totals skip trades that have no exchange rate."
    return Performance(
        closed_count=sample,
        open_count=open_count,
        win_rate=None if not money else q4(Decimal(len(wins)) / Decimal(len(money))),
        average_win=None if not wins else q8(sum(wins, Decimal("0")) / Decimal(len(wins))),
        average_loss=None if not losses else q8(sum(losses, Decimal("0")) / Decimal(len(losses))),
        average_r=None if not rs else q4(sum(rs, Decimal("0")) / Decimal(len(rs))),
        profit_factor=factor,
        net_realised_gbp=net,
        average_planned_rr=None if not planned else q4(sum(planned, Decimal("0")) / Decimal(len(planned))),
        average_actual_r=None if not rs else q4(sum(rs, Decimal("0")) / Decimal(len(rs))),
        max_drawdown_gbp=_drawdown(money),
        by_strategy=_groups(closed, "strategy"),
        by_timeframe=_groups(closed, "timeframe"),
        sample_is_small=sample < 5,
        note=note,
    )


def _entry(direction: str, entry: Decimal | None, low: Decimal | None, high: Decimal | None) -> Decimal:
    if entry is not None:
        return D(entry)
    if direction == LONG and high is not None:
        return D(high)
    if direction == SHORT and low is not None:
        return D(low)
    if low is not None:
        return D(low)
    if high is not None:
        return D(high)
    raise ValueError("an entry price or entry zone is required")


def _risk_budget(amount: Decimal | None, fraction: Decimal | None, capital: Decimal | None) -> Decimal:
    if amount is not None and fraction is not None:
        raise ValueError("use a risk amount or a percentage, not both")
    if amount is not None:
        budget = D(amount)
    elif fraction is not None and capital is not None:
        if D(fraction) <= 0 or D(fraction) > 1:
            raise ValueError("risk percentage must be between 0 and 100")
        budget = D(capital) * D(fraction)
    else:
        raise ValueError("a risk amount or a capital percentage is required")
    if budget <= 0:
        raise ValueError("risk must be positive")
    return budget


def _to_gbp(amount: Decimal, fx: Decimal | None) -> tuple[Decimal | None, bool]:
    if fx is None:
        return None, False
    return q8(D(amount) * D(fx)), True


def _drawdown(rows: list[Score]) -> Decimal | None:
    if not rows or any(row.realised_gbp is None for row in rows):
        return None
    ordered = sorted(rows, key=lambda row: row.exit_at or datetime.max.replace(tzinfo=timezone.utc))
    equity = Decimal("0")
    peak = Decimal("0")
    worst = Decimal("0")
    for row in ordered:
        equity += D(row.realised_gbp)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return q8(worst)


def _when(row: Execution) -> tuple[int, datetime]:
    if row.executed_at is None:
        return (1, datetime.max.replace(tzinfo=timezone.utc))
    return (0, row.executed_at)


def _groups(rows: list[Score], field: str) -> dict[str, dict]:
    grouped: dict[str, list[Score]] = {}
    for row in rows:
        grouped.setdefault(getattr(row, field) or "—", []).append(row)
    summary = {}
    for name, items in grouped.items():
        money = [D(item.realised_gbp) for item in items if item.realised_gbp is not None]
        wins = [value for value in money if value > 0]
        rs = [D(item.r_multiple) for item in items if item.r_multiple is not None]
        summary[name] = {
            "closed": len(items),
            "win_rate": None if not money else q4(Decimal(len(wins)) / Decimal(len(money))),
            "net_realised_gbp": None if not money else q8(sum(money, Decimal("0"))),
            "average_r": None if not rs else q4(sum(rs, Decimal("0")) / Decimal(len(rs))),
        }
    return summary
