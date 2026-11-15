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
NO_BOOK = "No open position is stored on the latest snapshot."


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
    prepared = []
    for position, instrument in rows:
        history = market.get_daily_bars(instrument.symbol)
        previous, current, as_of = last_session_move(history.bars)
        currency = instrument.currency or position.market_price_currency or ""
        fx = reporting_fx(currency, position.fx_rate_to_gbp)
        fills = _fills_today(session, instrument, chosen.origin, session_day, snapshot.snapshot_time, current, fx)
        signed = sum((_signed_fill(fill) for fill in fills), start=D(0))
        prepared.append(
            {
                "instrument_id": str(instrument.id),
                "symbol": instrument.symbol,
                "quantity": D(position.quantity) - signed,
                "previous": previous,
                "current": current,
                "as_of": as_of,
                "currency": currency,
                "fx": fx,
                "fills": fills,
            }
        )
    return attribute_positions(prepared)


def reporting_fx(currency: str, stored_rate: Decimal | None) -> Decimal | None:
    """GBP needs no conversion. Any other currency uses the rate stored on the position."""
    if (currency or "") == "GBP":
        return D(1)
    if stored_rate is None:
        return None
    rate = D(stored_rate)
    if rate <= 0:
        return None
    return rate


def last_session_move(bars) -> tuple[Decimal | None, Decimal | None, datetime | None]:
    """Previous completed daily close, latest stored close, and that bar's start.

    The broker snapshot price is not used. A weekend has no new bar, so the
    move is the last two stored sessions.
    """
    daily = [bar for bar in bars if getattr(bar, "interval", "1D") in {"1D", "1day"}]
    if len(daily) < 2:
        return None, None, None
    ordered = sorted(daily, key=lambda bar: bar.start)
    return D(ordered[-2].close), D(ordered[-1].close), ordered[-1].start


def attribute_positions(rows: list[dict]) -> dict:
    """Convert the names that have a close and an FX rate. Name the ones that do not."""
    openings: list[OpeningPosition] = []
    executions: list[IntradayExecution] = []
    gaps: list[str] = []
    as_of = None
    for row in rows:
        symbol = row["symbol"]
        quantity = D(row["quantity"])
        if quantity < 0:
            gaps.append(f"{symbol}: today's fills exceed the stored quantity")
            continue
        if quantity == 0 and not row.get("fills"):
            continue
        if row.get("current") is None or (quantity != 0 and row.get("previous") is None):
            gaps.append(f"{symbol}: no completed daily close")
            continue
        if row.get("fx") is None:
            gaps.append(f"{symbol}: no GBP rate for {row.get('currency') or 'its currency'}")
            continue
        if quantity != 0:
            openings.append(
                OpeningPosition(
                    instrument_id=row["instrument_id"],
                    symbol=symbol,
                    quantity=quantity,
                    previous_close=row["previous"],
                    current_price=row["current"],
                    currency=row["currency"],
                    fx_to_reporting=row["fx"],
                )
            )
        executions.extend(row.get("fills") or [])
        if row.get("as_of") is not None and (as_of is None or row["as_of"] > as_of):
            as_of = row["as_of"]
    if not openings and not executions:
        return _empty(_gap_reason(gaps) or NO_BOOK)
    result = calculate_daily_pnl_contribution(openings, executions)
    if not result["ready"]:
        return _empty(result.get("reason") or UNAVAILABLE_REASON)
    result["as_of"] = as_of
    result["price_kind"] = "eod_close"
    result["reason"] = _gap_reason(gaps)
    return result


def _gap_reason(gaps: list[str]) -> str | None:
    if not gaps:
        return None
    return "Today's P&L leaves out " + "; ".join(gaps) + "."


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


def _empty(reason: str = UNAVAILABLE_REASON) -> dict:
    return {"ready": False, "reason": reason, "total_gbp": None, "rows": []}
