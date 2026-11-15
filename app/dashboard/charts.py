"""Plotly charts for the dashboard. Styling lives here, not on each page."""

from datetime import date, timedelta, timezone

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from app.dashboard.present import format_gbp
from app.dashboard.theme import (
    APRICOT,
    APRICOT_INK,
    BLUE,
    CHART_GREY,
    CORAL,
    INK,
    LAVENDER,
    LINE,
    MUTED,
    PAPER,
    SAGE,
)

_RANGES = {"5D": 5, "1M": 21, "3M": 63}


def slice_history(rows: list[dict], window: str) -> list[dict]:
    if not rows or window == "ALL":
        return rows
    if window == "YTD":
        year = date.fromisoformat(rows[-1]["date"]).year
        chosen = [row for row in rows if date.fromisoformat(row["date"]).year == year]
        return chosen or rows
    count = _RANGES.get(window)
    if count is None:
        return rows
    return rows[-count:] or rows


def _layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        paper_bgcolor=PAPER,
        plot_bgcolor=PAPER,
        font={"family": "Segoe UI, Helvetica Neue, sans-serif", "color": INK, "size": 13},
        margin={"l": 72, "r": 36, "t": 28, "b": 64},
        height=height,
        hoverlabel={"bgcolor": PAPER, "bordercolor": LINE, "font": {"color": INK, "size": 13}},
        showlegend=False,
    )
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        color=MUTED,
        linecolor=LINE,
        tickfont={"size": 12},
        automargin=True,
        nticks=6,
        tickangle=0,
    )
    fig.update_yaxes(
        gridcolor=LINE,
        zeroline=False,
        color=MUTED,
        linecolor=LINE,
        tickfont={"size": 12},
        automargin=True,
        tickformat=",.2f",
        separatethousands=True,
    )
    return fig


def _label_bar_ends(fig: go.Figure, names: list[str], values: list[float], texts: list[str]) -> None:
    """Place each value beside its bar, on the side away from the category names.

    Outside text on a negative bar sits to the left of the tip and covers the name.
    """
    annotations = list(fig.layout.annotations) if fig.layout.annotations else []
    for name, value, text in zip(names, values, texts):
        annotations.append(
            {
                "x": value if value >= 0 else 0,
                "y": name,
                "text": text,
                "showarrow": False,
                "xanchor": "left",
                "yanchor": "middle",
                "xshift": 8,
                "font": {"size": 12, "color": INK},
            }
        )
    fig.update_layout(annotations=annotations)
    if not values:
        return
    low = min(0.0, min(values))
    high = max(0.0, max(values))
    span = max(high - low, 1.0)
    fig.update_xaxes(range=[low - span * 0.04, high + span * 0.42])


def _place_legend(fig: go.Figure) -> None:
    """Keep the legend above the plot, with enough margin that the rows are readable."""
    current = fig.layout.margin
    fig.update_layout(
        showlegend=True,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.04,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 12, "color": INK},
            "bgcolor": "rgba(0,0,0,0)",
            "itemwidth": 30,
            "tracegroupgap": 18,
            "entrywidthmode": "fraction",
            "entrywidth": 0.33,
        },
        margin={
            "l": current.l,
            "r": max(current.r or 0, 36),
            "t": max(current.t or 0, 120),
            "b": max(current.b or 0, 64),
        },
    )


def relative_performance_chart(series: list[dict]) -> go.Figure:
    colours = [BLUE, CHART_GREY, LAVENDER, SAGE]
    fig = go.Figure()
    for index, line in enumerate(series):
        points = line["points"]
        fig.add_trace(
            go.Scatter(
                x=[point["date"] for point in points],
                y=[point["value"] for point in points],
                name=line["name"],
                mode="lines",
                line={"color": colours[index % len(colours)], "width": 2.2},
                hovertext=[f"{line['name']}<br>{point['date']}<br>Rebased {point['value']:,.2f}" for point in points],
                hoverinfo="text",
            )
        )
    fig = _layout(fig, 320)
    _place_legend(fig)
    return fig


