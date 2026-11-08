from uuid import UUID

import streamlit as st

from app.dashboard.charts import before_after_chart, decision_outcome_chart, reward_chart, show_chart
from app.dashboard.components import (
    ai_agent_panel,
    choice_rows,
    confidence_bar,
    decision_header,
    decision_narrative,
    empty_state,
    journal_entries,
    page_header,
    section_header,
    styled_table,
)
from app.dashboard.present import DECISION_TONE, confidence_label, format_gbp
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.series import decision_chart_data
from core.config import get_settings
from core.enums import AnalysisTrigger, DecisionStatus, DecisionType
from core.money import D
from db.session import session_scope
from services.decisions import DecisionInput, create_decision, update_decision
from services.queries import decision_detail, decision_list, form_choices
from services.triggers import TriggerDispatcher

_ROLES = ["ANALYST", "SKEPTIC", "RISK_OFFICER", "SYNTHESIS"]


def _payload(choices: dict, prefix: str, existing: dict | None = None) -> DecisionInput | None:
    symbols = [row["symbol"] for row in choices["instruments"]]
    default_symbol = existing["symbol"] if existing else symbols[0]
    symbol = st.selectbox("Instrument", symbols, index=symbols.index(default_symbol), key=f"{prefix}-symbol")
    instrument = next(row for row in choices["instruments"] if row["symbol"] == symbol)
    kinds = [item.value for item in DecisionType]
    kind = st.selectbox(
        "Decision",
        kinds,
        index=kinds.index(existing["decision_type"]) if existing else 0,
        key=f"{prefix}-kind",
    )
    matching = [row for row in choices["theses"] if row["instrument_id"] == instrument["id"]]
    thesis_labels = ["None"] + [row["title"] for row in matching]
    thesis_choice = st.selectbox("Linked thesis", thesis_labels, key=f"{prefix}-thesis")
    thesis_id = None
    if thesis_choice != "None":
        thesis_id = UUID(next(row["id"] for row in matching if row["title"] == thesis_choice))
    proposed = st.text_input("Proposed action", value="" if not existing else existing["proposed_action"], key=f"{prefix}-proposed")
    reasoning = st.text_area("Reasoning", value="" if not existing else existing["reasoning"], key=f"{prefix}-reason")
    thesis_text = st.text_area("Thesis note at this moment", value="" if not existing else existing["thesis_text"], key=f"{prefix}-ttext")
    catalyst = st.text_input("Catalyst", value="" if not existing else existing["catalyst"], key=f"{prefix}-cat")
    horizon = st.text_input("Expected horizon", value="" if not existing else existing["expected_horizon"], key=f"{prefix}-hor")
    upside = st.number_input("Expected upside %", value=0.0, key=f"{prefix}-up")
    downside = st.number_input("Expected downside %", value=0.0, key=f"{prefix}-down")
    invalidation = st.text_area("What would prove this wrong", key=f"{prefix}-inv")
    confidence = st.slider("Your confidence", 0.0, 1.0, 0.5, 0.01, key=f"{prefix}-conf")
    thought = st.text_area("What I thought", key=f"{prefix}-thought")
    expected = st.text_area("What I expected", key=f"{prefix}-expected")
    statuses = [item.value for item in DecisionStatus]
    status = st.selectbox("Status", statuses, index=0, key=f"{prefix}-status")
    trade_labels = ["None"] + [row["label"] for row in choices["trades"]]
    trade_choice = st.selectbox("Link an existing fill (does not place an order)", trade_labels, key=f"{prefix}-trade")
    trade_id = None
    if trade_choice != "None":
        trade_id = UUID(next(row["id"] for row in choices["trades"] if row["label"] == trade_choice))
    if not proposed.strip() or not reasoning.strip():
        return None
    return DecisionInput(
        instrument_id=UUID(instrument["id"]),
        decision_type=DecisionType(kind),
        proposed_action=proposed,
        reasoning=reasoning,
        thesis_id=thesis_id,
        thesis_text=thesis_text,
        catalyst=catalyst,
        expected_horizon=horizon,
        expected_upside_percent=D(str(upside)),
        expected_downside_percent=D(str(downside)),
        invalidation_condition=invalidation,
        user_confidence=D(str(confidence)),
        linked_trade_id=trade_id,
        what_i_thought=thought,
        what_i_expected=expected,
        what_would_prove_me_wrong=invalidation,
        status=DecisionStatus(status),
    )


def _impact(detail: dict, visuals: dict) -> None:
    section_header("What did the book look like around this decision?")
    before = visuals.get("before")
    after = visuals.get("after")
    if not before or not after:
        empty_state("Awaiting later portfolio snapshot.")
        return
    symbol = visuals.get("symbol") or detail.get("symbol")
    show_chart(
        before_after_chart(
            [
                {"label": f"{symbol} weight", "before": before["weight"], "after": after["weight"]},
                {"label": "Cash", "before": before["cash_weight"], "after": after["cash_weight"]},
                {"label": "Largest weight", "before": before["top1"], "after": after["top1"]},
            ]
        )
    )
    st.caption(f"Portfolio {format_gbp(before['value'])} before · {format_gbp(after['value'])} after.")


