"""One instrument, opened from Today or Research Lab. Prices come from the market-data cache."""

from datetime import datetime, timezone
from uuid import UUID

import streamlit as st

from analytics.chart_ranges import HISTORY, INTRADAY_TIMEFRAMES, TIMEFRAMES, history_cap_days
from analytics.market_stats import (
    MarketBar,
    exponential_moving_average,
    latest_relative_volume,
    previous_session_high_low,
    session_vwap_series,
    simple_moving_average,
)
from app.dashboard.charts import show_chart, stock_detail_chart
from app.dashboard.components import empty_state, page_header, section_header
from app.dashboard.present import format_gbp, tidy_quantity, two_places
from app.dashboard.theme import BLUE, CORAL, SAGE
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.views.top_holdings import _change, _price
from core.config import get_settings
from db.models import Instrument
from db.session import session_scope
from services.holdings import holdings_report
from services.market_data import bar_status, build_market_data_service
from services.portfolio_source import portfolio_provider
from services.research import fallback_source
from services.setups import observe_displayed_bars
from services.trade_plans import chart_plans
from services.top_holdings import (
    chart_average_cost,
    day_change,
    load_timeline,
    volume_average_series,
    zones_for_chart,
)

_AGGREGATE = {"5min": "5-minute", "1D": "daily"}


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
    frame_key = f"{prefix}-timeframe-{identity}"
    range_key = f"{prefix}-range-{identity}"
    if frame_key not in st.session_state:
        st.session_state[frame_key] = "1D"
    _apply_chart_link(prefix, frame_key)
    if st.session_state[frame_key] not in TIMEFRAMES:
        st.session_state[frame_key] = "1D"
    if range_key not in st.session_state or st.session_state[range_key] not in HISTORY:
        st.session_state[range_key] = "3M"
    timeframe = st.session_state[frame_key]
    range_code = st.session_state[range_key]
    header_source = source if source is not None else report.get("source")
    try:
        with session_scope() as session:
            service = build_market_data_service(session, provider.origin, now=datetime.now(timezone.utc))
            series = service.chart_series(
                instrument_id=identity,
                symbol=symbol,
                range_code=range_code,
                timeframe=timeframe,
                currency=currency,
            )
            markers = load_timeline(session, identity, provider.origin)
            try:
                chart_plans_rows = chart_plans(session, str(identity), provider.origin)
            except Exception:
                chart_plans_rows = []
            session.commit()
    except Exception:
        if embedded:
            section_header(symbol, company)
        else:
            page_header(symbol, company, source=header_source)
        empty_state("Price history temporarily unavailable.")
        return
    chart_setups = []
    if series.status == "ready" and series.bars:
        chart_setups = observe_displayed_bars(symbol=symbol, timeframe=timeframe, bars=series.bars)

    _header(position, series, header_source, symbol=symbol, company=company, embedded=embedded, timeframe=timeframe)
    controls = st.columns(2)
    controls[0].segmented_control("Timeframe", TIMEFRAMES, key=frame_key)
    controls[1].segmented_control("Range", HISTORY, key=range_key)
    intraday = timeframe in INTRADAY_TIMEFRAMES
    toggles = _toggles(prefix, identity, timeframe, intraday)
    cap = history_cap_days(range_code, timeframe)
    if cap is not None and timeframe in {"5m", "15m"}:
        st.caption(f"{timeframe} shows the last {cap} days. Older candles are not requested and are not invented.")
    if series.status != "ready" or not series.bars:
        empty_state(series.message or "Price history temporarily unavailable.")
        return
    _chart(position, series, markers, toggles=toggles, intraday=intraday, setups=chart_setups, plans=chart_plans_rows)
    _setup_notes(chart_setups)
    _plan_notes(chart_plans_rows)
    _freshness(series, timeframe)
    st.caption(
        "Events on this chart are stored market events and your own decisions. "
        "There is no news feed, so headlines and earnings dates are not invented."
    )


def _header(position, series, source, *, symbol: str, company: str, embedded: bool, timeframe: str) -> None:
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
    bar_as_of = series.bars[-1].timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if series.bars else "—"
    broker = "—" if position.price is None else _price(position.price, position.price_currency)
    st.caption(
        f"Chart close is the stored {timeframe} bar as of {bar_as_of}. "
        f"Broker snapshot {broker} is a different price and is not drawn as the close."
    )


