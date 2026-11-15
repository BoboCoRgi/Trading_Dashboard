"""One instrument, opened from Today or Research Lab. Prices come from the market-data cache."""

from datetime import datetime, timezone
from uuid import UUID

import streamlit as st

from app.dashboard.charts import show_chart, stock_detail_chart
from app.dashboard.components import empty_state, page_header, section_header
from app.dashboard.present import format_gbp, tidy_quantity, two_places
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.views.top_holdings import _change, _price
from core.config import get_settings
from db.models import Instrument
from db.session import session_scope
from services.holdings import holdings_report
from services.market_data import build_market_data_service
from services.portfolio_source import portfolio_provider
from services.research import fallback_source
from services.top_holdings import (
    chart_average_cost,
    daily_rvol,
    day_change,
    load_timeline,
    moving_average_series,
    volume_average_series,
    vwap_for_chart,
    zones_for_chart,
)

_RANGES = ["1D", "5D", "1M", "3M", "1Y"]
_INTRADAY = {"1D", "5D"}


def render() -> None:
    instrument_id = str(st.query_params.get("instrument") or st.session_state.get("holding_instrument_id") or "")
    render_instrument(instrument_id, embedded=False)


def render_instrument(instrument_id: str, *, embedded: bool = False, source: dict | None = None) -> None:
    """Shared price chart. Research Lab embeds it; the holding page keeps the page header."""
    if not instrument_id:
        if embedded:
            empty_state("Choose a stock from the watchlist.")
        else:
            page_header(
                "Holding",
                "Open a holding from Today.",
                source=fallback_source(get_settings().data_mode),
            )
            empty_state("Choose a holding from Today.")
        return
    try:
        parsed = UUID(instrument_id)
    except ValueError:
        empty_state("Choose a stock from the watchlist." if embedded else "This holding is not in the current snapshot.")
        return
    provider = portfolio_provider()
    position = None
    instrument = None
    report: dict = {}
    try:
        with session_scope() as session:
            report = holdings_report(session, provider)
            if report.get("ready"):
                position = next((row for row in report["combined"] if str(row.instrument_id) == instrument_id), None)
            if position is None:
                instrument = session.get(Instrument, parsed)
    except Exception:
        if not embedded:
            page_header("Holding", "Price, trades, and stored events for one name.", source=source)
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    if not embedded and not report.get("ready"):
        page_header("Holding", "Price, trades, and stored events for one name.", source=report.get("source"))
        empty_state(report.get("message") or "The latest snapshot is unavailable.")
        return
    if not embedded and position is None:
        page_header("Holding", "Price, trades, and stored events for one name.", source=report.get("source"))
        empty_state("This holding is not in the current snapshot.")
        return
    if position is None and instrument is None:
        empty_state("This stock is not stored.")
        return

    symbol = position.symbol if position is not None else instrument.symbol
    company = position.company if position is not None else instrument.company_name
    currency = ""
    if position is not None and position.price_currency:
        currency = position.price_currency
    elif instrument is not None and instrument.currency:
        currency = instrument.currency
    identity = str(position.instrument_id if position is not None else instrument.id)
    prefix = "research" if embedded else "holding"
    range_key = f"{prefix}-range-{identity}"
    if range_key not in st.session_state:
        st.session_state[range_key] = "1M"
    range_code = st.session_state[range_key]
    if range_code not in _RANGES:
        range_code = "1M"
        st.session_state[range_key] = range_code
    header_source = source if source is not None else report.get("source")
    try:
        with session_scope() as session:
            service = build_market_data_service(session, provider.origin, now=datetime.now(timezone.utc))
            series = service.series(
                instrument_id=identity,
                symbol=symbol,
                range_code=range_code,
                currency=currency,
            )
            markers = load_timeline(session, identity, provider.origin)
            session.commit()
    except Exception:
        if embedded:
            section_header(symbol, company)
        else:
            page_header(symbol, company, source=header_source)
        empty_state("Price history temporarily unavailable.")
        return

    _header(position, series, header_source, symbol=symbol, company=company, embedded=embedded)
    st.segmented_control("Range", _RANGES, key=range_key)
    intraday = range_code in _INTRADAY
    ma20 = st.checkbox("MA20", value=True, key=f"{prefix}-ma20-{identity}")
    ma50 = st.checkbox("MA50", value=True, key=f"{prefix}-ma50-{identity}")
    ma200 = st.checkbox("MA200", value=False, key=f"{prefix}-ma200-{identity}")
    vwap_on = False
    if intraday:
        vwap_on = st.checkbox("VWAP", value=False, key=f"{prefix}-vwap-{identity}")
    else:
        st.caption("VWAP is shown on intraday ranges only.")
    if series.status != "ready" or not series.bars:
        empty_state(series.message or "Price history temporarily unavailable.")
        return
    _chart(position, series, markers, ma20=ma20, ma50=ma50, ma200=ma200, vwap_on=bool(vwap_on))
    _stats(series, intraday)
    _freshness(series)
    st.caption(
        "Events on this chart are stored market events and your own decisions. "
        "There is no news feed, so headlines and earnings dates are not invented."
    )


