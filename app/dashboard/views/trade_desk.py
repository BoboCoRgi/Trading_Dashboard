"""Short-term plans on the Decisions page. Nothing here places an order."""

from decimal import Decimal

import streamlit as st

from app.dashboard.components import empty_state, section_header, styled_table
from core.money import D, q2
from db.session import session_scope
from services.portfolio_source import portfolio_provider
from services.trade_plans import add_manual_fill, create_plan, link_trade, load_board, save_allocation, update_plan

_TIMEFRAMES = ["5m", "15m", "1H", "1D", "1W"]
_STATUSES = ["DRAFT", "PLANNED", "ACTIVE", "CLOSED", "CANCELLED"]


def render_short_term() -> None:
    try:
        with session_scope() as session:
            board = load_board(session, portfolio_provider())
    except Exception:
        section_header("Short-term trading")
        st.caption("Trading plans could not be loaded. The decision journal below is unchanged.")
        return
    section_header(
        "Short-term trading",
        "Plans and share splits do not place orders. Trading P&L is already inside the portfolio and is not added to it.",
    )
    _performance(board["performance"])
    _plans(board)
    _allocation(board)
    _journal(board)


def _performance(stats: dict) -> None:
    section_header("Trading performance", stats["note"])
    if stats["closed"] == 0 and stats["open"] == 0:
        empty_state("No closed trading result yet. Open plans are not treated as a track record.")
        return
    styled_table(
        ["Closed", "Open", "Win rate", "Avg win £", "Avg loss £", "Avg R", "Profit factor", "Net £", "Planned R", "Actual R", "Drawdown £"],
        [[
            str(stats["closed"]),
            str(stats["open"]),
            stats["win_rate"] or "—",
            stats["average_win"] or "—",
            stats["average_loss"] or "—",
            stats["average_r"] or "—",
            stats["profit_factor"] or "—",
            stats["net"] or "—",
            stats["planned_rr"] or "—",
            stats["actual_r"] or "—",
            stats["drawdown"] or "—",
        ]],
    )
    if stats["sample_is_small"]:
        st.caption("These figures describe the stored trades. They are not evidence that the strategy has an edge.")
    if stats["groups"]:
        styled_table(["Group", "Closed", "Win rate", "Net £", "Avg R"], stats["groups"])


def _plans(board: dict) -> None:
    plans = board["plans"]
    if plans:
        labels = {row["id"]: f"{row['symbol']} · {row['direction']} · {row['strategy']} · {row['status']}" for row in plans}
        ids = list(labels)
        requested = str(st.query_params.get("plan") or "")
        if requested in labels and st.session_state.get("plan-link") != requested:
            st.session_state["plan-link"] = requested
            st.session_state["trade-plan"] = requested
        selected = st.selectbox("Open a plan", ids, format_func=lambda item: labels[item], key="trade-plan")
        plan = next(row for row in plans if row["id"] == selected)
        st.caption(
            f"{plan['timeframe']} · {plan['shares']} shares · risk {plan['potential_loss']} {plan['capital_currency']}"
            f" · reward {plan['potential_profit'] or '—'} · R {plan['risk_reward'] or '—'} · capital {plan['required_capital']}"
        )
        st.caption("Calculated from the entry and stop in the currency you entered. Currencies are not converted. This is not an instruction to trade.")
        page = st.session_state.get("holding_page")
        if page is not None:
            params = {"instrument": plan["instrument_id"], "timeframe": plan["timeframe"] if plan["timeframe"] in _TIMEFRAMES else "1D"}
            if plan["setup_id"] and (plan["entry_price"] or plan["stop_loss"]):
                params["setup"] = plan["setup_id"]
                params["level"] = plan["entry_price"] or plan["stop_loss"]
            st.page_link(page, label=f"Open {plan['symbol']} chart", query_params=params)
        with st.expander("Edit this plan"):
            _edit_form(board, plan)
    with st.expander("New plan"):
        _create_form(board)


