"""Holdings read model.

Positions come from the selected portfolio snapshot. Trades come from the
canonical trade table for the same data origin. Corporate actions are not
execution markers. No broker API is called from here.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.events import rebase_to_start
from analytics.holdings import AccountLot, combine_lots
from analytics.market_stats import (
    MarketBar,
    cumulative_intraday_volume,
    daily_relative_volume,
    intraday_relative_volume,
    intraday_vwap,
    simple_moving_average,
)
from connectors.market_data.provider import StoredBenchmarks, StoredMarketData
from core.enums import Broker, DataOrigin, TradeAction
from core.money import D, q8
from db.models import BrokerAccount, Decision, Instrument, PositionSnapshot, Thesis, Trade
from services.ledger import snapshot_before
from services.portfolio_source import PortfolioDataProvider, portfolio_provider

_BROKER_LABEL = {Broker.TRADING212: "Trading 212", Broker.IBKR: "IBKR"}
_WINDOWS = {"1W": 5, "1M": 21, "3M": 63, "1Y": 252}


def holdings_report(session: Session, provider: PortfolioDataProvider | None = None) -> dict:
    chosen = provider or portfolio_provider()
    snapshot = chosen.latest_snapshot(session)
    status = chosen.status(session, snapshot=snapshot, loaded=True)
    if snapshot is None or not status["ready"]:
        return {"ready": False, "source": status, "message": status["message"], "command": status["command"]}
    previous = snapshot_before(session, snapshot.snapshot_time, origin=chosen.origin)
    previous_rows = _position_map(session, previous.id, chosen.origin) if previous else {}
    current_rows = _position_rows(session, snapshot.id, chosen.origin)
    lots = [_lot(position, instrument, account, previous_rows.get((account.id, instrument.id))) for position, instrument, account in current_rows]
    grouped: dict[str, list[AccountLot]] = {}
    for lot in lots:
        grouped.setdefault(lot.symbol, []).append(lot)
    combined = [combine_lots(rows) for rows in grouped.values()]
    combined = [row for row in combined if row is not None]
    combined.sort(key=lambda row: row.market_value_gbp or D(0), reverse=True)
    return {
        "ready": True,
        "source": status,
        "snapshot_id": str(snapshot.id),
        "as_of": snapshot.snapshot_time,
        "total_gbp": snapshot.total_portfolio_value_gbp,
        "cash_gbp": snapshot.cash_gbp,
        "combined": combined,
        "lots": lots,
        "origin": chosen.origin.value,
    }


def stock_detail(session: Session, symbol: str, provider: PortfolioDataProvider | None = None) -> dict:
    report = holdings_report(session, provider)
    if not report["ready"]:
        return report
    chosen = provider or portfolio_provider()
    match = next((row for row in report["combined"] if row.symbol == symbol), None)
    if match is None:
        return {"ready": False, "source": report["source"], "message": f"{symbol} is not in the current snapshot.", "command": None}
    market = StoredMarketData(session, chosen.origin)
    benchmarks = StoredBenchmarks(session, chosen.origin, market)
    daily = market.get_daily_bars(symbol)
    intraday = market.get_intraday_bars(symbol)
    volume = market.get_volume_history(symbol)
    return {
        "ready": True,
        "source": report["source"],
        "as_of": report["as_of"],
        "position": match,
        "daily": daily,
        "intraday": intraday,
        "trades": _execution_markers(session, match.instrument_id, chosen.origin),
        "daily_rvol": _daily_rvol(volume.bars),
        "intraday_rvol": _intraday_rvol(intraday.bars),
        "vwap": _vwap_note(intraday.bars),
        "relative": _relative(symbol, daily.bars, benchmarks),
        "thesis": _thesis(session, match.instrument_id, chosen.origin),
        "freshness": _freshness(report["as_of"], match, daily.note, intraday.note),
    }


def bars_for_range(bars: list[MarketBar], window: str) -> list[MarketBar]:
    if window == "INTRADAY":
        return [bar for bar in bars if bar.interval != "1D"]
    count = _WINDOWS.get(window)
    daily = [bar for bar in bars if bar.interval == "1D"]
    if count is None:
        return daily
    return daily[-count:]


def moving_averages(bars: list[MarketBar]) -> dict[str, list[Decimal | None]]:
    closes = [bar.close for bar in bars if bar.interval == "1D"]
    return {str(window): simple_moving_average(closes, window) for window in (20, 50, 200)}


def _position_rows(session: Session, snapshot_id, origin: DataOrigin):
    return session.execute(
        select(PositionSnapshot, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == PositionSnapshot.account_id)
        .where(
            PositionSnapshot.portfolio_snapshot_id == snapshot_id,
            PositionSnapshot.data_origin == origin,
        )
    ).all()


def _position_map(session: Session, snapshot_id, origin: DataOrigin) -> dict:
    rows = _position_rows(session, snapshot_id, origin)
    return {(account.id, instrument.id): position for position, instrument, account in rows}


def _lot(position: PositionSnapshot, instrument: Instrument, account: BrokerAccount, previous: PositionSnapshot | None) -> AccountLot:
    change, contribution = _daily_move(position, previous)
    return AccountLot(
        symbol=instrument.symbol,
        company=instrument.company_name,
        instrument_id=str(instrument.id),
        account_id=str(account.id),
        broker_label=_BROKER_LABEL.get(account.broker, account.broker.value),
        account_label=account.display_name,
        quantity=D(position.quantity),
        price=None if position.market_price is None else D(position.market_price),
        price_currency=position.market_price_currency or "",
        market_value_gbp=None if position.market_value_gbp is None else D(position.market_value_gbp),
        cost_basis_gbp=None if position.cost_basis_gbp is None else D(position.cost_basis_gbp),
        average_cost_gbp=None if position.average_cost_gbp is None else D(position.average_cost_gbp),
        broker_average=None if position.broker_average_price is None else D(position.broker_average_price),
        broker_average_currency=position.broker_average_currency,
        unrealised_gbp=None if position.unrealised_pnl_gbp is None else D(position.unrealised_pnl_gbp),
        unrealised_pct=None if position.unrealised_pnl_pct is None else D(position.unrealised_pnl_pct),
        weight=None if position.portfolio_weight is None else D(position.portfolio_weight),
        daily_change_pct=change,
        daily_pnl_gbp=contribution,
    )


def _daily_move(position: PositionSnapshot, previous: PositionSnapshot | None) -> tuple[Decimal | None, Decimal | None]:
    if previous is None or previous.market_price is None or position.market_price is None:
        return None, None
    if previous.fx_rate_to_gbp is None or position.fx_rate_to_gbp is None:
        return None, None
    if D(previous.market_price) == 0:
        return None, None
    change = q8((D(position.market_price) - D(previous.market_price)) / D(previous.market_price))
    quantity = min(D(previous.quantity), D(position.quantity))
    contribution = q8(
        quantity
        * (
            D(position.market_price) * D(position.fx_rate_to_gbp)
            - D(previous.market_price) * D(previous.fx_rate_to_gbp)
        )
    )
    return change, contribution


def _execution_markers(session: Session, instrument_id: str, origin: DataOrigin) -> list[dict]:
    from uuid import UUID

    rows = session.execute(
        select(Trade, BrokerAccount)
        .join(BrokerAccount, BrokerAccount.id == Trade.account_id)
        .where(Trade.instrument_id == UUID(instrument_id), Trade.data_origin == origin)
        .order_by(Trade.executed_at)
    ).all()
    markers = []
    for trade, account in rows:
        if trade.action not in {TradeAction.BUY, TradeAction.SELL}:
            continue
        decision = session.scalar(select(Decision).where(Decision.linked_trade_id == trade.id))
        markers.append(
            {
                "time": trade.executed_at,
                "action": trade.action.value,
                "quantity": D(trade.quantity),
                "price": D(trade.fill_price),
                "currency": trade.trade_currency,
                "broker": _BROKER_LABEL.get(account.broker, account.broker.value),
                "account": account.display_name,
                "value_gbp": D(trade.gross_value_gbp),
                "decision": None if decision is None else decision.decision_type.value,
            }
        )
    return markers


def _daily_rvol(bars: list[MarketBar]) -> dict:
    definition = "Daily relative volume is this session's full volume divided by the average full-session volume of the previous 20 trading days."
    volumes = [bar.volume for bar in bars if bar.interval == "1D" and bar.volume is not None]
    if len(volumes) < 21:
        return {"value": None, "note": definition + " Fewer than 21 stored daily volume bars, so it is not calculated."}
    value = daily_relative_volume(session_volume=D(volumes[-1]), prior_session_volumes=[D(volume) for volume in volumes[-21:-1]])
    return {"value": value, "note": definition}


def _intraday_rvol(bars: list[MarketBar]) -> dict:
    definition = (
        "Intraday relative volume compares cumulative volume up to a clock with the average cumulative volume "
        "up to that same clock over the previous 20 sessions. It is not compared with a full-day average."
    )
    intraday = [bar for bar in bars if bar.interval != "1D"]
    if not intraday:
        return {"value": None, "as_of": None, "note": definition + " Intraday bars are not stored, so it is unavailable."}
    latest = max(bar.start for bar in intraday)
    today = cumulative_intraday_volume(intraday, as_of=latest)
    if today is None:
        return {"value": None, "as_of": latest, "note": definition + " Today's cumulative volume could not be formed without guessing."}
    prior = []
    latest_utc = latest.astimezone(timezone.utc)
    for offset in range(1, 60):
        if len(prior) == 20:
            break
        value = cumulative_intraday_volume(intraday, as_of=latest_utc - timedelta(days=offset))
        if value is not None:
            prior.append(value)
    value = intraday_relative_volume(cumulative_to_clock=today, prior_cumulative_to_same_clock=prior)
    if value is None:
        return {
            "value": None,
            "as_of": latest,
            "note": definition + " Fewer than 20 earlier sessions have cumulative volume at this clock.",
        }
    return {"value": value, "as_of": latest, "note": definition}


def _vwap_note(bars: list[MarketBar]) -> dict:
    intraday = [bar for bar in bars if bar.interval != "1D"]
    if not intraday:
        return {"value": None, "note": "VWAP needs intraday high, low, close, and bar volume. It is not estimated from daily closes."}
    latest_day = max(bar.start for bar in intraday).date()
    session_bars = [bar for bar in intraday if bar.start.date() == latest_day]
    value = intraday_vwap(session_bars)
    if value is None:
        return {"value": None, "note": "Intraday bars are stored, but high, low, or bar volume is missing, so VWAP was not approximated."}
    return {"value": value, "note": "Intraday VWAP: typical price (high + low + close) / 3, weighted by each bar's volume."}


def _relative(symbol: str, stock_bars: list[MarketBar], benchmarks: StoredBenchmarks) -> dict:
    market = benchmarks.get_market_benchmark(symbol)
    sector = benchmarks.get_sector_benchmark(symbol)
    notes = [note for note in (market.note, sector.note) if note]
    stock = {bar.start.date(): bar.close for bar in stock_bars if bar.interval == "1D"}
    available = []
    for label, bars in ((market.label, market.bars), (sector.label, sector.bars)):
        if not label or not bars:
            continue
        other = {bar.start.date(): bar.close for bar in bars if bar.interval == "1D"}
        overlap = sorted(set(stock) & set(other))
        if len(overlap) >= 2:
            available.append((label, other, overlap))
    if not available:
        return {"ready": False, "series": [], "note": " ".join(notes)}
    common = set(available[0][2])
    for _, _, overlap in available[1:]:
        common &= set(overlap)
    days = sorted(common)
    used = available
    limited = ""
    if len(days) < 2:
        days = available[0][2]
        used = [available[0]]
        limited = " Only one benchmark shares enough dates with the stock to rebase together."
    stock_rebased = rebase_to_start([stock[day] for day in days])
    if stock_rebased is None:
        return {"ready": False, "series": [], "note": " ".join(notes)}
    series = [
        {
            "name": symbol,
            "points": [{"date": day.isoformat(), "value": float(value)} for day, value in zip(days, stock_rebased, strict=True)],
        }
    ]
    for label, other, _overlap in used:
        other_rebased = rebase_to_start([other[day] for day in days])
        if other_rebased is None:
            continue
        series.append(
            {
                "name": label,
                "points": [{"date": day.isoformat(), "value": float(value)} for day, value in zip(days, other_rebased, strict=True)],
            }
        )
    if len(series) < 2:
        return {"ready": False, "series": [], "note": " ".join(notes)}
    return {
        "ready": True,
        "series": series,
        "note": (
            "Every line is rebased to 100 on the first date shared by the stock and the benchmarks shown. "
            "This compares paths. It does not explain why the paths differ."
            + limited
        ),
    }


def _thesis(session: Session, instrument_id: str, origin: DataOrigin) -> dict | None:
    from uuid import UUID

    row = session.scalar(
        select(Thesis)
        .where(Thesis.instrument_id == UUID(instrument_id), Thesis.data_origin == origin)
        .order_by(Thesis.updated_at.desc())
    )
    if row is None:
        return None
    return {
        "title": row.title,
        "summary": row.thesis_summary,
        "bull": row.bull_case,
        "base": row.base_case,
        "bear": row.bear_case,
        "catalysts": row.catalysts,
        "invalidation": row.invalidation_conditions,
        "horizon": row.time_horizon,
        "status": row.status.value,
    }


def _freshness(as_of: datetime, position: CombinedPosition, daily_note: str, intraday_note: str) -> dict:
    moment = as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if as_of.tzinfo else str(as_of)
    price = "—" if position.price is None else f"{position.price} {position.price_currency}"
    return {
        "snapshot": moment,
        "snapshot_price": price,
        "price_delay": "Snapshot price from the broker position feed stored at ingestion. Not a separate real-time quote.",
        "history": daily_note,
        "intraday": intraday_note,
        "coverage": "Symbols in the selected data mode only. No exchange calendar is applied.",
    }


def chart_cost_lines(position, chart_currency: str) -> list[dict]:
    """Lines drawn only when the average is already in the chart currency."""
    lines = []
    for lot in position.lots:
        if lot.broker_average is None or lot.broker_average_currency != chart_currency:
            continue
        lines.append(
            {
                "name": f"{lot.broker_label} broker average",
                "price": lot.broker_average,
                "method": f"Broker average price in {lot.broker_average_currency} for this account.",
            }
        )
    native = position.native_cost
    if len(position.lots) > 1 and native.value is not None and native.currency == chart_currency:
        lines.append({"name": "Combined broker average", "price": native.value, "method": native.method})
    return lines
