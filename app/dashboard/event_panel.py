"""Event deep dive. Charts and notes use only stored rows."""

import streamlit as st

from app.dashboard.charts import price_path_chart, relative_performance_chart, show_chart
from app.dashboard.components import (
    ai_agent_panel,
    empty_state,
    section_header,
    timeline_block,
)
from app.dashboard.present import format_gbp
from core.enums import AnalysisTrigger, DecisionType
from db.session import session_scope
from services.scan import attach_event_analysis, record_event_decision
from services.triggers import TriggerDispatcher


def render_event_dive(detail: dict) -> None:
    left, right = st.columns(2, gap="large")
    with left:
        section_header("Price", "Stored closes up to the event.")
        if len(detail.get("prices") or []) < 2:
            empty_state("Awaiting historical data")
        else:
            show_chart(price_path_chart(detail["prices"]))
    with right:
        section_header("Did this name move differently?", detail.get("relative_note") or "")
        if len(detail.get("relative") or []) < 2:
            empty_state("Requires market data integration")
        else:
            show_chart(relative_performance_chart(detail["relative"]))

    section_header("Portfolio contribution", "Market P&L versus the previous close and today's execution prices.")
    if detail.get("contribution") is None:
        empty_state("Today's P&L attribution will appear once current prices and previous closes are available.")
    else:
        st.caption(format_gbp(detail["contribution"]))

    section_header("Timeline", "Only timestamps that are stored.")
    timeline_block(detail.get("timeline") or [])

    section_header("Evidence")
    if detail.get("evidence"):
        st.caption(detail["evidence"])
    else:
        empty_state("Requires research source")

    section_header("Thesis impact", "A price move does not change the stored thesis.")
    thesis = detail.get("thesis")
    if not thesis:
        empty_state("No thesis is stored for this name.")
    else:
        st.caption(f"{thesis['title']} · stored status {thesis['stored_status']} · review {thesis['overall']}")
        st.caption(thesis["note"])
        if thesis.get("summary"):
            st.caption(thesis["summary"])
        if thesis.get("invalidation"):
            st.caption(f"Invalidation still reads: {thesis['invalidation']}")

    section_header("Counter-evidence")
    st.caption(detail.get("counter") or "Requires research source")
    section_header("What would change the interpretation")
    for line in detail.get("would_change") or []:
        st.caption(line)

    section_header("AI analysis", "Analyst, Skeptic, Risk Officer, and Synthesis. Not a trade instruction.")
    if not detail.get("analyses"):
        empty_state("No event analysis is stored yet.")
    for row in detail.get("analyses") or []:
        ai_agent_panel(row["role"], row["response"], meta=row.get("meta") or "")

    if detail.get("linked_decision"):
        st.caption("A decision is already linked to this event. No order was sent.")
    else:
        with st.form(f"event-decision-{detail['id']}"):
            kind = st.selectbox("Decision", ["WATCH", "PASS", "HOLD", "BUY", "SELL"], key=f"event-kind-{detail['id']}")
            reason = st.text_area("Why you are recording this", key=f"event-reason-{detail['id']}")
            saved = st.form_submit_button("Link decision to this event")
        if saved:
            if not reason.strip():
                st.error("A reason is required.")
            else:
                with session_scope() as session:
                    record_event_decision(session, detail["id"], DecisionType(kind), reason)
                st.success("Decision stored and linked. No order was sent.")
                st.rerun()

    st.checkbox("Send this context to the configured external provider", key=f"confirm_event_{detail['id']}")
    if st.button("Run event analysis", key=f"analyse_event_{detail['id']}"):
        try:
            with session_scope() as session:
                result = TriggerDispatcher().emit(
                    session,
                    AnalysisTrigger.LARGE_PRICE_MOVE,
                    instrument_symbol=detail["symbol"],
                    allow_external=st.session_state.get(f"confirm_event_{detail['id']}", False),
                )
                if result.status == "completed":
                    attach_event_analysis(session, detail["id"])
            st.info(f"{result.status}: {result.detail}")
            if result.status == "completed":
                st.rerun()
        except Exception:
            st.error("The event analysis did not run.")