def equity_chart(points: list[dict]) -> go.Figure:
    times = [point["time"] for point in points]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32], vertical_spacing=0.08)
    fig.add_trace(go.Scatter(x=times, y=[float(point["equity"]) for point in points], mode="lines", line={"color": BLUE, "width": 2}, name="Equity"), row=1, col=1)
    fig.add_trace(go.Scatter(x=times, y=[float(point["drawdown"]) for point in points], mode="lines", line={"color": CORAL, "width": 2}, name="Drawdown %"), row=2, col=1)
    fig.update_yaxes(title_text="Equity", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown %", row=2, col=1)
    return _layout(fig, 460)


def show_chart(fig: go.Figure, *, interactive: bool = False) -> None:
    config = {"displayModeBar": interactive, "responsive": True}
    if interactive:
        config["scrollZoom"] = True
        config["modeBarButtonsToRemove"] = ["lasso2d", "select2d"]
    st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        config=config,
    )


def portfolio_value_chart(rows: list[dict]) -> go.Figure:
    hover = [
        f"{row['date']}<br>Portfolio {format_gbp(row['value'])}<br>Daily change "
        + ("—" if row["daily_pnl"] is None else format_gbp(row["daily_pnl"]))
        for row in rows
    ]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[row["date"] for row in rows],
            y=[row["value"] for row in rows],
            mode="lines",
            line={"color": BLUE, "width": 2.4},
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[rows[-1]["date"]],
            y=[rows[-1]["value"]],
            mode="markers",
            marker={"size": 9, "color": BLUE},
            hoverinfo="skip",
        )
    )
    fig = _layout(fig, 320)
    fig.update_yaxes(tickprefix="£", tickformat=",.2f")
    return fig


def drawdown_chart(rows: list[dict], limit: float) -> go.Figure:
    hover = [f"{row['date']}<br>Drawdown {row['drawdown']:,.2f}%" for row in rows]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[row["date"] for row in rows],
            y=[row["drawdown"] for row in rows],
            mode="lines",
            line={"color": CORAL, "width": 2},
            fill="tozeroy",
            fillcolor="rgba(217, 137, 126, 0.28)",
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig.add_hline(
        y=limit,
        line_dash="dash",
        line_color=APRICOT,
        annotation_text=f"Limit {limit:,.2f}%",
        annotation_font_color=APRICOT_INK,
        annotation_position="top left",
    )
    fig = _layout(fig, 240)
    fig.update_yaxes(ticksuffix="%")
    return fig


def pnl_contribution_chart(rows: list[dict]) -> go.Figure:
    ordered = list(reversed(rows))
    colors = [SAGE if row["gbp"] >= 0 else CORAL for row in ordered]
    hover = []
    labels = []
    for row in ordered:
        money = format_gbp(row["gbp"])
        share = row.get("share")
        hover.append(f"{row['symbol']}<br>{money}" + (f"<br>{share}" if share else ""))
        labels.append(money if not share else f"{money} · {share}")
    names = [row["symbol"] for row in ordered]
    values = [row["gbp"] for row in ordered]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=names,
            orientation="h",
            marker_color=colors,
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig = _layout(fig, max(240, 48 * len(rows) + 48))
    fig.update_layout(margin={"l": 16, "r": 36, "t": 24, "b": 48})
    fig.update_xaxes(zeroline=True, zerolinecolor=LINE, tickprefix="£", tickformat=",.2f")
    _label_bar_ends(fig, names, values, labels)
    return fig


