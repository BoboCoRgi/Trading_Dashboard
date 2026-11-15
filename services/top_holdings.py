"""Top holdings and the stock-detail series built from cached bars.

Portfolio weight comes from the current snapshot. Prices come from the market-data
service. A missing chart does not invent a price from the snapshot.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.chart_ranges import MINI_RANGE
from analytics.market_stats import (
    MarketBar,
    daily_relative_volume,
    simple_moving_average,
    vwap_series,
)
from analytics.support_resistance import PriceZone, support_resistance_zones
from analytics.timeline import (
    TimelineMarker,
    bars_covering,
    decision_marker,
    dedupe_markers,
    stored_event_marker,
    trade_marker,
)
from connectors.market_data.bars import PriceBar
from core.enums import Broker, DataOrigin, TradeAction
from core.money import D, q8
from db.models import BrokerAccount, Decision, MarketEvent, Trade

_BROKER_LABELS = {Broker.TRADING212: "Trading 212", Broker.IBKR: "IBKR"}
from services.market_data import SeriesResult

_CARD_MESSAGE = "Price history temporarily unavailable."


@dataclass
class HoldingCard:
    instrument_id: str
    symbol: str
    company: str
    weight: Decimal | None
    latest_price: Decimal | None
    price_currency: str
    range_return: Decimal | None
    average_cost: Decimal | None
    average_cost_currency: str
    unrealised_gbp: Decimal | None
    bars: list[PriceBar]
    markers: list[dict]
    source: str
    data_type: str
    updated_at: datetime | None
    message: str | None


def select_top_holdings(positions: list, *, limit: int = 5) -> list:
    """Current holdings, largest portfolio weight first. Fewer than five stays fewer than five."""
    open_positions = [position for position in positions if position.quantity > 0]
    ranked = sorted(open_positions, key=lambda position: (position.weight is None, -(position.weight or D(0)), position.symbol))
    return ranked[:limit]


def build_holding_cards(positions: list, load_one) -> list[HoldingCard]:
    cards = []
    for position in select_top_holdings(positions):
        try:
            cards.append(load_one(position))
        except Exception:
            cards.append(unavailable_card(position, _CARD_MESSAGE))
    return cards


def unavailable_card(position, message: str) -> HoldingCard:
    cost, currency = displayed_average_cost(position)
    return HoldingCard(
        instrument_id=str(position.instrument_id),
        symbol=position.symbol,
        company=position.company,
        weight=position.weight,
        latest_price=None,
        price_currency=position.price_currency or "",
        range_return=None,
        average_cost=cost,
        average_cost_currency=currency,
        unrealised_gbp=position.unrealised_gbp,
        bars=[],
        markers=[],
        source="",
        data_type="UNKNOWN",
        updated_at=None,
        message=message,
    )


def card_from_series(position, series: SeriesResult, markers: list[TimelineMarker]) -> HoldingCard:
    if series.status != "ready" or not series.bars:
        failed = unavailable_card(position, series.message or _CARD_MESSAGE)
        return failed
    currency = series.bars[-1].currency or position.price_currency
    placed = _placed(markers, series.bars, currency)
    cost, cost_currency = displayed_average_cost(position)
    return HoldingCard(
        instrument_id=str(position.instrument_id),
        symbol=position.symbol,
        company=position.company,
        weight=position.weight,
        latest_price=series.bars[-1].close,
        price_currency=currency,
        range_return=range_return(series.bars),
        average_cost=cost,
        average_cost_currency=cost_currency,
        unrealised_gbp=position.unrealised_gbp,
        bars=series.bars,
        markers=placed,
        source=series.source,
        data_type=series.data_type,
        updated_at=series.updated_at,
        message=None,
    )


def displayed_average_cost(position) -> tuple[Decimal | None, str]:
    native = position.native_cost
    if native.value is not None and native.currency:
        return native.value, native.currency
    if position.gbp_cost.value is not None:
        return position.gbp_cost.value, "GBP"
    return None, ""


def chart_average_cost(position, chart_currency: str) -> Decimal | None:
    """A line on the price axis only when the average is already in that currency."""
    native = position.native_cost
    if native.value is not None and native.currency == chart_currency:
        return native.value
    if chart_currency == "GBP" and position.gbp_cost.value is not None:
        return position.gbp_cost.value
    return None


def range_return(bars: list[PriceBar]) -> Decimal | None:
    if len(bars) < 2 or bars[0].close == 0:
        return None
    return q8((D(bars[-1].close) - D(bars[0].close)) / D(bars[0].close))


def day_change(bars: list[PriceBar], interval: str) -> Decimal | None:
    if interval == "1D" and len(bars) >= 2 and bars[-2].close != 0:
        return q8((D(bars[-1].close) - D(bars[-2].close)) / D(bars[-2].close))
    if interval != "1D" and bars and bars[-1].timestamp.tzinfo is not None:
        day = bars[-1].timestamp.astimezone(bars[-1].timestamp.tzinfo).date()
        session = [bar for bar in bars if bar.timestamp.astimezone(bar.timestamp.tzinfo).date() == day]
        if len(session) >= 2 and session[0].close != 0:
            return q8((D(session[-1].close) - D(session[0].close)) / D(session[0].close))
    return None


def moving_average_series(bars: list[PriceBar]) -> dict[int, list[Decimal | None]]:
    closes = [bar.close for bar in bars]
    return {window: simple_moving_average(closes, window) for window in (20, 50, 200)}


def volume_average_series(bars: list[PriceBar]) -> list[Decimal | None]:
    if not bars or any(bar.volume is None for bar in bars):
        return [None] * len(bars)
    return simple_moving_average([D(bar.volume) for bar in bars], 20)


def vwap_for_chart(bars: list[PriceBar]) -> list[Decimal | None]:
    return vwap_series([_market_bar(bar) for bar in bars])


def zones_for_chart(bars: list[PriceBar]) -> list[PriceZone]:
    return support_resistance_zones([_market_bar(bar) for bar in bars])


def daily_rvol(bars: list[PriceBar]) -> Decimal | None:
    volumes = [D(bar.volume) for bar in bars if bar.interval == "1D" and bar.volume is not None]
    if len(volumes) < 21:
        return None
    return daily_relative_volume(session_volume=volumes[-1], prior_session_volumes=volumes[-21:-1])


def load_timeline(session: Session, instrument_id: str, origin: DataOrigin) -> list[TimelineMarker]:
    instrument = UUID(str(instrument_id))
    markers: list[TimelineMarker] = []
    trades = session.execute(
        select(Trade, BrokerAccount.broker)
        .join(BrokerAccount, BrokerAccount.id == Trade.account_id)
        .where(Trade.instrument_id == instrument, Trade.data_origin == origin)
        .order_by(Trade.executed_at)
    ).all()
    for trade, broker in trades:
        if trade.action not in {TradeAction.BUY, TradeAction.SELL}:
            continue
        if trade.executed_at is None or trade.executed_at.tzinfo is None:
            continue
        markers.append(
            trade_marker(
                instrument_id=str(instrument),
                trade_id=str(trade.id),
                timestamp=trade.executed_at,
                action=trade.action.value,
                quantity=D(trade.quantity),
                price=D(trade.fill_price),
                currency=trade.trade_currency,
                broker=_BROKER_LABELS.get(broker, getattr(broker, "value", str(broker))),
            )
        )
    decisions = session.scalars(
        select(Decision).where(Decision.instrument_id == instrument, Decision.data_origin == origin)
    ).all()
    for decision in decisions:
        if decision.created_at is None or decision.created_at.tzinfo is None:
            continue
        markers.append(
            decision_marker(
                instrument_id=str(instrument),
                decision_id=str(decision.id),
                timestamp=decision.created_at,
                decision_type=decision.decision_type.value,
                reasoning=decision.reasoning or "",
                conviction=None if decision.user_confidence is None else D(decision.user_confidence),
            )
        )
    events = session.scalars(
        select(MarketEvent).where(MarketEvent.instrument_id == instrument, MarketEvent.data_origin == origin)
    ).all()
    for event in events:
        if event.event_date is None or event.event_date.tzinfo is None:
            continue
        markers.append(
            stored_event_marker(
                instrument_id=str(instrument),
                event_id=str(event.id),
                timestamp=event.event_date,
                classification=event.event_classification.value,
                note=event.context_note or "",
            )
        )
    return dedupe_markers(markers)


def mini_range() -> str:
    return MINI_RANGE


def _placed(markers: list[TimelineMarker], bars: list[PriceBar], currency: str) -> list[dict]:
    return bars_covering(
        markers,
        [bar.timestamp for bar in bars],
        [bar.close for bar in bars],
        currency,
    )


def _market_bar(bar: PriceBar) -> MarketBar:
    return MarketBar(
        start=bar.timestamp,
        interval=bar.interval,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        currency=bar.currency,
        source=bar.source,
    )
