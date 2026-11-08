"""Holdings analysis. Positions come from the selected snapshot, not a broker call."""

from decimal import Decimal

import streamlit as st

from app.dashboard.charts import allocation_chart, pnl_contribution_chart, price_volume_chart, relative_performance_chart, show_chart
from app.dashboard.components import empty_state, page_header, portfolio_unavailable, section_header, styled_table
from app.dashboard.present import format_gbp
from app.dashboard.system_status import database_unavailable_message
from core.config import get_settings
from db.session import session_scope
from services.holdings import bars_for_range, chart_cost_lines, holdings_report, moving_averages, stock_detail
from services.portfolio_source import portfolio_provider


def render() -> None:
    provider = portfolio_provider()
    configured = {"mode": "live" if provider.origin.value == "LIVE" else "mock", "label": provider.label, "ready": False}
    try:
        with session_scope() as session:
            report = holdings_report(session, provider)
            detail = None
            symbol = None
            if report.get("ready") and report["combined"]:
                symbol = st.session_state.get("holdings-symbol") or report["combined"][0].symbol
                if symbol not in {row.symbol for row in report["combined"]}:
                    symbol = report["combined"][0].symbol
                detail = stock_detail(session, symbol, provider)
    except Exception:
        page_header("Holdings", "What you own, and what the stored prices show.", source=configured)
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    if not report.get("ready"):
        page_header("Holdings", "What you own, and what the stored prices show.", source=report.get("source"))
        portfolio_unavailable(report.get("source"))
        return

    page_header(
        "Holdings",
        "Current positions from the selected snapshot. Same symbol is combined by quantity.",
        meta=f"Snapshot {report['source'].get('snapshot_clock') or ''}",
        source=report.get("source"),
    )
    _book(report)
    if not report["combined"]:
        empty_state("This snapshot has no positions.")
        return
    symbols = [row.symbol for row in report["combined"]]
    symbol = st.selectbox("Stock", symbols, index=symbols.index(symbol), key="holdings-symbol")
    if detail is None or detail.get("position") is None or detail["position"].symbol != symbol:
        with session_scope() as session:
            detail = stock_detail(session, symbol, portfolio_provider())
    if not detail.get("ready"):
        empty_state(detail.get("message") or "This symbol is not in the snapshot.")
        return
    _detail(detail)


def _book(report: dict) -> None:
    mode = st.segmented_control("View", ["Combined", "By account"], default="Combined", key="holdings-book")
    if mode == "By account":
        rows = [_lot_row(lot) for lot in report["lots"]]
        rows.sort(key=lambda row: row[0])
    else:
        rows = [_combined_row(row) for row in report["combined"]]
    section_header(
        "Positions",
        "Average cost is weighted by quantity. GBP cost and the broker's native average are labeled separately.",
    )
    styled_table(
        ["Ticker", "Account", "Quantity", "Average cost", "Price", "Value", "P&L", "Weight", "Day"],
        rows,
    )
    settings = get_settings()
    weights = [
        {
            "label": row.symbol,
            "weight": float(row.weight * 100) if row.weight is not None else 0.0,
            "value": float(row.market_value_gbp or 0),
            "kind": "position",
        }
        for row in report["combined"]
        if row.weight is not None
    ]
    total = report["total_gbp"]
    if total:
        weights.append(
            {
                "label": "Cash",
                "weight": float(report["cash_gbp"] / total * 100),
                "value": float(report["cash_gbp"]),
                "kind": "cash",
            }
        )
    left, right = st.columns(2, gap="large")
    with left:
        section_header("Where is the weight?", "Combined by symbol. Cash is the snapshot cash balance.")
        if weights:
            show_chart(allocation_chart(weights, float(settings.concentration_top1_alert) * 100))
        else:
            empty_state("Weights are not stored on these positions.")
    with right:
        section_header("Daily contribution", "Price change on shares held in both latest snapshots, in GBP.")
        contributions = [
            {"symbol": row.symbol, "gbp": float(row.daily_pnl_gbp)}
            for row in report["combined"]
            if row.daily_pnl_gbp is not None
        ]
        if contributions:
            show_chart(pnl_contribution_chart(contributions))
        else:
            empty_state("Daily contribution needs a previous snapshot with prices and FX for every account.")