def _allocation(board: dict) -> None:
    section_header("Core and trading shares", "Core is the broker position minus the trading allocation. The broker holding is not changed.")
    holdings = board["holdings"]
    if not holdings:
        empty_state("No broker position is stored, so there is nothing to split.")
        return
    with st.form("share-allocation"):
        entered = {}
        for row in holdings:
            entered[row["instrument_id"]] = st.text_input(
                f"{row['symbol']} trading shares · broker {_shown(row['broker_quantity'])}",
                value=row["trading_quantity"],
            )
            if row["core_quantity"] is not None:
                st.caption(f"Core {_shown(row['core_quantity'])} · Trading {_shown(row['trading_quantity'])}")
            else:
                st.caption("This allocation is above the broker position, so it is not applied to P&L.")
        if st.form_submit_button("Save share split"):
            _save_allocations(holdings, entered)


def _journal(board: dict) -> None:
    section_header("Trading journal", "P&L uses stored executions. A manual row is labelled and is not a broker fill.")
    plan_id = st.session_state.get("trade-plan")
    plan = next((row for row in board["plans"] if row["id"] == plan_id), None)
    if plan is None:
        empty_state("Open a plan to record how it was traded.")
        return
    st.caption(plan["pnl_note"])
    pieces = [
        f"Open quantity {plan['open_quantity']}",
        f"Realised {plan['realised'] or '—'} {plan['currency']}",
        f"Realised £ {plan['realised_gbp'] or '—'}",
        f"Unrealised {plan['unrealised'] or '—'}",
        f"Actual R {plan['r_multiple'] or '—'}",
        f"Held {plan['duration'] or '—'}",
    ]
    st.caption(" · ".join(pieces))
    if plan["fills"]:
        styled_table(
            ["When", "Source", "Side", "Qty", "Price", "Fees", "Note"],
            [
                [row["executed_at"], row["source"], row["action"], row["quantity"], f"{row['price']} {row['currency']}", row["fees"], row["notes"]]
                for row in plan["fills"]
            ],
        )
    else:
        empty_state("No execution is linked. Broker history is not invented.")
    trades = [row for row in board["trades"] if row["instrument_id"] == plan["instrument_id"]]
    if trades:
        choice = st.selectbox("Stored broker fill", ["None"] + [row["label"] for row in trades], key=f"link-{plan['id']}")
        if st.button("Attach fill", key=f"attach-{plan['id']}"):
            if choice == "None":
                st.error("Choose a stored fill.")
            else:
                trade_id = next(row["id"] for row in trades if row["label"] == choice)
                _run(lambda session: link_trade(session, plan["id"], trade_id), "Fill attached. It was not sent again.")
    with st.form(f"manual-{plan['id']}"):
        st.caption("Manual entry")
        action = st.selectbox("Side", ["BUY", "SELL"])
        quantity = st.text_input("Quantity")
        price = st.text_input("Price")
        fees = st.text_input("Fees", value="0")
        currency = st.text_input("Currency", value=plan["currency"] or "USD")
        fx = st.text_input("GBP per unit of currency, if known", value="")
        when = st.text_input("Time UTC", value="")
        notes = st.text_area("What I learned")
        if st.form_submit_button("Save manual entry"):
            _run(
                lambda session: add_manual_fill(
                    session,
                    plan["id"],
                    {
                        "action": action,
                        "quantity": quantity,
                        "price": price,
                        "fees": fees or "0",
                        "currency": currency,
                        "fx_rate_to_gbp": fx,
                        "executed_at": when,
                        "notes": notes,
                    },
                ),
                "Manual entry stored. No broker order was created.",
            )


def _create_form(board: dict) -> None:
    instruments = board["instruments"]
    if not instruments:
        empty_state("No symbol is available in this data mode yet.")
        return
    with st.form("new-trade-plan"):
        payload = _fields(board, "new", None)
        submitted = st.form_submit_button("Save plan")
        if submitted and payload is None:
            st.error("Check the strategy, entry, stop and risk figures.")
        if submitted and payload is not None:
            _run(lambda session: create_plan(session, portfolio_provider().origin, payload), "Plan stored. No order was sent.")


def _edit_form(board: dict, plan: dict) -> None:
    with st.form(f"edit-plan-{plan['id']}"):
        payload = _fields(board, plan["id"], plan)
        submitted = st.form_submit_button("Update plan")
        if submitted and payload is None:
            st.error("Check the strategy, entry, stop and risk figures.")
        if submitted and payload is not None:
            _run(lambda session: update_plan(session, plan["id"], payload), "Plan updated. The previous version remains in the revision log.")


