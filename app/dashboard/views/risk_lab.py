import streamlit as st

from app.dashboard.charts import (
    concentration_chart,
    concentration_trend_chart,
    pnl_contribution_chart,
    scenario_impact_chart,
    show_chart,
    theme_chart,
)
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.components import (
    attention_block,
    empty_state,
    hero_number,
    market_event_card,
    metric_strip,
    page_header,
    portfolio_unavailable,
    section_header,
    styled_table,
)
from app.dashboard.present import parse_percent, summary_map
from app.dashboard.series import allocation_rows, contribution_rows, snapshot_history
from core.config import get_settings
from core.enums import DataOrigin
from db.session import session_scope
from services.portfolio_source import portfolio_provider
from services.queries import risk_view
from services.scan import recent_events, refresh_scan


def _percent_rows(rows: list[dict], label_key: str) -> list[dict]:
    parsed = []
    for row in rows:
        weight = parse_percent(str(row.get("weight") or ""))
        if weight is None:
            continue
        parsed.append({"label": row[label_key], "weight": float(weight)})
    return parsed


def render() -> None:
    try:
        with session_scope() as session:
            provider = portfolio_provider()
            view = risk_view(session, provider)
            origin = DataOrigin(view["source"]["origin"]) if view.get("ready") else None
            weights = allocation_rows(session, provider) if view.get("ready") else []
            history = snapshot_history(session, provider) if view.get("ready") else []
            if origin is DataOrigin.MOCK:
                refresh_scan(session)
            events = (
                [row for row in recent_events(session, origin=origin) if row["priority"] in {"HIGH", "CRITICAL"}]
                if origin is not None
                else []
            )
            contribution = contribution_rows(session, provider) if view.get("ready") else {"by_symbol": []}
    except Exception:
        page_header("Risk lab", "What could hurt the portfolio.")
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    if not view.get("ready"):
        page_header("Risk lab", "What could hurt the portfolio.", source=view.get("source"))
        portfolio_unavailable(view.get("source"))
        return

    summary = summary_map(view["summary"])
    settings = get_settings()
    limit = float(settings.concentration_top1_alert) * 100
    page_header("Risk lab", "What could hurt the portfolio.", meta=f"Snapshot {view['as_of']}", source=view.get("source"))
    hero_number(summary.get("Drawdown from high", "—"), "Drawdown from the high")
    metric_strip(
        [
            {
                "value": summary.get("Largest weight", "—"),
                "label": "Largest position",
                "hint": f"Top 5 {summary.get('Top 5', '—')}",
            },
            {"value": summary.get("Top 3", "—"), "label": "Top 3"},
            {
                "value": summary.get("Cash", "—"),
                "label": "Cash",
                "hint": f"High-water {summary.get('High-water value', '—')}",
            },
        ]
    )
    attention_block(view)
    section_header("Recent high-impact moves", "Only HIGH and CRITICAL events. A large move in a tiny weight stays off this list.")
    if not events:
        empty_state("No high-impact market event is stored.")
    for event in events:
        market_event_card(event)

    section_header("Where is the concentration?", "Coral is an actual breach of the configured holding limit. Cash stays grey.")
    if weights:
        show_chart(concentration_chart(weights, limit))
    else:
        empty_state("Holdings will appear once a snapshot is stored.")

    left, right = st.columns(2, gap="large")
    with left:
        section_header("What could hurt the portfolio most?", "Estimated portfolio impact. The bar is the loss, not the scenario name.")
        impacts = []
        for row in view["scenarios"]:
            impact = parse_percent(str(row.get("impact") or ""))
            if impact is None:
                continue
            impacts.append({"name": row["name"], "impact": float(impact), "assumption": row.get("assumption") or ""})
        if impacts:
            show_chart(scenario_impact_chart(impacts))
        else:
            empty_state("Scenario results will appear once a snapshot is stored.")
    with right:
        section_header("Which themes am I actually in?", "Themes are the labels stored on each instrument.")
        themes = _percent_rows(view["themes"], "theme")
        for row in view["accounts"]:
            if row.get("account") == "Cash":
                cash = parse_percent(str(row.get("weight") or ""))
                if cash is not None:
                    themes.append({"label": "Cash", "weight": float(cash)})
        if themes:
            show_chart(theme_chart(themes))
        else:
            empty_state("Theme labels are not stored on these instruments yet.")

    section_header("Is the largest weight rising?", "Largest holding as a share of the portfolio.")
    if len(history) < 2:
        empty_state("Concentration history will appear once snapshots accumulate.")
    else:
        show_chart(concentration_trend_chart(history, limit))

    section_header("Who dragged the portfolio today?", "Negative contribution only. This is price change on shares already held.")
    losers = [row for row in contribution.get("by_symbol") or [] if row.get("gbp", 0) < 0]
    if losers:
        show_chart(pnl_contribution_chart(losers))
    else:
        empty_state("No holding reduced portfolio value between the last two snapshots.")

    section_header("Single-name shocks", "Each figure is the estimated portfolio move. Other holdings stay unchanged.")
    styled_table(
        ["Symbol", "Weight", "-10%", "-20%", "-30%"],
        [
            [row["symbol"], row["weight"], row["-10%"], row["-20%"], row["-30%"]]
            for row in view["grid"]
        ],
    )
    st.caption("VaR, correlation, and expected shortfall are not in this version. Cash is not shocked.")
