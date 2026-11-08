"""Plotly charts for the dashboard. Styling lives here, not on each page."""

from datetime import date, timedelta

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
        margin={"l": 8, "r": 16, "t": 8, "b": 8},
        height=height,
        hoverlabel={"bgcolor": PAPER, "bordercolor": LINE, "font": {"color": INK, "size": 13}},
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, color=MUTED, linecolor=LINE, tickfont={"size": 11})
    fig.update_yaxes(gridcolor=LINE, zeroline=False, color=MUTED, linecolor=LINE, tickfont={"size": 11})
    return fig


def price_path_chart(rows: list[dict]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[row["date"] for row in rows],
            y=[row["price"] for row in rows],
            mode="lines+markers",
            marker={"size": 4, "color": BLUE},
            line={"color": BLUE, "width": 2.2},
            hovertext=[f"{row['date']}<br>Price {row['price']:.2f}" for row in rows],
            hoverinfo="text",
        )
    )
    fig = _layout(fig, 280)
    fig.update_yaxes(tickformat=",.2f")
    return fig


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
                hovertext=[f"{line['name']}<br>{point['date']}<br>Rebased {point['value']:.1f}" for point in points],
                hoverinfo="text",
            )
        )
    fig = _layout(fig, 280)
    fig.update_layout(showlegend=True, legend={"orientation": "h", "y": 1.12})
    return fig


def show_chart(fig: go.Figure) -> None:
    st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        config={"displayModeBar": False, "responsive": True},
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
    fig.update_yaxes(tickprefix="£", tickformat=",.0f")
    return fig


