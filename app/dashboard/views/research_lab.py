"""Research workspace: watchlist, thesis, stored events, and the shared stock chart."""

import streamlit as st

from app.dashboard.components import ai_agent_panel, empty_state, page_header, section_header
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.theme import LAVENDER
from app.dashboard.views.stock_detail import render_instrument
from app.dashboard.views.top_holdings import _change, _price
from core.config import get_settings
from core.enums import AnalysisTrigger, WatchlistStatus
from app.dashboard.present import two_places
from db.session import session_scope
from services.ai_reuse import should_invoke_model
from services.portfolio_source import portfolio_provider
from services.queries import ai_review
from services.research import (
    EMPTY_EVENTS,
    EMPTY_THESIS,
    SECTIONS,
    THESIS_STATUSES,
    WATCH_STATUSES,
    add_watch,
    apply_open,
    dive_context,
    events_for,
    fallback_source,
    load_universe,
    remove_watch,
    save_thesis,
    thesis_for,
)
from services.triggers import TriggerDispatcher
from app.dashboard.views.strategy_lab import render_strategy_lab


def render() -> None:
    provider = portfolio_provider()
    settings = get_settings()
    try:
        with session_scope() as session:
            source = provider.status(session)
            universe = load_universe(session, provider)
    except Exception:
        page_header(
            "Research lab",
            "Watchlist, the thesis, known events, one name, and a cached-candle backtest.",
            accent=LAVENDER,
            source=fallback_source(settings.data_mode),
        )
        st.error(database_unavailable_message(settings.data_mode))
        return
    page_header(
        "Research lab",
        "Watchlist, the thesis, known events, one name, and a cached-candle backtest.",
        accent=LAVENDER,
        source=source,
    )
    if "research_section_widget" not in st.session_state:
        st.session_state["research_section_widget"] = "Watchlist"
    if universe and st.session_state.get("research_instrument_id") not in {row["instrument_id"] for row in universe}:
        st.session_state["research_instrument_id"] = universe[0]["instrument_id"]
    st.segmented_control("Research section", list(SECTIONS), key="research_section_widget", label_visibility="collapsed")
    section = st.session_state.get("research_section_widget") or "Watchlist"
    if section == "Theses":
        _theses(universe, provider)
    elif section == "Catalysts & Events":
        _events(universe)
    elif section == "Deep Dive":
        _dive(universe, source, provider)
    elif section == "Strategy Lab":
        render_strategy_lab(universe, provider)
    else:
        _watchlist(universe, provider)


def _watchlist(universe: list[dict], provider) -> None:
    section_header("Watchlist", "Holdings, names you add, and names already attached to a thesis or decision.")
    with st.form("add-watch", clear_on_submit=True):
        ticker = st.text_input("Ticker")
        status = st.selectbox("Status", list(WATCH_STATUSES))
        submitted = st.form_submit_button("Add to watchlist")
    if submitted:
        _add(ticker, status, provider)
    notice = st.session_state.pop("research_notice", None)
    if notice:
        st.caption(notice)
    if not universe:
        empty_state("Add a ticker to start a watchlist. Current holdings appear here on their own.")
        return
    for row in universe:
        symbol, action = st.columns([6, 1])
        symbol.button(
            f"{row['symbol']}  {row['company']}",
            key=f"open-{row['instrument_id']}",
            on_click=apply_open,
            args=(st.session_state, row["instrument_id"]),
        )
        if row["removable"]:
            action.button("Remove", key=f"remove-{row['instrument_id']}", on_click=_remove, args=(row,))
        st.caption(
            " · ".join(
                [
                    row["status"],
                    _price(row["price"], row["currency"]),
                    f"1D {_change(row['day_pct'])}",
                    f"1M {_change(row['month_pct'])}",
                    f"Weight {_weight(row['weight'])}" if row["held"] else "Weight —",
                    f"Thesis {'Yes' if row['active_thesis'] else 'No'}",
                    f"Reviewed {row['last_reviewed'] or '—'}",
                    f"Next {row['next_catalyst'] or '—'}",
                ]
            )
        )