def _detail(detail: dict) -> None:
    position = detail["position"]
    fresh = detail["freshness"]
    section_header(position.symbol, position.company)
    st.caption(
        f"Snapshot {fresh['snapshot']}. Snapshot price {fresh['snapshot_price']}. {fresh['price_delay']}"
    )
    st.caption(fresh["history"])
    window = st.segmented_control(
        "Range",
        ["INTRADAY", "1W", "1M", "3M", "1Y"],
        default="3M",
        key=f"range-{position.symbol}",
    )
    style = st.segmented_control("Chart", ["Line", "Candlestick"], default="Line", key=f"style-{position.symbol}")
    ma20 = st.checkbox("20D average", key=f"ma20-{position.symbol}")
    ma50 = st.checkbox("50D average", key=f"ma50-{position.symbol}")
    ma200 = st.checkbox("200D average", key=f"ma200-{position.symbol}")
    vwap_on = st.checkbox("VWAP", key=f"vwap-{position.symbol}")
    source_bars = detail["intraday"].bars if window == "INTRADAY" else detail["daily"].bars
    shown = bars_for_range(source_bars, window or "3M")
    if window == "INTRADAY" and not shown:
        empty_state(detail["intraday"].note)
    elif not shown:
        empty_state(detail["daily"].note)
    else:
        candle = style == "Candlestick"
        ohlc = all(bar.open is not None and bar.high is not None and bar.low is not None for bar in shown)
        if candle and not ohlc:
            empty_state("Candles need open, high, and low. Stored closes do not include them, so no candle is drawn.")
        else:
            chart_bars = [_chart_bar(bar, shown) for bar in shown]
            if vwap_on and window == "INTRADAY" and detail["vwap"]["value"] is not None:
                for bar in chart_bars:
                    bar["vwap"] = float(detail["vwap"]["value"])
            averages = {}
            if window != "INTRADAY":
                full = moving_averages(detail["daily"].bars)
                start = len(detail["daily"].bars) - len(shown)
                if ma20:
                    averages["20D"] = [None if value is None else float(value) for value in full["20"][start:]]
                if ma50:
                    averages["50D"] = [None if value is None else float(value) for value in full["50"][start:]]
                if ma200:
                    averages["200D"] = [None if value is None else float(value) for value in full["200"][start:]]
            currency = shown[0].currency
            lines = [
                {"name": line["name"], "price": float(line["price"])}
                for line in chart_cost_lines(position, currency)
            ]
            if vwap_on and window == "INTRADAY" and detail["vwap"]["value"] is not None:
                lines.append({"name": "VWAP", "price": float(detail["vwap"]["value"])})
            show_chart(
                price_volume_chart(
                    chart_bars,
                    style="candle" if candle else "line",
                    trades=[_trade_point(trade) for trade in detail["trades"] if _in_span(trade, shown)],
                    cost_lines=lines,
                    averages=averages,
                    show_volume=all(bar.volume is not None for bar in shown),
                )
            )
            if not all(bar.volume is not None for bar in shown):
                empty_state("Bar volume is not stored. This is not a daily total and not cumulative intraday volume.")
    _cost_notes(position, shown[0].currency if shown else None)
    section_header("Your executions", "BUY and SELL fills for this symbol in the same data mode. Corporate actions are not shown as trades.")
    if detail["trades"]:
        styled_table(
            ["Time", "Action", "Quantity", "Fill", "Broker", "GBP value", "Decision"],
            [
                [
                    trade["time"].isoformat(),
                    trade["action"],
                    _qty(trade["quantity"]),
                    f"{trade['price']} {trade['currency']}",
                    f"{trade['broker']} · {trade['account']}",
                    _gbp(trade["value_gbp"]),
                    "—" if trade["decision"] is None else trade["decision"],
                ]
                for trade in detail["trades"]
            ],
        )
        st.caption("A marker is drawn on the chart when a stored bar covers that execution time.")
    else:
        empty_state("No BUY or SELL fills are stored for this symbol in this data mode.")
    _stat("Daily RVOL", detail["daily_rvol"])
    _stat("Intraday RVOL", detail["intraday_rvol"])
    st.caption(detail["vwap"]["note"])
    section_header("Relative performance", "Requires a stored benchmark mapping and stored benchmark closes.")
    if detail["relative"]["ready"]:
        show_chart(relative_performance_chart(detail["relative"]["series"]))
        st.caption(detail["relative"]["note"])
    else:
        empty_state(detail["relative"]["note"])
    section_header("Thesis", "The stored thesis for this symbol in the same data mode. A price move does not change it.")
    thesis = detail["thesis"]
    if thesis is None:
        empty_state("No thesis is stored for this symbol in this data mode.")
    else:
        st.markdown(f"**{thesis['title']}** · {thesis['status']} · {thesis['horizon']}")
        st.write(thesis["summary"])
        st.markdown("**Bull case**")
        st.write(thesis["bull"])
        st.markdown("**Base case**")
        st.write(thesis["base"])
        st.markdown("**Bear case**")
        st.write(thesis["bear"])
        st.markdown("**Catalysts**")
        st.write(thesis["catalysts"])
        st.caption(f"Invalidation: {thesis['invalidation']}")
        st.caption("A price move does not change this thesis. Status changes only when the thesis record is updated.")