def _toggles(prefix: str, identity: str, timeframe: str, intraday: bool) -> dict[str, bool]:
    defaults = {
        "ema9": intraday,
        "ema20": True,
        "sma50": not intraday,
        "sma200": timeframe == "1D",
        "vwap": intraday,
        "volume": True,
        "levels": True,
    }
    labels = [
        ("ema9", "EMA 9"),
        ("ema20", "EMA 20"),
        ("sma50", "SMA 50"),
        ("sma200", "SMA 200"),
    ]
    if intraday:
        labels.append(("vwap", "VWAP"))
    labels.extend((("volume", "Volume"), ("levels", "Levels")))
    columns = st.columns(len(labels))
    selected = {}
    for column, (key, label) in zip(columns, labels, strict=True):
        selected[key] = column.checkbox(label, value=defaults[key], key=f"{prefix}-{key}-{timeframe}-{identity}")
    selected.setdefault("vwap", False)
    if not intraday:
        st.caption("Session VWAP is calculated on intraday timeframes only.")
    return selected


def _apply_chart_link(prefix: str, frame_key: str) -> None:
    linked = f"{st.query_params.get('timeframe')}|{st.query_params.get('setup')}"
    if not st.query_params.get("setup") or st.session_state.get(f"{prefix}-link") == linked:
        return
    st.session_state[f"{prefix}-link"] = linked
    requested = str(st.query_params.get("timeframe") or "")
    if requested in TIMEFRAMES:
        st.session_state[frame_key] = requested


def _setup_notes(setups: list[dict]) -> None:
    if not setups:
        return
    section_header("Setups", "Observations from completed candles. Not instructions to trade.")
    active = [row for row in setups if row.get("active")]
    earlier = [row for row in setups if not row.get("active")]
    for row in active:
        st.caption(f"Active · {row['label']} · {row['status']} · {row['evidence']} Data {row['data_status']}.")
        st.caption(row["invalidation"])
    for row in earlier[:5]:
        st.caption(f"Earlier · {row['label']} · {row['status']} · {row['evidence']}")


def _plan_notes(plans: list[dict]) -> None:
    if not plans:
        return
    page = st.session_state.get("decisions_page")
    for plan in plans:
        st.caption(f"Planned {plan['direction']} · {plan['status']} · {plan['timeframe']}. Dashed plan lines are not fills.")
        if page is not None:
            st.page_link(page, label="Open trading journal", query_params={"plan": plan["id"]})