def _theses(universe: list[dict], provider) -> None:
    section_header("Theses", "One record per stock. Saving it does not create a decision.")
    selected = _picker(universe)
    if selected is None:
        empty_state("Add a holding or a ticker before writing a thesis.")
        return
    try:
        with session_scope() as session:
            current = thesis_for(session, selected["instrument_id"], origin=provider.origin)
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    if current is None:
        empty_state(EMPTY_THESIS)
    options = list(THESIS_STATUSES)
    if current and current["status"] not in options:
        options.append(current["status"])
    conviction_options = ["—", 1, 2, 3, 4, 5]
    with st.form(f"thesis-{selected['instrument_id']}"):
        summary = st.text_area("Thesis summary", value="" if current is None else current["summary"] or "")
        bull = st.text_area("Bull case", value="" if current is None else current["bull_case"] or "")
        bear = st.text_area("Bear case", value="" if current is None else current["bear_case"] or "")
        assumptions = st.text_area("Key assumptions", value="" if current is None else current["assumptions"] or "")
        risks = st.text_area("Risks", value="" if current is None else current["risks"] or "")
        catalysts = st.text_area("Catalysts", value="" if current is None else current["catalysts"] or "")
        invalidation = st.text_area(
            "Invalidation conditions",
            value="" if current is None else current["invalidation_conditions"] or "",
        )
        conviction = st.selectbox(
            "Conviction",
            conviction_options,
            index=_index(conviction_options, "—" if current is None or current["conviction"] is None else current["conviction"], default=0),
        )
        horizon = st.text_input("Time horizon", value="" if current is None else current["time_horizon"] or "")
        status = st.selectbox(
            "Status",
            options,
            index=_index(options, None if current is None else current["status"], default=0),
        )
        saved = st.form_submit_button("Save thesis")
    if current and current.get("last_reviewed"):
        st.caption(f"Last reviewed {current['last_reviewed'][:10]}.")
    if saved:
        _save(selected, summary, bull, bear, assumptions, risks, catalysts, invalidation, conviction, horizon, status)


def _events(universe: list[dict]) -> None:
    section_header("Catalysts and events", "Only dates and notes that are already stored. Nothing is filled in.")
    selected = _picker(universe)
    if selected is None:
        empty_state("Choose a stock from the watchlist.")
        return
    try:
        with session_scope() as session:
            rows = events_for(session, selected["instrument_id"])
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    if not rows:
        empty_state(EMPTY_EVENTS)
        return
    for row in rows:
        st.markdown(f"**{row['title']}**")
        st.caption(" · ".join(part for part in (row["kind"], row["when_label"], row["detail"]) if part))


def _dive(universe: list[dict], source: dict, provider) -> None:
    selected = _picker(universe)
    if selected is None:
        empty_state("Choose a stock from the watchlist.")
        return
    render_instrument(selected["instrument_id"], embedded=True, source=source)
    try:
        with session_scope() as session:
            context = dive_context(session, selected["instrument_id"], origin=provider.origin)
            review = ai_review(session, selected["instrument_id"])
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    section_header("Thesis", "")
    thesis = context["thesis"]
    if not thesis:
        empty_state(EMPTY_THESIS)
        st.button("Add a thesis", on_click=_go, args=("Theses",))
    else:
        _show_thesis(thesis)
    if context["events"]:
        section_header("Stored events", "")
        for row in context["events"]:
            st.caption(" · ".join(part for part in (row["when_label"], row["kind"], row["title"]) if part))
    else:
        st.caption(EMPTY_EVENTS)
    if context["decisions"]:
        section_header("Decisions", "")
        for row in context["decisions"]:
            st.caption(f"{row['created_at'] or '—'} · {row['decision_type']} · {row['status']}")
    section_header("Gemini review", "Analyst, Skeptic, Risk Officer, and Synthesis. This does not rank a trade.")
    st.checkbox("Send this context to the configured external provider", key="confirm_external_research")
    clicked = st.button("Challenge this thesis")
    if should_invoke_model(button_clicked=clicked, cached=False):
        _challenge(selected["symbol"])
    shown = [
        run
        for run in review["runs"]
        if run["trigger"] in {AnalysisTrigger.MANUAL_DEEP_DIVE.value, AnalysisTrigger.PRE_DECISION_REVIEW.value}
    ][:8]
    if not shown:
        st.caption("No Gemini review is stored for this name.")
        return
    for run in shown:
        meta = f"{run['time']} · {run['provider']} · {run['model']}"
        ai_agent_panel(run["role"], run["response"], meta)