def _cost_notes(position, chart_currency: str | None) -> None:
    if position.gbp_cost.value is None:
        st.caption(position.gbp_cost.method)
    elif chart_currency and chart_currency != "GBP":
        st.caption(
            f"GBP average cost {position.gbp_cost.value} is not drawn on this {chart_currency} chart. {position.gbp_cost.method}"
        )
    else:
        st.caption(f"GBP average cost {position.gbp_cost.value}. {position.gbp_cost.method}")
    if chart_currency:
        for line in chart_cost_lines(position, chart_currency):
            st.caption(f"{line['name']}: {line['price']} {chart_currency}. {line['method']}")
    elif position.native_cost.value is not None:
        st.caption(f"Broker average {position.native_cost.value} {position.native_cost.currency}. {position.native_cost.method}")
    elif position.native_cost.method:
        st.caption(position.native_cost.method)


def _stat(title: str, payload: dict) -> None:
    value = "Unavailable" if payload["value"] is None else f"{payload['value']}x"
    section_header(title, payload["note"])
    st.markdown(f"**{value}**")


def _chart_bar(bar, shown) -> dict:
    cumulative = None
    if bar.interval != "1D" and all(item.volume is not None for item in shown):
        cumulative = float(sum(item.volume for item in shown if item.start <= bar.start))
    return {
        "time": bar.start.isoformat(),
        "open": None if bar.open is None else float(bar.open),
        "high": None if bar.high is None else float(bar.high),
        "low": None if bar.low is None else float(bar.low),
        "close": float(bar.close),
        "volume": None if bar.volume is None else float(bar.volume),
        "cumulative_volume": cumulative,
    }


def _trade_point(trade: dict) -> dict:
    decision = "No linked decision" if trade["decision"] is None else f"Decision {trade['decision']}"
    return {
        "time": trade["time"].isoformat(),
        "action": trade["action"],
        "price": float(trade["price"]),
        "hover": (
            f"{trade['action']}<br>{trade['time'].isoformat()}<br>"
            f"Quantity {trade['quantity']}<br>Fill {trade['price']} {trade['currency']}<br>"
            f"{trade['broker']} · {trade['account']}<br>GBP value {format_gbp(float(trade['value_gbp']))}<br>{decision}"
        ),
    }


def _in_span(trade: dict, bars) -> bool:
    if not bars:
        return False
    start = min(bar.start for bar in bars)
    end = max(bar.start for bar in bars)
    return start <= trade["time"] <= end


def _combined_row(row) -> list[str]:
    account = "Combined · " + ", ".join(f"{lot.broker_label} { _qty(lot.quantity) }" for lot in row.lots)
    cost = _cost(row.gbp_cost.value, "GBP")
    if row.native_cost.value is not None:
        cost = f"{cost} · broker {_cost(row.native_cost.value, row.native_cost.currency)}"
    return [
        row.symbol,
        account,
        _qty(row.quantity),
        cost,
        _cost(row.price, row.price_currency),
        _gbp(row.market_value_gbp),
        f"{_gbp(row.unrealised_gbp)} ({_pct(row.unrealised_pct)})",
        _pct(row.weight),
        f"{_pct(row.daily_change_pct)} · {_gbp(row.daily_pnl_gbp)}",
    ]


def _lot_row(lot) -> list[str]:
    cost = _cost(lot.average_cost_gbp, "GBP")
    if lot.broker_average is not None:
        cost = f"{cost} · broker {_cost(lot.broker_average, lot.broker_average_currency or '')}"
    return [
        lot.symbol,
        f"{lot.broker_label} · {lot.account_label}",
        _qty(lot.quantity),
        cost,
        _cost(lot.price, lot.price_currency),
        _gbp(lot.market_value_gbp),
        f"{_gbp(lot.unrealised_gbp)} ({_pct(lot.unrealised_pct)})",
        _pct(lot.weight),
        f"{_pct(lot.daily_change_pct)} · {_gbp(lot.daily_pnl_gbp)}",
    ]


def _gbp(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return format_gbp(float(value), decimals=2)


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{(value * Decimal('100')).quantize(Decimal('0.01'))}%"


def _qty(value: Decimal) -> str:
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _cost(value: Decimal | None, currency: str) -> str:
    if value is None:
        return "—"
    return f"{value} {currency}".strip()
