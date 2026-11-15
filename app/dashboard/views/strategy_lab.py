"""Strategy Lab inside Research Lab. Runs use cached candles and do not place orders."""

import streamlit as st

from app.dashboard.charts import equity_chart, show_chart
from app.dashboard.components import empty_state, section_header, styled_table
from db.session import session_scope
from services.backtests import run_saved

_STRATEGIES = (
    ("breakout", "Breakout"),
    ("breakout_retest", "Breakout retest"),
    ("pullback", "Trend pullback"),
    ("support_bounce", "Support bounce"),
    ("vwap", "VWAP cross"),
    ("orb", "Opening range breakout"),
)


def render_strategy_lab(universe: list[dict], provider) -> None:
    section_header(
        "Strategy Lab",
        "One symbol, one position, cached candles only. A simulated trade is not a journal entry and is not an order.",
    )
    if not universe:
        empty_state("No symbol is available in this data mode yet.")
        return
    symbols = [row["symbol"] for row in universe]
    left, right = st.columns(2)
    symbol = left.selectbox("Symbol", symbols, key="lab-symbol")
    strategy_label = right.selectbox("Strategy", [label for _key, label in _STRATEGIES], key="lab-strategy")
    strategy = next(key for key, label in _STRATEGIES if label == strategy_label)
    controls = st.columns(4)
    timeframe = controls[0].selectbox("Timeframe", ["1D", "15m", "5m"], key="lab-timeframe")
    days = controls[1].selectbox("History", ["60", "120", "365", "1095"], key="lab-days")
    capital = controls[2].text_input("Starting capital", value="10000", key="lab-capital")
    risk = controls[3].text_input("Risk fraction", value="0.01", key="lab-risk")
    more = st.columns(4)
    stop = more[0].text_input("Stop fraction", value="0.02", key="lab-stop")
    target = more[1].text_input("Target R", value="2", key="lab-target")
    slip = more[2].text_input("Slippage bps", value="5", key="lab-slip")
    spread = more[3].text_input("Spread bps", value="2", key="lab-spread")
    compare = st.checkbox("Compare a small parameter grid", key="lab-compare")
    st.caption("The grid is capped. In-sample and out-of-sample results are shown together. The highest past return is not selected.")
    if st.button("Run backtest", key="lab-run"):
        instrument = next(row for row in universe if row["symbol"] == symbol)
        with st.spinner("Reading cached candles."):
            try:
                with session_scope() as session:
                    report = run_saved(
                        session,
                        origin=provider.origin,
                        instrument_id=instrument["instrument_id"],
                        strategy=strategy,
                        timeframe=timeframe,
                        days=int(days),
                        capital=capital,
                        risk_fraction=risk,
                        stop_fraction=stop,
                        target_r=target,
                        commission_bps="1",
                        slippage_bps=slip,
                        spread_bps=spread,
                        compare=compare,
                    )
                st.session_state["lab-report"] = report
            except Exception as exc:
                st.error(str(exc) if str(exc) else "The backtest did not finish.")
                return
    report = st.session_state.get("lab-report")
    if not report:
        empty_state("No backtest has been run in this session.")
        return
    _show(report)


def _show(report: dict) -> None:
    for note in report["notes"]:
        st.caption(note)
    st.caption(
        f"{report['symbol']} · {report['strategy']} · {report['timeframe']} · {report['version']} · "
        f"{report['bars']} cached bars · {report['source'] or 'no source'} · calculated {report['calculated_at']}"
    )
    metrics = report["metrics"]
    styled_table(
        ["Return", "CAGR", "Max DD", "Sharpe", "Sortino", "Win rate", "Profit factor", "Avg R", "Expectancy", "Exposure", "Trades"],
        [[
            metrics["total_return"],
            metrics["cagr"],
            metrics["max_drawdown"],
            metrics["sharpe"],
            metrics["sortino"],
            metrics["win_rate"],
            metrics["profit_factor"],
            metrics["average_r"],
            metrics["expectancy"],
            metrics["exposure"],
            metrics["trade_count"],
        ]],
    )
    st.caption(f"Buy and hold over the same cached bars, with the same costs: {report['buy_hold_return']}.")
    if metrics.get("sample_is_small") == "yes":
        st.caption("Fewer than 30 simulated trades. Do not treat this as evidence of an edge.")
    journal = report["journal"]
    st.caption(
        f"Journal: {journal['closed_plans']} closed plans"
        f"{'' if journal['average_r'] is None else f', average realised R {journal['average_r']}'}. {journal['note']}"
    )
    if report["equity"]:
        section_header("Equity and drawdown", "Marked from cached closes. Drawdown is the decline from the curve's peak.")
        show_chart(equity_chart(report["equity"]))
    if report["trades"]:
        section_header("Simulated trades", "Entry is the next bar after the signal. Exit reason records the conservative path.")
        styled_table(
            ["Signal", "Entry", "Exit", "Entry px", "Exit px", "P&L", "R", "Exit"],
            [
                [row["signal"][:16], row["entry"][:16], row["exit"][:16], row["entry_price"], row["exit_price"], row["pnl"], row["r"], row["reason"]]
                for row in report["trades"][:50]
            ],
        )
    if report["variants"]:
        section_header("Parameter comparison", "Training is the first 70% of bars. The rest is out of sample. No variant is marked best.")
        styled_table(
            ["Stop", "Target R", "In-sample trades", "In-sample P&L", "Out-of-sample trades", "Out-of-sample P&L", "Overfit risk"],
            [
                [
                    row["stop"],
                    row["target_r"],
                    str(row["in_sample_trades"]),
                    row["in_sample_pnl"],
                    str(row["out_of_sample_trades"]),
                    row["out_of_sample_pnl"],
                    "yes" if row["overfit_risk"] else "no",
                ]
                for row in report["variants"]
            ],
        )
    if report["walk_forward"]:
        section_header("Walk-forward", "Each test window is after its training window.")
        styled_table(
            ["Train through", "Test through", "Train trades", "Train P&L", "Test trades", "Test P&L"],
            [
                [row["train_through"], row["test_through"], str(row["train_trades"]), row["train_pnl"], str(row["test_trades"]), row["test_pnl"]]
                for row in report["walk_forward"]
            ],
        )
    elif report["variants"]:
        st.caption("Walk-forward needs at least 80 cached bars, so it was not run.")