def render() -> None:
    page_header("Decisions", "What changed, and what happened afterwards.")
    try:
        with session_scope() as session:
            rows = decision_list(session)
            choices = form_choices(session)
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return

    detail = None
    if rows:
        labels = [f"{row['symbol']} · {row['decision_type']} · {row['created_at']}" for row in rows]
        selected = st.selectbox("Open a decision", labels)
        with session_scope() as session:
            detail = decision_detail(session, rows[labels.index(selected)]["id"])
            visuals = decision_chart_data(session, detail["id"]) if detail else {"ready": False}
        if detail:
            decision_header(detail)
            left, right = st.columns([1.3, 0.7], gap="large")
            with left:
                section_header("Expected downside and upside", "The marker is this decision, not a forecast.")
                if visuals.get("downside") is None or visuals.get("upside") is None:
                    empty_state("Upside and downside were not recorded on this decision.")
                else:
                    show_chart(reward_chart(visuals["downside"], visuals["upside"]))
            with right:
                confidence_bar(visuals.get("confidence"))
            chart_col, outcome_col = st.columns(2, gap="large")
            with chart_col:
                _impact(detail, visuals)
            with outcome_col:
                section_header("How did this decision perform?", "Price return from the decision close. Not a benchmark result.")
                figure = decision_outcome_chart(
                    visuals.get("prices") or [],
                    visuals.get("created_on") or "",
                    visuals.get("decision_type") or "",
                    visuals.get("markers") or [],
                )
                if figure is None:
                    empty_state("Price history will appear once closes are stored around this decision.")
                else:
                    show_chart(figure)
                    if not visuals.get("markers"):
                        st.caption("1D, 5D, 20D and 60D markers appear when those later closes exist.")
            decision_narrative(detail)
            journal_entries(detail["journal"])
            section_header("Outcome markers", "Close-to-close instrument return. Pending rows are not plotted.")
            styled_table(
                ["Horizon", "Status", "Return", "Notes"],
                [
                    [row["horizon"], row["status"], row["return"], row["notes"] or "—"]
                    for row in detail["outcomes"]
                ],
            )
            section_header("AI challenge", "These views disagree on purpose. None of them is the decision.")
            for role in _ROLES:
                role_payload = detail["roles"].get(role)
                ai_agent_panel(role, None if not role_payload else role_payload["response"])
            with st.expander("Run metadata"):
                for role in _ROLES:
                    role_payload = detail["roles"].get(role)
                    if not role_payload:
                        st.caption(f"{role} · not run")
                        continue
                    st.caption(
                        f"{role} · {role_payload['provider']} / {role_payload['model']} · {role_payload['created_at']} · "
                        f"tokens {role_payload['input_tokens']}/{role_payload['output_tokens']} · "
                        f"est. ${role_payload['estimated_cost']} · prompt {role_payload['prompt_version']}"
                    )
            st.checkbox("Send this context to the configured external provider", key="confirm_external_decision")
            if st.button("Run pre-decision review on this decision"):
                try:
                    with session_scope() as session:
                        result = TriggerDispatcher().emit(
                            session,
                            AnalysisTrigger.PRE_DECISION_REVIEW,
                            instrument_symbol=detail["symbol"],
                            decision_id=UUID(detail["id"]),
                            allow_external=st.session_state.get("confirm_external_decision", False),
                        )
                    st.info(f"{result.status}: {result.detail}")
                    if result.status == "completed":
                        st.rerun()
                except Exception:
                    st.error("The review did not run.")
        with st.expander("All decisions"):
            choice_rows(
                [
                    {
                        "badge": row["decision_type"],
                        "tone": DECISION_TONE.get(row["decision_type"], "grey"),
                        "title": row["symbol"],
                        "meta": (
                            f"{row['created_at']} · {row['status']} · "
                            f"confidence {confidence_label(row['confidence'])}"
                        ),
                        "detail": "" if row["thesis"] == "—" else row["thesis"],
                    }
                    for row in rows
                ]
            )

    with st.expander("Add a decision"):
        with st.form("add-decision"):
            payload = _payload(choices, "add")
            submitted = st.form_submit_button("Save decision")
        if submitted:
            if payload is None:
                st.error("Proposed action and reasoning are required.")
            else:
                with session_scope() as session:
                    create_decision(session, payload)
                st.success("Decision stored. No order was sent.")
                st.rerun()

    with st.expander("Edit the open decision"):
        if not rows:
            st.caption("Nothing to edit.")
        else:
            with st.form("edit-decision"):
                payload = _payload(choices, "edit", existing=detail if rows else None)
                submitted = st.form_submit_button("Update decision")
            if submitted and payload is not None and detail:
                with session_scope() as session:
                    update_decision(session, UUID(detail["id"]), payload)
                st.success("Decision updated. The previous journal entry remains.")
                st.rerun()