def _header(position, series, source, *, symbol: str, company: str, embedded: bool) -> None:
    latest = series.bars[-1].close if series.bars else None
    currency = series.bars[-1].currency if series.bars else (position.price_currency if position is not None else "")
    if embedded:
        section_header(symbol, company)
    else:
        page_header(symbol, company, meta=_price(latest, currency), source=source)
    if position is None:
        if latest is not None:
            st.caption(_price(latest, currency))
        st.caption("Not in the current portfolio.")
        return
    change = day_change(series.bars, series.interval) if series.bars else None
    columns = st.columns(4)
    columns[0].metric("Day change", _change(change))
    columns[1].metric("Quantity", tidy_quantity(position.quantity))
    columns[2].metric("Position value", "—" if position.market_value_gbp is None else format_gbp(float(position.market_value_gbp)))
    columns[3].metric("Weight", "—" if position.weight is None else f"{(position.weight * 100):,.2f}%")
    cost = chart_average_cost(position, currency or "")
    cost_text = _price(cost, currency) if cost is not None else "Not drawn. The stored average is in another currency."
    pnl = "—" if position.unrealised_gbp is None else format_gbp(float(position.unrealised_gbp))
    st.caption(f"Average cost {cost_text}. Unrealised P&L {pnl}. Position value is GBP. The chart axis is {currency or 'the trading currency'}.")


def _chart(position, series, markers, *, ma20: bool, ma50: bool, ma200: bool, vwap_on: bool) -> None:
    from analytics.timeline import bars_covering

    currency = series.bars[-1].currency
    placed = bars_covering(markers, [bar.timestamp for bar in series.bars], [bar.close for bar in series.bars], currency)
    averages = moving_average_series(series.bars)
    selected = {}
    if ma20:
        selected["MA20"] = _floats(averages[20])
    if ma50:
        selected["MA50"] = _floats(averages[50])
    if ma200:
        selected["MA200"] = _floats(averages[200])
    if vwap_on:
        selected["VWAP"] = _floats(vwap_for_chart(series.bars))
    missing = [name for name, values in selected.items() if values and all(value is None for value in values)]
    volume_values = [None if bar.volume is None else float(bar.volume) for bar in series.bars]
    show_volume = any(value is not None for value in volume_values)
    volume_average = _floats(volume_average_series(series.bars)) if show_volume else None
    cost = None if position is None else chart_average_cost(position, currency)
    lines = [] if cost is None else [{"name": "Average cost", "price": float(cost)}]
    zones = [
        {
            "kind": zone.kind,
            "low": float(zone.low),
            "high": float(zone.high),
            "hover": (
                f"{'Support zone' if zone.kind == 'support' else 'Resistance zone'}"
                f"<br>{_price(zone.low, currency)}–{_price(zone.high, currency)}"
                f"<br>Evidence: {zone.touches} recent touches"
            ),
        }
        for zone in zones_for_chart(series.bars)
    ]
    chart_bars = []
    for bar, volume in zip(series.bars, volume_values, strict=True):
        chart_bars.append(
            {
                "time": bar.timestamp,
                "close": float(bar.close),
                "open": _float(bar.open),
                "high": _float(bar.high),
                "low": _float(bar.low),
                "volume": volume,
            }
        )
    chart_markers = []
    for marker in placed:
        moment = marker["time"].astimezone(timezone.utc)
        label = moment.strftime("%Y-%m-%d %H:%M UTC")
        chart_markers.append(
            {
                "time": marker["time"],
                "price": float(marker["price"]),
                "event_type": marker["event_type"],
                "title": marker["title"],
                "hover": "<br>".join(part for part in (marker["title"], label, marker["description"]) if part),
            }
        )
    show_chart(
        stock_detail_chart(
            chart_bars,
            markers=chart_markers,
            averages=selected,
            cost_lines=lines,
            zones=zones,
            show_volume=show_volume,
            volume_average=volume_average,
        )
    )
    if missing:
        st.caption(f"{', '.join(missing)} needs more closes than this range contains.")
    if position is not None and cost is None and position.gbp_cost.value is not None and currency != "GBP":
        st.caption(f"GBP average cost is not drawn on this {currency} chart.")


def _stats(series, intraday: bool) -> None:
    section_header("Relative volume", _rvol_note(intraday))
    if intraday:
        st.markdown("**Unavailable**")
        return
    value = daily_rvol(series.bars)
    st.markdown("**Unavailable**" if value is None else f"**{two_places(value)}x**")


def _freshness(series) -> None:
    updated = "—" if series.updated_at is None else series.updated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.caption(f"Source {series.source or '—'}. Last updated {updated}. Data type {series.data_type}.")


def _rvol_note(intraday: bool) -> str:
    if intraday:
        return (
            "Intraday relative volume needs cumulative volume at the same clock over 20 earlier sessions. "
            "A partial day is not compared with a full-day average."
        )
    return "Daily relative volume is this session's volume divided by the average of the previous 20 full sessions."


def _floats(values) -> list[float | None]:
    return [None if value is None else float(value) for value in values]


def _float(value) -> float | None:
    return None if value is None else float(value)