def allocation_chart(rows: list[dict], limit: float | None = None) -> go.Figure:
    ordered = list(reversed(rows))
    colors = []
    for row in ordered:
        if row.get("kind") == "cash":
            colors.append(CHART_GREY)
        elif limit is not None and row["weight"] >= limit:
            colors.append(CORAL)
        else:
            colors.append(BLUE)
    hover = []
    for row in ordered:
        note = "Above the configured limit" if limit is not None and row.get("kind") != "cash" and row["weight"] >= limit else ""
        extra = f"<br>{note}" if note else ""
        hover.append(f"{row['label']}<br>Weight {row['weight']:,.2f}%<br>Value {format_gbp(row['value'])}{extra}")
    fig = go.Figure(
        go.Bar(
            x=[row["weight"] for row in ordered],
            y=[row["label"] for row in ordered],
            orientation="h",
            marker_color=colors,
            text=[f"{row['weight']:,.2f}%" for row in ordered],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    if limit is not None:
        fig.add_vline(x=limit, line_dash="dash", line_color=APRICOT)
    fig = _layout(fig, max(240, 40 * len(rows) + 48))
    fig.update_layout(margin={"l": 16, "r": 120, "t": 24, "b": 48})
    fig.update_xaxes(ticksuffix="%")
    return fig


def concentration_chart(rows: list[dict], limit: float) -> go.Figure:
    return allocation_chart(rows, limit)


def _wrap_label(name: str, width: int = 16) -> str:
    """Break a long category name so it stays in the margin instead of covering the bar."""
    if len(name) <= width or " " not in name:
        return name
    cut = name.rfind(" ", 0, width + 1)
    if cut <= 0:
        cut = name.find(" ")
    return f"{name[:cut]}<br>{name[cut + 1:]}"


def scenario_impact_chart(rows: list[dict]) -> go.Figure:
    ordered = sorted(rows, key=lambda item: item["impact"])
    ordered = list(reversed(ordered))
    names = [_wrap_label(row["name"]) for row in ordered]
    values = [row["impact"] for row in ordered]
    hover = [f"{row['name']}<br>Portfolio impact {row['impact']:,.2f}%<br>{row.get('assumption', '')}" for row in ordered]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=names,
            orientation="h",
            marker_color=CORAL,
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig = _layout(fig, max(260, 72 * len(rows) + 56))
    fig.update_layout(margin={"l": 168, "r": 36, "t": 24, "b": 48})
    fig.update_yaxes(tickformat="", automargin=True, ticklabelstandoff=8)
    fig.update_xaxes(zeroline=True, zerolinecolor=LINE, ticksuffix="%")
    _label_bar_ends(fig, names, values, [f"{value:,.2f}%" for value in values])
    return fig


def theme_chart(rows: list[dict]) -> go.Figure:
    ordered = list(reversed(rows))
    colors = [CHART_GREY if row["label"] == "Cash" else LAVENDER for row in ordered]
    hover = [f"{row['label']}<br>Weight {row['weight']:,.2f}%" for row in ordered]
    fig = go.Figure(
        go.Bar(
            x=[row["weight"] for row in ordered],
            y=[row["label"] for row in ordered],
            orientation="h",
            marker_color=colors,
            text=[f"{row['weight']:,.2f}%" for row in ordered],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    fig = _layout(fig, max(220, 40 * len(rows) + 40))
    fig.update_layout(margin={"l": 16, "r": 120, "t": 24, "b": 48})
    fig.update_xaxes(ticksuffix="%")
    return fig


def concentration_trend_chart(rows: list[dict], limit: float) -> go.Figure:
    hover = [f"{row['date']}<br>Largest weight {row['top1']:,.2f}%" for row in rows]
    fig = go.Figure(
        go.Scatter(
            x=[row["date"] for row in rows],
            y=[row["top1"] for row in rows],
            mode="lines",
            line={"color": BLUE, "width": 2.2},
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig.add_hline(
        y=limit,
        line_dash="dash",
        line_color=APRICOT,
        annotation_text=f"Limit {limit:,.2f}%",
        annotation_font_color=APRICOT_INK,
        annotation_position="top left",
    )
    fig = _layout(fig, 240)
    fig.update_yaxes(ticksuffix="%")
    return fig


def before_after_chart(rows: list[dict]) -> go.Figure:
    fig = go.Figure()
    for row in rows:
        fig.add_trace(
            go.Scatter(
                x=[row["before"], row["after"]],
                y=[row["label"], row["label"]],
                mode="lines+markers+text",
                line={"color": BLUE, "width": 3},
                marker={"size": 11, "color": [BLUE, INK]},
                text=[f"{row['before']:,.2f}%", f"{row['after']:,.2f}%"],
                textposition=["middle left", "middle right"],
                hovertext=[
                    f"{row['label']}<br>Before {row['before']:,.2f}%",
                    f"{row['label']}<br>After {row['after']:,.2f}%",
                ],
                hoverinfo="text",
                cliponaxis=False,
            )
        )
    fig = _layout(fig, max(200, 56 * len(rows) + 36))
    fig.update_layout(margin={"l": 88, "r": 88, "t": 28, "b": 56})
    fig.update_xaxes(ticksuffix="%")
    return fig


def reward_chart(downside: float, upside: float) -> go.Figure:
    left = -abs(downside)
    right = abs(upside)
    fig = go.Figure(
        go.Scatter(
            x=[left, 0, right],
            y=[0, 0, 0],
            mode="lines+markers+text",
            line={"color": LINE, "width": 8},
            marker={"size": [14, 16, 14], "color": [CORAL, INK, SAGE]},
            text=[f"{left:,.2f}%", "Now", f"+{right:,.2f}%"],
            textposition=["bottom center", "top center", "bottom center"],
            hovertext=[f"Downside {left:,.2f}%", "Decision point", f"Upside +{right:,.2f}%"],
            hoverinfo="text",
        )
    )
    fig = _layout(fig, 120)
    fig.update_xaxes(ticksuffix="%", zeroline=False)
    fig.update_yaxes(visible=False, range=[-1.2, 1.2])
    return fig


def decision_outcome_chart(prices: list[dict], created_on: str, decision_type: str, markers: list[dict]) -> go.Figure | None:
    if len(prices) < 2:
        return None
    start = date.fromisoformat(created_on) - timedelta(days=14)
    window = [row for row in prices if date.fromisoformat(row["date"]) >= start]
    if len(window) < 2:
        window = prices
    anchor = next((row for row in window if row["date"] >= created_on), window[0])
    base = anchor["price"] or None
    if not base:
        return None
    returns = [((row["price"] / base) - 1) * 100 for row in window]
    currency = window[-1].get("currency") or ""
    hover = [
        f"{row['date']}<br>{currency} {row['price']:,.2f}<br>Since decision {change:+,.2f}%"
        for row, change in zip(window, returns)
    ]
    fig = go.Figure(
        go.Scatter(
            x=[row["date"] for row in window],
            y=returns,
            mode="lines",
            line={"color": BLUE, "width": 2.2},
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig.add_vline(
        x=created_on,
        line_dash="dot",
        line_color=INK,
        annotation_text=decision_type,
        annotation_font_color=INK,
        annotation_position="top left",
    )
    if markers:
        fig.add_trace(
            go.Scatter(
                x=[row["date"] for row in markers],
                y=[row["return_pct"] for row in markers],
                mode="markers+text",
                marker={"size": 9, "color": LAVENDER, "line": {"color": INK, "width": 1}},
                text=[row["horizon"] for row in markers],
                textposition="top center",
                hovertext=[f"{row['horizon']}<br>{row['return_pct']:+,.2f}% since the decision close" for row in markers],
                hoverinfo="text",
            )
        )
    fig = _layout(fig, 280)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=LINE)
    return fig


def opportunity_scatter(points: list[dict]) -> go.Figure:
    size = [18 + (18 * (point["confidence"] or 0.4)) for point in points]
    hover = [
        (
            f"{point['symbol']} · {point['decision']}<br>"
            f"Expected downside {point['downside']:,.2f}%<br>"
            f"Expected upside {point['upside']:,.2f}%"
            + (f"<br>Confidence {point['confidence'] * 100:,.2f}%" if point["confidence"] is not None else "")
            + "<br>Stored expectation. Not a recommendation."
        )
        for point in points
    ]
    fig = go.Figure(
        go.Scatter(
            x=[point["downside"] for point in points],
            y=[point["upside"] for point in points],
            mode="markers+text",
            text=[point["symbol"] for point in points],
            textposition="top center",
            marker={"size": size, "color": LAVENDER, "line": {"color": INK, "width": 1}},
            hovertext=hover,
            hoverinfo="text",
        )
    )
    fig = _layout(fig, 380)
    fig.update_layout(margin={"l": 72, "r": 36, "t": 48, "b": 72})
    fig.update_xaxes(title="Expected downside %", ticksuffix="%", automargin=True)
    fig.update_yaxes(title="Expected upside %", ticksuffix="%", automargin=True)
    return fig


def ai_cost_chart(rows: list[dict]) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=[row["date"] for row in rows],
            y=[row["cost"] for row in rows],
            marker_color=LAVENDER,
            hovertext=[f"{row['date']}<br>Estimated cost ${row['cost']:,.2f}" for row in rows],
            hoverinfo="text",
        )
    )
    fig = _layout(fig, 240)
    fig.update_yaxes(tickprefix="$", tickformat=",.2f")
    return fig


def count_bar_chart(rows: list[dict], color: str) -> go.Figure:
    ordered = list(reversed(rows))
    fig = go.Figure(
        go.Bar(
            x=[row["count"] for row in ordered],
            y=[row["label"] for row in ordered],
            orientation="h",
            marker_color=color,
            text=[str(row["count"]) for row in ordered],
            textposition="outside",
            hovertext=[f"{row['label']}<br>{row['count']} runs" for row in ordered],
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    fig = _layout(fig, max(180, 42 * len(rows) + 36))
    fig.update_layout(margin={"l": 16, "r": 72, "t": 24, "b": 48})
    return fig


def price_volume_chart(
    bars: list[dict],
    *,
    style: str,
    trades: list[dict],
    cost_lines: list[dict],
    averages: dict[str, list[float | None]],
    show_volume: bool,
    markers: list[dict] | None = None,
    zones: list[dict] | None = None,
    volume_average: list[float | None] | None = None,
    levels: list[dict] | None = None,
    height: int | None = None,
) -> go.Figure:
    """Price and, when present, bar volume on one time axis.

    ``bars[].volume`` is the volume of that bar. Hover text says so.
    """
    fig = make_subplots(
        rows=2 if show_volume else 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        row_heights=[0.72, 0.28] if show_volume else None,
    )
    hover = [_price_hover(bar, previous) for previous, bar in zip([None, *bars[:-1]], bars)]
    times = [bar["time"] for bar in bars]
    closes = [bar["close"] for bar in bars]
    if style == "candle" and all(bar.get("open") is not None and bar.get("high") is not None and bar.get("low") is not None for bar in bars):
        fig.add_trace(
            go.Candlestick(
                x=times,
                open=[bar["open"] for bar in bars],
                high=[bar["high"] for bar in bars],
                low=[bar["low"] for bar in bars],
                close=closes,
                increasing_line_color=SAGE,
                decreasing_line_color=CORAL,
                hovertext=hover,
                hoverinfo="text",
                name="Price",
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=times,
                y=closes,
                mode="lines",
                line={"color": BLUE, "width": 2.2},
                hovertext=hover,
                hoverinfo="text",
                name="Close",
            ),
            row=1,
            col=1,
        )
    for name, values in averages.items():
        if not values or all(value is None for value in values):
            continue
        fig.add_trace(
            go.Scatter(
                x=times,
                y=values,
                mode="lines",
                name=name,
                line={"width": 1.4},
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )
    grouped_trades: dict[str, list[dict]] = {}
    for trade in trades:
        grouped_trades.setdefault(str(trade["action"]), []).append(trade)
    for action, items in grouped_trades.items():
        fig.add_trace(
            go.Scatter(
                x=[item["time"] for item in items],
                y=[item["price"] for item in items],
                mode="markers",
                name="Buy" if action == "BUY" else "Sell" if action == "SELL" else action.title(),
                marker={
                    "symbol": "triangle-up" if action == "BUY" else "triangle-down",
                    "size": 12,
                    "color": SAGE if action == "BUY" else CORAL,
                },
                hovertext=[item["hover"] for item in items],
                hoverinfo="text",
            ),
            row=1,
            col=1,
        )
    _add_markers(fig, markers or [], row=1, col=1)
    _add_zones(fig, zones or [], times)
    for level in levels or []:
        fig.add_hline(
            y=level["price"],
            line_dash="dash",
            line_color=level.get("color") or MUTED,
            annotation_text=level["name"],
            annotation_position="right",
            row=1,
            col=1,
        )
    for line in cost_lines:
        fig.add_hline(
            y=line["price"],
            line_dash="dot",
            line_color=APRICOT,
            annotation_text=line["name"],
            annotation_position="top left",
            row=1,
            col=1,
        )
    if show_volume:
        colours = []
        for previous, bar in zip([None, *bars[:-1]], bars):
            reference = bar["open"] if bar.get("open") is not None else (previous["close"] if previous else bar["close"])
            colours.append(SAGE if bar["close"] >= reference else CORAL)
        fig.add_trace(
            go.Bar(
                x=times,
                y=[bar["volume"] for bar in bars],
                marker_color=colours,
                name="Volume",
                hovertext=[f"{bar['time']}<br>Bar volume (this bar) {_places(bar['volume'])}" for bar in bars],
                hoverinfo="text",
            ),
            row=2,
            col=1,
        )
        if volume_average and any(value is not None for value in volume_average):
            fig.add_trace(
                go.Scatter(
                    x=times,
                    y=volume_average,
                    mode="lines",
                    name="Volume average",
                    line={"color": APRICOT, "width": 1.4},
                    hoverinfo="skip",
                ),
                row=2,
                col=1,
            )
        fig.update_yaxes(title_text="Volume", title_standoff=16, row=2, col=1)
        fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig = _layout(fig, height or (520 if show_volume else 380))
    fig.update_layout(xaxis_rangeslider_visible=False)
    _place_legend(fig)
    fig.update_yaxes(rangemode="normal", row=1, col=1)
    return fig


_MARKER_STYLE = {
    "TRADE_BUY": ("triangle-up", SAGE, "Buy"),
    "TRADE_SELL": ("triangle-down", CORAL, "Sell"),
    "DECISION": ("diamond", LAVENDER, "Decision"),
    "EARNINGS": ("circle", BLUE, "Event"),
    "COMPANY_EVENT": ("circle", BLUE, "Event"),
    "MARKET_EVENT": ("circle", BLUE, "Event"),
}


def stock_mini_chart(bars: list[dict], markers: list[dict], *, positive: bool) -> go.Figure:
    """Compact close line for one holding. The axis is not pinned to zero."""
    fig = go.Figure()
    colour = SAGE if positive else CORAL
    fig.add_trace(
        go.Scatter(
            x=[bar["time"] for bar in bars],
            y=[bar["close"] for bar in bars],
            mode="lines",
            line={"color": colour, "width": 2},
            hovertext=[_hover_time(bar["time"]) + f"<br>{_places(bar['close'])}" for bar in bars],
            hoverinfo="text",
            name="Close",
        )
    )
    _add_markers(fig, markers, size=9)
    fig = _layout(fig, 230)
    fig.update_yaxes(rangemode="normal", tickformat=",.2f")
    return fig


def stock_detail_chart(
    bars: list[dict],
    *,
    markers: list[dict],
    averages: dict[str, list[float | None]],
    cost_lines: list[dict],
    zones: list[dict],
    show_volume: bool,
    volume_average: list[float | None] | None = None,
    levels: list[dict] | None = None,
) -> go.Figure:
    fig = price_volume_chart(
        bars,
        style="candle",
        trades=[],
        cost_lines=cost_lines,
        averages=averages,
        show_volume=show_volume,
        markers=markers,
        zones=zones,
        volume_average=volume_average,
        levels=levels,
        height=560 if show_volume else 420,
    )
    fig.update_layout(hovermode="x")
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikecolor=MUTED, spikethickness=1)
    fig.update_yaxes(showspikes=True, spikemode="across", spikesnap="cursor", spikecolor=MUTED, spikethickness=1)
    return fig


def _hover_time(value) -> str:
    if hasattr(value, "strftime"):
        moment = value.astimezone(timezone.utc) if getattr(value, "tzinfo", None) else value
        if moment.hour == 0 and moment.minute == 0:
            return moment.strftime("%Y-%m-%d")
        return moment.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def _add_markers(fig: go.Figure, markers: list[dict], size: int = 12, row: int | None = None, col: int | None = None) -> None:
    grouped: dict[str, dict] = {}
    for marker in markers:
        symbol, colour, name = _MARKER_STYLE.get(marker.get("event_type"), ("circle", BLUE, "Event"))
        hover = marker.get("hover") or "<br>".join(
            part for part in (marker.get("title"), str(marker.get("time")), marker.get("description")) if part
        )
        bucket = grouped.setdefault(name, {"symbol": symbol, "colour": colour, "x": [], "y": [], "hover": []})
        bucket["x"].append(marker["time"])
        bucket["y"].append(marker["price"])
        bucket["hover"].append(hover)
    for name, bucket in grouped.items():
        trace = go.Scatter(
            x=bucket["x"],
            y=bucket["y"],
            mode="markers",
            name=name,
            marker={"symbol": bucket["symbol"], "size": size, "color": bucket["colour"], "line": {"width": 0}},
            hovertext=bucket["hover"],
            hoverinfo="text",
        )
        if row is None:
            fig.add_trace(trace)
        else:
            fig.add_trace(trace, row=row, col=col)


def _add_zones(fig: go.Figure, zones: list[dict], times: list) -> None:
    if not times or not zones:
        return
    grouped: dict[str, list[dict]] = {}
    for zone in zones:
        kind = "support" if zone["kind"] == "support" else "resistance"
        colour = SAGE if kind == "support" else CORAL
        fig.add_hrect(
            y0=zone["low"],
            y1=zone["high"],
            fillcolor=colour,
            opacity=0.14,
            line_width=0,
            row=1,
            col=1,
        )
        grouped.setdefault(kind, []).append(zone)
    for kind, items in grouped.items():
        colour = SAGE if kind == "support" else CORAL
        fig.add_trace(
            go.Scatter(
                x=[times[-1]] * len(items),
                y=[(zone["low"] + zone["high"]) / 2 for zone in items],
                mode="markers",
                name="Support" if kind == "support" else "Resistance",
                marker={"size": 8, "color": colour, "opacity": 0.7},
                hovertext=[zone["hover"] for zone in items],
                hoverinfo="text",
            ),
            row=1,
            col=1,
        )


def _places(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.2f}"


def _price_hover(bar: dict, previous: dict | None) -> str:
    change = "—"
    if previous and previous["close"]:
        change = f"{((bar['close'] - previous['close']) / previous['close']) * 100:,.2f}%"
    parts = [str(bar["time"]), f"Close {_places(bar['close'])}", f"Change {change}"]
    if bar.get("open") is not None:
        parts.append(f"Open {_places(bar['open'])}  High {_places(bar['high'])}  Low {_places(bar['low'])}")
    if bar.get("volume") is None:
        parts.append("Bar volume: not stored")
    else:
        parts.append(f"Bar volume (this bar) {_places(bar['volume'])}")
    if bar.get("cumulative_volume") is not None:
        parts.append(f"Cumulative intraday volume {_places(bar['cumulative_volume'])}")
    if bar.get("vwap") is not None:
        parts.append(f"VWAP {_places(bar['vwap'])}")
    return "<br>".join(parts)
