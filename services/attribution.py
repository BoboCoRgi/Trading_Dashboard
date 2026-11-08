"""Today's market P&L for the current book.

Snapshot prices are not used. A missing quote or previous close stays missing.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.attribution import IntradayExecution, OpeningPosition, calculate_daily_pnl_contribution
from connectors.market_data.provider import StoredMarketData
from core.enums import TradeAction
from core.money import D
from db.models import Instrument, PositionSnapshot, Trade
from services.portfolio_source import PortfolioDataProvider, portfolio_provider

UNAVAILABLE_REASON = "Today's P&L attribution will appear once current prices and previous closes are available."


def load_today_attribution(session: Session, provider: PortfolioDataProvider | None = None) -> dict:
    """Build attribution inputs from holdings and stored market data.

    The latest snapshot supplies which names are held and the current quantity.
    It does not supply the previous close or the mark used in the formula.
    """
    chosen = provider or portfolio_provider()
    snapshot = chosen.latest_snapshot(session)
    if snapshot is None:
        return _empty()
    rows = session.execute(
        select(PositionSnapshot, Instrument)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .where(
            PositionSnapshot.portfolio_snapshot_id == snapshot.id,
            PositionSnapshot.data_origin == chosen.origin,
        )
    ).all()
    if not rows:
        return _empty()
    market = StoredMarketData(session, chosen.origin)
    session_day = datetime.now(timezone.utc).date()
    openings: list[OpeningPosition] = []
    executions: list[IntradayExecution] = []
    missing = False
    for position, instrument in rows:
        mark = market.get_quote(instrument.symbol)
        history = market.get_daily_bars(instrument.symbol)
        previous = _previous_close(history.bars, session_day)
        current = mark.price
        currency = instrument.currency or position.market_price_currency or ""
        fx = D(1) if currency == "GBP" else None
        fills = _fills_today(session, instrument, chosen.origin, session_day, snapshot.snapshot_time, current, fx)
        signed = sum((_signed_fill(fill) for fill in fills), start=D(0))
        opening_qty = D(position.quantity) - signed
        if opening_qty < 0 or current is None or fx is None or (opening_qty != 0 and previous is None):
            missing = True
        if opening_qty != 0:
            openings.append(
                OpeningPosition(
                    instrument_id=str(instrument.id),
                    symbol=instrument.symbol,
                    quantity=opening_qty,
                    previous_close=previous,
                    current_price=current,
                    currency=currency,
                    fx_to_reporting=fx,
                )
            )
        executions.extend(fills)
    if missing:
        return _empty()
    result = calculate_daily_pnl_contribution(openings, executions)
    if not result["ready"]:
        return _empty()
    return result


def _fills_today(session, instrument, origin, session_day, as_of, current: Decimal | None, fx: Decimal | None):
    rows = session.scalars(
        select(Trade).where(Trade.instrument_id == instrument.id, Trade.data_origin == origin)
    ).all()
    fills = []
    for trade in rows:
        executed = trade.executed_at
        if executed.tzinfo is None or as_of.tzinfo is None or executed > as_of:
            continue
        day = executed.astimezone(timezone.utc).date() if executed.tzinfo else executed.date()
        if day != session_day or trade.action not in {TradeAction.BUY, TradeAction.SELL}:
            continue
        fills.append(
            IntradayExecution(
                instrument_id=str(instrument.id),
                symbol=instrument.symbol,
                side=trade.action.value,
                quantity=D(trade.quantity),
                execution_price=D(trade.fill_price),
                current_price=current,
                currency=trade.trade_currency,
                fx_to_reporting=fx,
            )
        )
    return fills


def _signed_fill(fill: IntradayExecution) -> Decimal:
    quantity = D(fill.quantity)
    return quantity if fill.side == "BUY" else -quantity


def _previous_close(bars, session_day) -> Decimal | None:
    prior = []
    for bar in bars:
        start = bar.start
        day = start.astimezone(timezone.utc).date() if start.tzinfo else start.date()
        if day < session_day:
            prior.append(bar)
    if not prior:
        return None
    return D(prior[-1].close)


def _empty() -> dict:
    return {"ready": False, "reason": UNAVAILABLE_REASON, "total_gbp": None, "rows": []}