def drawdown_chart(rows: list[dict], limit: float) -> go.Figure:
    hover = [f"{row['date']}<br>Drawdown {row['drawdown']:.1f}%" for row in rows]
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
        annotation_text=f"Limit {limit:.0f}%",
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
    fig = go.Figure(
        go.Bar(
            x=[row["gbp"] for row in ordered],
            y=[row["symbol"] for row in ordered],
            orientation="h",
            marker_color=colors,
            text=labels,
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    fig = _layout(fig, max(220, 36 * len(rows) + 40))
    fig.update_layout(margin={"l": 8, "r": 72, "t": 8, "b": 8})
    fig.update_xaxes(zeroline=True, zerolinecolor=LINE, tickprefix="£", tickformat=",.0f")
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
        hover.append(f"{row['label']}<br>Weight {row['weight']:.1f}%<br>Value {format_gbp(row['value'])}{extra}")
    fig = go.Figure(
        go.Bar(
            x=[row["weight"] for row in ordered],
            y=[row["label"] for row in ordered],
            orientation="h",
            marker_color=colors,
            text=[f"{row['weight']:.1f}%" for row in ordered],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    if limit is not None:
        fig.add_vline(x=limit, line_dash="dash", line_color=APRICOT)
    fig = _layout(fig, max(220, 34 * len(rows) + 36))
    fig.update_layout(margin={"l": 8, "r": 56, "t": 8, "b": 8})
    fig.update_xaxes(ticksuffix="%")
    return fig


def concentration_chart(rows: list[dict], limit: float) -> go.Figure:
    return allocation_chart(rows, limit)


def scenario_impact_chart(rows: list[dict]) -> go.Figure:
    ordered = sorted(rows, key=lambda item: item["impact"])
    ordered = list(reversed(ordered))
    hover = [f"{row['name']}<br>Portfolio impact {row['impact']:.1f}%<br>{row.get('assumption', '')}" for row in ordered]
    fig = go.Figure(
        go.Bar(
            x=[row["impact"] for row in ordered],
            y=[row["name"] for row in ordered],
            orientation="h",
            marker_color=CORAL,
            text=[f"{row['impact']:.1f}%" for row in ordered],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    fig = _layout(fig, max(200, 42 * len(rows) + 30))
    fig.update_layout(margin={"l": 8, "r": 64, "t": 8, "b": 8})
    fig.update_xaxes(zeroline=True, zerolinecolor=LINE, ticksuffix="%")
    return fig


def theme_chart(rows: list[dict]) -> go.Figure:
    ordered = list(reversed(rows))
    colors = [CHART_GREY if row["label"] == "Cash" else LAVENDER for row in ordered]
    hover = [f"{row['label']}<br>Weight {row['weight']:.1f}%" for row in ordered]
    fig = go.Figure(
        go.Bar(
            x=[row["weight"] for row in ordered],
            y=[row["label"] for row in ordered],
            orientation="h",
            marker_color=colors,
            text=[f"{row['weight']:.1f}%" for row in ordered],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            cliponaxis=False,
        )
    )
    fig = _layout(fig, max(200, 34 * len(rows) + 30))
    fig.update_layout(margin={"l": 8, "r": 56, "t": 8, "b": 8})
    fig.update_xaxes(ticksuffix="%")
    return fig


def concentration_trend_chart(rows: list[dict], limit: float) -> go.Figure:
    hover = [f"{row['date']}<br>Largest weight {row['top1']:.1f}%" for row in rows]
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
        annotation_text=f"Limit {limit:.0f}%",
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
                text=[f"{row['before']:.1f}%", f"{row['after']:.1f}%"],
                textposition=["middle left", "middle right"],
                hovertext=[
                    f"{row['label']}<br>Before {row['before']:.1f}%",
                    f"{row['label']}<br>After {row['after']:.1f}%",
                ],
                hoverinfo="text",
                cliponaxis=False,
            )
        )
    fig = _layout(fig, max(180, 52 * len(rows) + 24))
    fig.update_layout(margin={"l": 48, "r": 48, "t": 8, "b": 8})
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
            text=[f"{left:.0f}%", "Now", f"+{right:.0f}%"],
            textposition=["bottom center", "top center", "bottom center"],
            hovertext=[f"Downside {left:.0f}%", "Decision point", f"Upside +{right:.0f}%"],
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
        f"{row['date']}<br>{currency} {row['price']:,.2f}<br>Since decision {change:+.1f}%"
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
                hovertext=[f"{row['horizon']}<br>{row['return_pct']:+.1f}% since the decision close" for row in markers],
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
            f"Expected downside {point['downside']:.0f}%<br>"
            f"Expected upside {point['upside']:.0f}%"
            + (f"<br>Confidence {point['confidence'] * 100:.0f}%" if point["confidence"] is not None else "")
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
    fig = _layout(fig, 340)
    fig.update_xaxes(title="Expected downside %", ticksuffix="%")
    fig.update_yaxes(title="Expected upside %", ticksuffix="%")
    return fig


def ai_cost_chart(rows: list[dict]) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=[row["date"] for row in rows],
            y=[row["cost"] for row in rows],
            marker_color=LAVENDER,
            hovertext=[f"{row['date']}<br>Estimated cost ${row['cost']:.4f}" for row in rows],
            hoverinfo="text",
        )
    )
    fig = _layout(fig, 240)
    fig.update_yaxes(tickprefix="$", tickformat=".3f")
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
    fig = _layout(fig, max(160, 36 * len(rows) + 28))
    fig.update_layout(margin={"l": 8, "r": 36, "t": 8, "b": 8})
    return fig


def price_volume_chart(
    bars: list[dict],
    *,
    style: str,
    trades: list[dict],
    cost_lines: list[dict],
    averages: dict[str, list[float | None]],
    show_volume: bool,
) -> go.Figure:
    """Price and, when present, bar volume on one time axis.

    ``bars[].volume`` is the volume of that bar. Hover text says so.
    """
    fig = make_subplots(
        rows=2 if show_volume else 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
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
    for trade in trades:
        fig.add_trace(
            go.Scatter(
                x=[trade["time"]],
                y=[trade["price"]],
                mode="markers",
                name=trade["action"],
                marker={
                    "symbol": "triangle-up" if trade["action"] == "BUY" else "triangle-down",
                    "size": 12,
                    "color": SAGE if trade["action"] == "BUY" else CORAL,
                },
                hovertext=[trade["hover"]],
                hoverinfo="text",
            ),
            row=1,
            col=1,
        )
    for line in cost_lines:
        fig.add_hline(
            y=line["price"],
            line_dash="dot",
            line_color=APRICOT,
            annotation_text=line["name"],
            annotation_position="right",
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
                name="Bar volume",
                hovertext=[f"{bar['time']}<br>Bar volume (this bar) {bar['volume']}" for bar in bars],
                hoverinfo="text",
            ),
            row=2,
            col=1,
        )
        fig.update_yaxes(title_text="Bar volume", row=2, col=1)
    fig = _layout(fig, 460 if show_volume else 320)
    fig.update_layout(showlegend=True, legend={"orientation": "h", "y": 1.08}, xaxis_rangeslider_visible=False)
    return fig


def _price_hover(bar: dict, previous: dict | None) -> str:
    change = "—"
    if previous and previous["close"]:
        change = f"{((bar['close'] - previous['close']) / previous['close']) * 100:.2f}%"
    parts = [str(bar["time"]), f"Close {bar['close']}", f"Change {change}"]
    if bar.get("open") is not None:
        parts.append(f"Open {bar['open']}  High {bar['high']}  Low {bar['low']}")
    if bar.get("volume") is None:
        parts.append("Bar volume: not stored")
    else:
        parts.append(f"Bar volume (this bar) {bar['volume']}")
    if bar.get("cumulative_volume") is not None:
        parts.append(f"Cumulative intraday volume {bar['cumulative_volume']}")
    if bar.get("vwap") is not None:
        parts.append(f"VWAP {bar['vwap']}")
    return "<br>".join(parts)