def _fields(board: dict, prefix: str, existing: dict | None) -> dict | None:
    instruments = board["instruments"]
    symbols = [row["symbol"] for row in instruments]
    current = existing["symbol"] if existing else symbols[0]
    symbol = st.selectbox("Symbol", symbols, index=symbols.index(current) if current in symbols else 0)
    instrument = next(row for row in instruments if row["symbol"] == symbol)
    strategy = st.text_input("Strategy", value="" if not existing else existing["strategy"])
    timeframe = st.selectbox("Timeframe", _TIMEFRAMES, index=_TIMEFRAMES.index(existing["timeframe"]) if existing and existing["timeframe"] in _TIMEFRAMES else 3)
    direction = st.selectbox("Direction", ["LONG", "SHORT"], index=0 if not existing or existing["direction"] == "LONG" else 1)
    entry = st.text_input("Entry price", value="" if not existing else (existing["entry_price"] or ""))
    low = st.text_input("Entry zone low", value="" if not existing else (existing["entry_low"] or ""))
    high = st.text_input("Entry zone high", value="" if not existing else (existing["entry_high"] or ""))
    stop = st.text_input("Stop", value="" if not existing else existing["stop_loss"])
    target = st.text_input("Target", value="" if not existing else (existing["take_profit"] or ""))
    mode = st.selectbox("Risk", ["Amount", "Percent of capital"], index=0 if not existing or existing["risk_amount"] else 1)
    risk = st.text_input("Risk amount or percent", value="" if not existing else (existing["risk_amount"] or _percent(existing["risk_fraction"])))
    capital = st.text_input("Trading capital", value="" if not existing else (existing["capital_amount"] or ""))
    currency = st.text_input("Capital currency", value="USD" if not existing else existing["capital_currency"])
    statuses = _STATUSES
    status = st.selectbox("Status", statuses, index=statuses.index(existing["status"]) if existing else 1)
    choices = ["None"] + [row["label"] for row in board["setups"] if row["instrument_id"] == instrument["id"]]
    current_setup = "None"
    if existing and existing["setup_id"]:
        match = next((row["label"] for row in board["setups"] if row["id"] == existing["setup_id"]), "None")
        current_setup = match
    setup = st.selectbox("Technical signal", choices, index=choices.index(current_setup) if current_setup in choices else 0)
    notes = st.text_area("Notes", value="" if not existing else existing["notes"])
    if not strategy.strip() or not stop.strip() or not risk.strip():
        return None
    if not entry.strip() and not low.strip() and not high.strip():
        return None
    payload = {
        "instrument_id": instrument["id"],
        "strategy": strategy,
        "timeframe": timeframe,
        "direction": direction,
        "entry_price": entry.strip(),
        "entry_low": low.strip(),
        "entry_high": high.strip(),
        "stop_loss": stop.strip(),
        "take_profit": target.strip(),
        "capital_currency": currency.strip() or "USD",
        "status": status,
        "setup_id": "" if setup == "None" else next(row["id"] for row in board["setups"] if row["label"] == setup),
        "notes": notes,
    }
    try:
        if mode == "Amount":
            payload["risk_amount"] = risk.strip()
            payload["risk_fraction"] = ""
            payload["capital_amount"] = capital.strip()
        else:
            payload["risk_amount"] = ""
            payload["risk_fraction"] = str(D(risk.strip()) / Decimal("100"))
            payload["capital_amount"] = capital.strip()
    except Exception:
        return None
    return payload


def _save_allocations(holdings: list[dict], entered: dict[str, str]) -> None:
    try:
        with session_scope() as session:
            provider = portfolio_provider()
            fresh = {row["instrument_id"]: row for row in load_board(session, provider)["holdings"]}
            for instrument_id, text in entered.items():
                current = fresh.get(instrument_id)
                if current is None:
                    continue
                save_allocation(session, provider.origin, instrument_id, D(text.strip() or "0"), D(current["broker_quantity"]))
        st.success("Share split stored. Broker positions were not changed.")
        st.rerun()
    except Exception as exc:
        st.error(str(exc) if str(exc) else "The share split was not saved.")


def _run(action, message: str) -> None:
    try:
        with session_scope() as session:
            action(session)
        st.success(message)
        st.rerun()
    except Exception as exc:
        st.error(str(exc) if str(exc) else "That was not saved.")


def _shown(value: str) -> str:
    try:
        return f"{q2(D(value)):.2f}"
    except Exception:
        return value


def _percent(value: str | None) -> str:
    if not value:
        return ""
    return str(D(value) * Decimal("100"))
