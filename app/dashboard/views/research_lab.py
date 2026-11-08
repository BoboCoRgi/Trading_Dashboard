from uuid import UUID

import streamlit as st

from app.dashboard.charts import opportunity_scatter, show_chart
from app.dashboard.event_panel import render_event_dive
from app.dashboard.components import (
    ai_agent_panel,
    catalyst_lane,
    choice_rows,
    empty_state,
    market_event_card,
    opportunity_card,
    page_header,
    section_header,
    styled_table,
    thesis_panel,
)
from app.dashboard.present import STATUS_TONE
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.series import event_context, expectation_points, outcome_board, thesis_questions
from app.dashboard.theme import LAVENDER
from core.config import get_settings
from core.enums import AnalysisTrigger
from db.session import session_scope
from research.library import add_note
from services.queries import ai_review, research_view
from services.scan import radar_cards, recent_events, refresh_scan
from services.ai_reuse import should_invoke_model
from services.triggers import TriggerDispatcher


def render() -> None:
    page_header(
        "Research lab",
        "Theses, catalysts, and the questions still open.",
        accent=LAVENDER,
    )
    try:
        with session_scope() as session:
            view = research_view(session)
            refresh_scan(session)
            points = expectation_points(session)
            questions = thesis_questions(session)
            events = recent_events(session)
            radar = radar_cards(session)
            dives = {event["id"]: event_context(session, event["id"]) for event in events}
            outcomes = outcome_board(session)
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    for thesis in view["theses"]:
        thesis["catalysts"] = questions.get(thesis["id"], "")
    watch_symbols = {row["symbol"] for row in view["watchlist"]}
    points = [point for point in points if point["symbol"] in watch_symbols]
    radar_tab, events_tab, outcomes_tab, watch, theses, catalysts, notes, dive = st.tabs(
        ["Radar", "Events", "Outcomes", "Watchlist", "Theses", "Catalysts", "Notes", "Deep dive"]
    )
    with radar_tab:
        section_header("Opportunity radar", "Holdings and the watchlist only. A flag is not a buy.")
        if not radar:
            empty_state("No screen flag on the latest snapshot.")
        for row in radar:
            opportunity_card(row)
        section_header("Where are the stored expectations?", "Decision expectations. Higher is not a reason to buy.")
        if points:
            show_chart(opportunity_scatter(points))
        else:
            empty_state("Expected upside and downside are not stored yet.")
    with events_tab:
        section_header("Market events", "No index is stored, so classification stays unknown.")
        if not events:
            empty_state("No unusual move is stored for the latest snapshot.")
        for event in events:
            market_event_card(event)
            detail = dives.get(event["id"])
            if detail:
                with st.expander(f"{event['symbol']} event detail"):
                    render_event_dive(detail)
    with outcomes_tab:
        section_header("Opportunity outcomes", "Forward returns stay blank until those sessions exist. Rejected names stay on this list.")
        if not outcomes:
            empty_state("No opportunity candidate is stored yet.")
        else:
            styled_table(
                ["Symbol", "Detected", "Type", "Status", "Decision", "Forward path"],
                [
                    [row["symbol"], row["detected"], row["type"], row["status"], row["decision"], row["horizons"]]
                    for row in outcomes
                ],
            )
    with watch:
        plotted = {point["symbol"] for point in points}
        missing = [row["symbol"] for row in view["watchlist"] if row["symbol"] not in plotted]
        if missing:
            st.caption("Not plotted, because no expected upside and downside are stored: " + ", ".join(missing))
        choice_rows(
            [
                {
                    "badge": row["status"],
                    "tone": STATUS_TONE.get(row["status"], "lavender"),
                    "title": row["symbol"],
                    "detail": row["notes"],
                }
                for row in view["watchlist"]
            ]
        )
    with theses:
        if not view["theses"]:
            empty_state("No thesis is stored.")
        for thesis in view["theses"]:
            thesis_panel(thesis)
    with catalysts:
        section_header("Catalysts", "No confirmed calendar date is stored, so these are not placed on a timeline.")
        catalyst_lane(view["catalysts"])
    with notes:
        choice_rows(
            [
                {
                    "title": row["title"],
                    "badge": row["symbol"],
                    "tone": "lavender",
                    "meta": row["created_at"],
                    "detail": row["body"],
                }
                for row in view["notes"]
            ]
        )
        with st.form("add-note"):
            symbols = [row["symbol"] for row in view["instruments"]]
            symbol = st.selectbox("Instrument", symbols)
            title = st.text_input("Title")
            body = st.text_area("Note")
            saved = st.form_submit_button("Save note")
        if saved and title.strip() and body.strip():
            instrument_id = UUID(next(row["id"] for row in view["instruments"] if row["symbol"] == symbol))
            with session_scope() as session:
                add_note(session, instrument_id=instrument_id, title=title, body=body)
            st.success("Note stored.")
            st.rerun()
    with dive:
        symbols = [row["symbol"] for row in view["instruments"]]
        symbol = st.selectbox("Instrument for a deep dive", symbols, key="dive-symbol")
        st.caption("A deep dive runs Analyst, Skeptic, Risk Officer, and Synthesis. It does not rank a trade.")
        st.checkbox("Send this context to the configured external provider", key="confirm_external_research")
        if should_invoke_model(button_clicked=st.button("Deep dive"), cached=False):
            try:
                with session_scope() as session:
                    result = TriggerDispatcher().emit(
                        session,
                        AnalysisTrigger.MANUAL_DEEP_DIVE,
                        instrument_symbol=symbol,
                        allow_external=st.session_state.get("confirm_external_research", False),
                    )
                if result.status == "unavailable":
                    st.error("AI analysis temporarily unavailable.")
                elif result.status in {"completed", "stored"}:
                    st.rerun()
                else:
                    st.info(f"{result.status}: {result.detail}")
            except Exception:
                st.error("AI analysis temporarily unavailable.")
        try:
            with session_scope() as session:
                review = ai_review(session)
        except Exception:
            review = {"runs": []}
        shown = 0
        for run in review["runs"]:
            if run.get("status") not in {None, "completed"}:
                continue
            if run["trigger"] not in {"MANUAL_DEEP_DIVE", "PRE_DECISION_REVIEW"}:
                continue
            if shown >= 4:
                break
            ai_agent_panel(
                run["role"],
                run["response"],
                meta=(
                    f"{run['provider']} · {run['model']} · thinking {run.get('thinking_level') or '—'} · "
                    f"analysis {run['time']}"
                ),
            )
            shown += 1
        if shown == 0:
            empty_state("No deep dive is stored yet.")
