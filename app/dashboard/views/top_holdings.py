"""Top holdings on Today. One failed chart stays inside its own card."""

from datetime import datetime, timezone

import streamlit as st

from app.dashboard.charts import show_chart, stock_mini_chart
from app.dashboard.components import empty_state, section_header, show
from app.dashboard.present import format_gbp
from core.money import D
from db.session import session_scope
from services.holdings import holdings_report
from services.market_data import build_market_data_service
from services.portfolio_source import portfolio_provider
from services.top_holdings import (
    HoldingCard,
    build_holding_cards,
    card_from_series,
    load_timeline,
    mini_range,
)


def render_top_holdings() -> None:
    section_header(
        "Top holdings — price & events",
        "Largest current weights. The line is the close. Markers are your executions and stored events.",
    )
    try:
        cards = _load_cards()
    except Exception:
        empty_state("Price history temporarily unavailable.")
        return
    if not cards:
        empty_state("No open holding is stored on the latest snapshot.")
        return
    for start in range(0, len(cards), 2):
        row = cards[start : start + 2]
        columns = st.columns(2, gap="medium")
        for column, card in zip(columns, row):
            with column:
                _card(card)


def _load_cards() -> list[HoldingCard]:
    provider = portfolio_provider()
    with session_scope() as session:
        report = holdings_report(session, provider)
        if not report.get("ready"):
            return []
        origin = provider.origin
        service = build_market_data_service(session, origin, now=datetime.now(timezone.utc))

        def load_one(position):
            try:
                series = service.series(
                    instrument_id=str(position.instrument_id),
                    symbol=position.symbol,
                    range_code=mini_range(),
                    currency=position.price_currency or "",
                )
                markers = load_timeline(session, position.instrument_id, origin)
                card = card_from_series(position, series, markers)
                session.commit()
                return card
            except Exception:
                session.rollback()
                raise

        return build_holding_cards(report["combined"], load_one)


def _card(card: HoldingCard) -> None:
    weight = "—" if card.weight is None else f"{(card.weight * 100):,.2f}%"
    price = _price(card.latest_price, card.price_currency)
    change = _change(card.range_return)
    show(
        f'<div class="holding-card"><p class="holding-symbol">{_text(card.symbol)}</p>'
        f'<p class="holding-company">{_text(card.company)}</p>'
        f'<p class="holding-meta">Portfolio weight {weight}<br>Latest price {price}<br>'
        f"Selected-range return {change}</p></div>"
    )
    if card.message or not card.bars:
        empty_state(card.message or "Price history temporarily unavailable.")
    else:
        show_chart(
            stock_mini_chart(
                [_bar(bar) for bar in card.bars],
                [_marker(marker) for marker in card.markers],
                positive=card.range_return is not None and card.range_return >= 0,
            )
        )
    bits = []
    if card.average_cost is not None:
        bits.append(f"Average cost {_price(card.average_cost, card.average_cost_currency)}")
    if card.unrealised_gbp is not None:
        bits.append(f"Unrealised {format_gbp(float(card.unrealised_gbp))}")
    if card.updated_at is not None:
        bits.append(card.updated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    if card.source:
        bits.append(card.source)
    if card.data_type and card.data_type != "UNKNOWN":
        bits.append(card.data_type)
    if bits:
        st.caption(" · ".join(bits))
    page = st.session_state.get("holding_page")
    if page is not None:
        st.page_link(page, label="Open analysis", query_params={"instrument": card.instrument_id})


def _bar(bar) -> dict:
    return {"time": bar.timestamp, "close": float(bar.close)}


def _marker(marker: dict) -> dict:
    moment = marker["time"]
    label = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if hasattr(moment, "strftime") else str(moment)
    hover = "<br>".join(
        part
        for part in (
            marker.get("title"),
            label,
            marker.get("description"),
            None if marker.get("price") is None else f"Price {marker['price']}",
        )
        if part
    )
    return {
        "time": moment,
        "price": float(marker["price"]),
        "event_type": marker.get("event_type"),
        "title": marker.get("title"),
        "hover": hover,
    }


def _price(value, currency: str) -> str:
    if value is None:
        return "—"
    amount = f"{D(value):,.2f}"
    if currency == "USD":
        return f"${amount}"
    if currency == "GBP":
        return f"£{amount}"
    return f"{amount} {currency}".strip()


def _change(value) -> str:
    if value is None:
        return "—"
    return f"{(value * 100):+.2f}%"


def _text(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