def _chart(position, series, markers, *, toggles: dict[str, bool], intraday: bool, setups: list[dict] | None = None, plans: list[dict] | None = None) -> None:
    from analytics.timeline import bars_covering

    currency = series.bars[-1].currency
    market = [_market_bar(bar) for bar in series.bars]
    closes = [bar.close for bar in series.bars]
    placed = bars_covering(markers, [bar.timestamp for bar in series.bars], closes, currency)
    selected = {}
    if toggles["ema9"]:
        selected["EMA 9"] = _floats(exponential_moving_average(closes, 9))
    if toggles["ema20"]:
        selected["EMA 20"] = _floats(exponential_moving_average(closes, 20))
    if toggles["sma50"]:
        selected["SMA 50"] = _floats(simple_moving_average(closes, 50))
    if toggles["sma200"]:
        selected["SMA 200"] = _floats(simple_moving_average(closes, 200))
    vwap = session_vwap_series(market) if intraday else [None] * len(market)
    if toggles["vwap"] and intraday:
        selected["VWAP"] = _floats(vwap)
    missing = [name for name, values in selected.items() if values and all(value is None for value in values)]
    volume_values = [None if bar.volume is None else float(bar.volume) for bar in series.bars]
    show_volume = toggles["volume"] and any(value is not None for value in volume_values)
    volume_average = _floats(volume_average_series(series.bars)) if show_volume else None
    cost = None if position is None else chart_average_cost(position, currency)
    lines = [] if cost is None else [{"name": "Average cost", "price": float(cost)}]
    prior_high, prior_low = previous_session_high_low(market) if toggles["levels"] else (None, None)
    levels = []
    if prior_high is not None:
        levels.append({"name": "Prior high", "price": float(prior_high)})
    if prior_low is not None:
        levels.append({"name": "Prior low", "price": float(prior_low)})
    linked = str(st.query_params.get("level") or "")
    if linked and st.query_params.get("setup"):
        try:
            levels.append({"name": "Setup", "price": float(linked)})
        except ValueError:
            pass
    elif setups:
        current = next((row for row in setups if row.get("active") and row.get("level")), None)
        if current is not None:
            try:
                levels.append({"name": current["label"], "price": float(current["level"])})
            except ValueError:
                pass
    for plan in plans or []:
        for level in plan.get("levels") or []:
            try:
                levels.append(
                    {
                        "name": level["name"],
                        "price": float(level["price"]),
                        "color": {"Plan entry": BLUE, "Plan stop": CORAL, "Plan target": SAGE}.get(level["name"], BLUE),
                    }
                )
            except (TypeError, ValueError):
                continue
    zones = []
    if toggles["levels"]:
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
    chart_column, panel_column = st.columns([2.4, 1])
    with chart_column:
        show_chart(
            stock_detail_chart(
                chart_bars,
                markers=chart_markers,
                averages=selected,
                cost_lines=lines,
                zones=zones,
                levels=levels,
                show_volume=show_volume,
                volume_average=volume_average,
            ),
            interactive=True,
        )
        if any(bar["open"] is None or bar["high"] is None or bar["low"] is None for bar in chart_bars):
            st.caption("Open, high, or low is missing, so this range stays a close line.")
        if missing:
            st.caption(f"{', '.join(missing)} needs more closes than this range contains.")
        if getattr(series, "aggregated_from", None):
            source_name = _AGGREGATE.get(series.aggregated_from, series.aggregated_from)
            st.caption(f"These candles are aggregated from stored {source_name} bars. Missing source bars are left as gaps.")
        if position is not None and cost is None and position.gbp_cost.value is not None and currency != "GBP":
            st.caption(f"GBP average cost is not drawn on this {currency} chart.")
    with panel_column:
        st.markdown('<div class="tech-panel"></div>', unsafe_allow_html=True)
        _panel(
            series,
            selected,
            rvol=latest_relative_volume(market) if toggles["volume"] else None,
            zones=zones,
            prior_high=prior_high,
            prior_low=prior_low,
            currency=currency,
            show_levels=toggles["levels"],
            show_rvol=toggles["volume"],
        )


def _panel(series, selected, *, rvol, zones, prior_high, prior_low, currency, show_levels, show_rvol) -> None:
    section_header("Levels", "Latest value from bars already on the chart.")
    rows = [("Close", _price(series.bars[-1].close, currency))]
    for name, values in selected.items():
        latest = next((value for value in reversed(values) if value is not None), None)
        rows.append((name, "Needs more bars" if latest is None else _shown(latest, currency)))
    for name, value in rows:
        st.caption(f"{name} · {value}")
    if show_rvol:
        st.caption("RVOL · Unavailable" if rvol is None else f"RVOL · {two_places(rvol)}x")
    if not show_levels:
        return
    if prior_high is None or prior_low is None:
        st.caption("Prior session high / low · Unavailable")
    else:
        st.caption(f"Prior high · {_price(prior_high, currency)}")
        st.caption(f"Prior low · {_price(prior_low, currency)}")
    if not zones:
        st.caption("Swing zones · None with two or more touches in this window.")
        return
    for zone in zones:
        label = "Support" if zone["kind"] == "support" else "Resistance"
        st.caption(f"{label} · {_shown(zone['low'], currency)}–{_shown(zone['high'], currency)}")


def _freshness(series, timeframe: str) -> None:
    updated = "—" if series.updated_at is None else series.updated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = bar_status(series.bars, series.interval, datetime.now(timezone.utc))
    st.caption(f"Source {series.source or '—'}. Last updated {updated}. {timeframe} data is {status}.")
    st.caption(
        "Buy and sell marks are stored Trading 212 and IBKR fills. "
        "Events are stored market events and your own decisions. Nothing on this chart is invented."
    )


def _market_bar(bar) -> MarketBar:
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


def _floats(values) -> list[float | None]:
    return [None if value is None else float(value) for value in values]


def _shown(value, currency: str) -> str:
    if isinstance(value, float):
        return _price(f"{value:.2f}", currency)
    return _price(value, currency)


def _float(value) -> float | None:
    return None if value is None else float(value)