def _picker(universe: list[dict]) -> dict | None:
    if not universe:
        return None
    labels = [f"{row['symbol']} · {row['company']}" for row in universe]
    current = st.session_state.get("research_instrument_id")
    index = next((pos for pos, row in enumerate(universe) if row["instrument_id"] == current), 0)
    choice = st.selectbox("Stock", labels, index=index)
    selected = universe[labels.index(choice)]
    st.session_state["research_instrument_id"] = selected["instrument_id"]
    return selected


def _add(ticker: str, status: str, provider) -> None:
    try:
        with session_scope() as session:
            added = add_watch(session, ticker, origin=provider.origin, status=WatchlistStatus(status))
    except ValueError as exc:
        st.warning(str(exc))
        return
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    st.session_state["research_instrument_id"] = added["instrument_id"]
    st.rerun()


def _remove(row: dict) -> None:
    try:
        with session_scope() as session:
            result = remove_watch(session, row["instrument_id"], held=row["held"])
    except Exception:
        return
    if not result.get("removed"):
        st.session_state["research_notice"] = result.get("reason") or "This name stays on the list."


def _save(selected, summary, bull, bear, assumptions, risks, catalysts, invalidation, conviction, horizon, status) -> None:
    try:
        with session_scope() as session:
            save_thesis(
                session,
                selected["instrument_id"],
                {
                    "summary": summary,
                    "bull_case": bull,
                    "bear_case": bear,
                    "assumptions": assumptions,
                    "risks": risks,
                    "catalysts": catalysts,
                    "invalidation": invalidation,
                    "conviction": None if conviction == "—" else conviction,
                    "time_horizon": horizon,
                    "status": status,
                },
                origin=portfolio_provider().origin,
            )
    except ValueError as exc:
        st.warning(str(exc))
        return
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return
    st.rerun()


def _challenge(symbol: str) -> None:
    try:
        with session_scope() as session:
            result = TriggerDispatcher().emit(
                session,
                AnalysisTrigger.MANUAL_DEEP_DIVE,
                instrument_symbol=symbol,
                allow_external=st.session_state.get("confirm_external_research", False),
            )
    except Exception:
        st.error("AI analysis temporarily unavailable.")
        return
    if result.status == "unavailable":
        st.error("AI analysis temporarily unavailable.")
    elif result.status in {"completed", "stored"}:
        st.rerun()
    else:
        st.caption(result.detail or result.status)


def _show_thesis(thesis: dict) -> None:
    bits = [thesis.get("status") or "", f"Conviction {thesis['conviction']}" if thesis.get("conviction") else ""]
    horizon = thesis.get("time_horizon") or ""
    if horizon:
        bits.append(horizon)
    st.caption(" · ".join(bit for bit in bits if bit))
    for label, key in (
        ("Summary", "summary"),
        ("Bull case", "bull_case"),
        ("Bear case", "bear_case"),
        ("Assumptions", "assumptions"),
        ("Risks", "risks"),
        ("Catalysts", "catalysts"),
        ("Invalidation", "invalidation_conditions"),
    ):
        text = (thesis.get(key) or "").strip()
        if text:
            st.markdown(f"**{label}**")
            st.write(text)


def _go(section: str) -> None:
    st.session_state["research_section_widget"] = section


def _weight(value) -> str:
    if value is None:
        return "—"
    return f"{two_places(float(value) * 100)}%"


def _index(options: list, value, *, default: int) -> int:
    if value in options:
        return options.index(value)
    return default
