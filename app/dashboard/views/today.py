import streamlit as st

from app.dashboard.charts import allocation_chart, pnl_contribution_chart, show_chart
from app.dashboard.components import (
    activity_list,
    attention_block,
    brief_block,
    change_block,
    empty_state,
    market_event_card,
    opportunity_card,
    page_header,
    portfolio_unavailable,
    positions_table,
    portfolio_pulse,
    section_header,
)
from app.dashboard.event_panel import render_event_dive
from app.dashboard.present import format_gbp
from app.dashboard.series import allocation_rows, event_context
from app.dashboard.system_status import database_unavailable_message
from core.config import get_settings
from core.enums import AnalysisTrigger, DataOrigin, DecisionType
from core.money import D
from db.session import session_scope
from services.attribution import load_today_attribution
from services.portfolio_source import portfolio_provider
from services.queries import today_view
from services.scan import promote_candidate, radar_cards, recent_events, refresh_scan
from services.ai_reuse import should_invoke_model
from services.triggers import TriggerDispatcher


def render() -> None:
    try:
        with session_scope() as session:
            provider = portfolio_provider()
            view = today_view(session, provider)
            origin = DataOrigin(view["source"]["origin"]) if view.get("ready") else None
            if origin is DataOrigin.MOCK:
                refresh_scan(session)
            weights = allocation_rows(session, provider) if view.get("ready") else []
            attribution = load_today_attribution(session, provider) if view.get("ready") else {"ready": False, "rows": []}
            events = recent_events(session, origin=origin) if origin is not None else []
            radar = radar_cards(session, origin=origin) if origin is not None else []
            dives = {event["id"]: event_context(session, event["id"], provider) for event in events}
    except Exception:
        page_header("Today", "What requires attention today.")
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    if not view.get("ready"):
        page_header("Today", "What requires attention today.", source=view.get("source"))
        portfolio_unavailable(view.get("source"))
        return

    settings = get_settings()
    page_header("Today", "What requires attention today.", meta=f"Snapshot {view['as_of']}", source=view.get("source"))
    market_pnl = None if not attribution.get("ready") else format_gbp(float(attribution["total_gbp"]))
    portfolio_pulse(view, market_pnl=market_pnl)

    left, right = st.columns(2, gap="large")
    with left:
        section_header(
            "What's driving my P&L today?",
            "Beginning-of-day shares versus the previous close, plus today's fills versus their execution price.",
        )
        if not attribution.get("ready"):
            empty_state("Today's P&L attribution will appear once current prices and previous closes are available.")
        else:
            show_chart(pnl_contribution_chart(_chart_rows(attribution)))
    with right:
        section_header("Where is the weight?", "Same symbol is combined across accounts. Coral means above the configured limit.")
        if not weights:
            empty_state("Holdings will appear once a snapshot is stored.")
        else:
            show_chart(allocation_chart(weights, float(settings.concentration_top1_alert) * 100))

    attention_block(view)
    section_header("Market events", "Detected from stored prices. Classification stays unknown without a benchmark.")
    if not events:
        empty_state("No unusual move on the latest snapshot. A smooth path does not become an event.")
    for event in events:
        market_event_card(event)
        detail = dives.get(event["id"])
        if detail:
            with st.expander(f"{event['symbol']} event detail"):
                render_event_dive(detail)
    section_header("Opportunity radar", "A screen of holdings and the watchlist. Not a recommendation.")
    if not radar:
        empty_state("No screen flag on the latest snapshot.")
    for row in radar:
        opportunity_card(row)
        if row["status"] == "NEW":
            with st.expander(f"Record a decision on {row['symbol']}"):
                with st.form(f"promote-{row['id']}"):
                    kind = st.selectbox("Decision", ["WATCH", "PASS", "HOLD", "BUY", "SELL"], key=f"kind-{row['id']}")
                    reason = st.text_area("Why you are recording this", key=f"reason-{row['id']}")
                    saved = st.form_submit_button("Save decision")
                if saved:
                    if not reason.strip():
                        st.error("A reason is required. The screen does not decide.")
                    else:
                        with session_scope() as session:
                            promote_candidate(session, row["id"], DecisionType(kind), reason)
                        st.success("Decision stored. No order was sent.")
                        st.rerun()

    left, right = st.columns(2, gap="large")
    with left:
        change_block(view["changes"])
    with right:
        activity_list(view["trades"])
    positions_table(view["positions"])
    brief_block(view["brief"])
    st.checkbox("Send this context to the configured external provider", key="confirm_external_today")
    if should_invoke_model(button_clicked=st.button("Run AI review"), cached=False):
        confirm = st.session_state.get("confirm_external_today", False)
        try:
            with session_scope() as session:
                result = TriggerDispatcher().emit(
                    session,
                    AnalysisTrigger.DAILY_CLOSE_REVIEW,
                    allow_external=confirm,
                )
            if result.status == "unavailable":
                st.error("AI analysis temporarily unavailable.")
            elif result.status in {"completed", "stored"}:
                st.rerun()
            else:
                st.info(f"{result.status}: {result.detail}")
        except Exception:
            st.error("AI analysis temporarily unavailable.")


def _chart_rows(attribution: dict) -> list[dict]:
    total = attribution.get("total_gbp")
    rows = []
    for row in attribution["rows"]:
        gbp = row["contribution_gbp"]
        rows.append({"symbol": row["symbol"], "gbp": float(gbp), "share": _share(gbp, total)})
    return rows


def _share(gbp, total) -> str | None:
    if total in (None, 0) or gbp == 0:
        return None
    portion = abs(D(gbp) / D(total) * 100)
    shown = f"{portion:.0f}%"
    if total < 0 and gbp < 0:
        return f"{shown} of today's loss"
    if total > 0 and gbp > 0:
        return f"{shown} of today's gain"
    return f"{shown} of today's move"
