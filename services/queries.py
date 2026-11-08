"""Read models shared by Streamlit and FastAPI."""

from datetime import timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.exposure import exposure_by
from analytics.scenarios import ScenarioDefinition, ShockPosition, run_scenario
from core.enums import AIRole, DataOrigin
from core.money import D, q2, q8
from db.models import (
    AIClaim,
    AIRun,
    BrokerAccount,
    Catalyst,
    Decision,
    Instrument,
    PortfolioSnapshot,
    PositionSnapshot,
    ResearchNote,
    Thesis,
    Trade,
    WatchlistItem,
)
from journal.assemble import analyses_for, journal_for, outcomes_for
from services.ledger import build_risk_context, snapshot_before
from services.portfolio_source import PortfolioDataProvider, portfolio_provider


def _book(session: Session, provider: PortfolioDataProvider | None):
    chosen = provider or portfolio_provider()
    snapshot = chosen.latest_snapshot(session)
    status = chosen.status(session, snapshot=snapshot, loaded=True)
    return chosen, snapshot, status


def _unavailable(status: dict) -> dict:
    return {
        "ready": False,
        "source": status,
        "message": status["message"],
        "command": status["command"],
    }


def _money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    amount = q2(D(value))
    sign = "-" if amount < 0 else ""
    return f"{sign}£{abs(amount):,.2f}"


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{(D(value) * D(100)).quantize(Decimal('0.01'))}%"


def _when(value) -> str:
    if value is None:
        return "—"
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M UTC")


def today_view(session: Session, provider: PortfolioDataProvider | None = None) -> dict:
    chosen, snapshot, status = _book(session, provider)
    if snapshot is None or not status["ready"]:
        return _unavailable(status)
    previous = snapshot_before(session, snapshot.snapshot_time, origin=chosen.origin)
    context = build_risk_context(session, snapshot)
    history = chosen.history(session)
    start = snapshot.snapshot_time - timedelta(days=5)
    trades = session.execute(
        select(Trade, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == Trade.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == Trade.account_id)
        .where(Trade.executed_at >= start, Trade.data_origin == chosen.origin)
        .order_by(Trade.executed_at.desc())
    ).all()
    positions = session.execute(
        select(PositionSnapshot, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == PositionSnapshot.account_id)
        .where(
            PositionSnapshot.portfolio_snapshot_id == snapshot.id,
            PositionSnapshot.data_origin == chosen.origin,
        )
        .order_by(PositionSnapshot.market_value_gbp.desc())
    ).all()
    brief_run = session.scalar(
        select(AIRun)
        .where(AIRun.role == AIRole.DAILY_BRIEF, AIRun.status == "completed")
        .order_by(AIRun.created_at.desc())
    )
    brief = None
    if brief_run is not None:
        payload = brief_run.structured_response_json
        brief = {
            "summary": payload.get("summary"),
            "what_changed": payload.get("what_changed") or [],
            "risks": [claim.get("text") for claim in payload.get("portfolio_risks") or []],
            "opportunities": [claim.get("text") for claim in payload.get("opportunities") or []],
            "unknowns": [claim.get("text") for claim in payload.get("missing_information") or []],
            "provider": brief_run.provider,
            "model": brief_run.model,
            "thinking_level": brief_run.thinking_level,
            "analysed_at": _when(brief_run.created_at),
            "data_as_of": (brief_run.input_context_json or {}).get("totals", {}).get("snapshot_time"),
            "cost": str(brief_run.estimated_cost_usd),
            "disclaimer": payload.get("disclaimer"),
            "confidence": payload.get("confidence"),
        }
    return {
        "ready": True,
        "as_of": _when(snapshot.snapshot_time),
        "source": status,
        "mock": chosen.origin is DataOrigin.MOCK,
        "summary": [
            ("Portfolio value", _money(snapshot.total_portfolio_value_gbp)),
            ("Cash", _money(snapshot.cash_gbp)),
            ("Invested", _money(snapshot.invested_value_gbp)),
            ("Daily P&L", f"{_money(snapshot.daily_pnl_gbp)} ({_pct(snapshot.daily_pnl_pct)})"),
            ("Drawdown from high", _pct(snapshot.drawdown_from_ath)),
            ("Max drawdown", _pct(snapshot.max_drawdown_to_date)),
            ("Largest weight", _pct(snapshot.top1_concentration)),
            ("Top 3", _pct(snapshot.top3_concentration)),
            ("Top 5", _pct(snapshot.top5_concentration)),
            ("Realised P&L", _money(snapshot.realised_pnl_gbp)),
        ],
        "positions": [
            {
                "symbol": instrument.symbol,
                "account": account.display_name,
                "quantity": str(position.quantity),
                "value": _money(position.market_value_gbp),
                "weight": _pct(position.portfolio_weight),
                "unrealised": _money(position.unrealised_pnl_gbp),
                "unrealised_pct": _pct(position.unrealised_pnl_pct),
            }
            for position, instrument, account in positions
        ],
        "trades": [
            {
                "time": _when(trade.executed_at),
                "account": account.display_name,
                "symbol": instrument.symbol,
                "action": trade.action.value,
                "quantity": str(trade.quantity),
                "price": f"{trade.fill_price} {trade.trade_currency}",
            }
            for trade, instrument, account in trades
        ],
        "changes": context["changes"],
        "alerts": context["alerts"],
        "brief": brief,
        "value_series": [
            {"date": row.snapshot_time.date().isoformat(), "value": float(row.total_portfolio_value_gbp)}
            for row in history
        ],
        "drawdown_series": [
            {"date": row.snapshot_time.date().isoformat(), "drawdown_pct": float(D(row.drawdown_from_ath) * 100)}
            for row in history
        ],
        "previous_as_of": _when(previous.snapshot_time) if previous else None,
    }


def decision_list(session: Session) -> list[dict]:
    rows = session.execute(
        select(Decision, Instrument, Thesis)
        .join(Instrument, Instrument.id == Decision.instrument_id)
        .join(Thesis, Thesis.id == Decision.thesis_id, isouter=True)
        .order_by(Decision.created_at.desc())
    ).all()
    return [
        {
            "id": str(decision.id),
            "created_at": _when(decision.created_at),
            "symbol": instrument.symbol,
            "decision_type": decision.decision_type.value,
            "status": decision.status.value,
            "thesis": thesis.title if thesis else "—",
            "confidence": str(decision.user_confidence) if decision.user_confidence is not None else "—",
            "reasoning": decision.reasoning,
        }
        for decision, instrument, thesis in rows
    ]


def decision_detail(session: Session, decision_id: str) -> dict | None:
    decision = session.get(Decision, UUID(str(decision_id)))
    if decision is None:
        return None
    instrument = session.get(Instrument, decision.instrument_id)
    thesis = session.get(Thesis, decision.thesis_id) if decision.thesis_id else None
    before = session.get(PortfolioSnapshot, decision.portfolio_before_snapshot_id) if decision.portfolio_before_snapshot_id else None
    after = session.get(PortfolioSnapshot, decision.portfolio_after_snapshot_id) if decision.portfolio_after_snapshot_id else None
    trade = session.get(Trade, decision.linked_trade_id) if decision.linked_trade_id else None
    roles: dict[str, dict] = {}
    for run in analyses_for(session, decision.id):
        roles[run.role.value] = {
            "provider": run.provider,
            "model": run.model,
            "model_version": run.model_version,
            "prompt_version": run.prompt_version,
            "created_at": _when(run.created_at),
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "estimated_cost": str(run.estimated_cost_usd),
            "cost_note": run.cost_note,
            "response": run.structured_response_json,
        }
    return {
        "id": str(decision.id),
        "created_at": _when(decision.created_at),
        "symbol": instrument.symbol if instrument else "—",
        "company_name": instrument.company_name if instrument else "",
        "decision_type": decision.decision_type.value,
        "status": decision.status.value,
        "proposed_action": decision.proposed_action,
        "actual_action": decision.actual_action or "—",
        "reasoning": decision.reasoning,
        "thesis_text": decision.thesis_text,
        "thesis_title": thesis.title if thesis else "—",
        "thesis_status": thesis.status.value if thesis else "—",
        "catalyst": decision.catalyst,
        "expected_horizon": decision.expected_horizon,
        "expected_upside": _pct(decision.expected_upside_pct),
        "expected_downside": _pct(decision.expected_downside_pct),
        "invalidation": decision.invalidation_condition,
        "confidence": str(decision.user_confidence) if decision.user_confidence is not None else "—",
        "trade": None
        if trade is None
        else f"{trade.action.value} {trade.quantity} @ {trade.fill_price} {trade.trade_currency}",
        "before": None
        if before is None
        else {
            "as_of": _when(before.snapshot_time),
            "value": _money(before.total_portfolio_value_gbp),
            "drawdown": _pct(before.drawdown_from_ath),
            "top1": _pct(before.top1_concentration),
            "cash": _money(before.cash_gbp),
        },
        "after": None
        if after is None
        else {
            "as_of": _when(after.snapshot_time),
            "value": _money(after.total_portfolio_value_gbp),
            "drawdown": _pct(after.drawdown_from_ath),
            "top1": _pct(after.top1_concentration),
            "cash": _money(after.cash_gbp),
        },
        "journal": [
            {
                "created_at": _when(entry.created_at),
                "thought": entry.what_i_thought,
                "expected": entry.what_i_expected,
                "wrong": entry.what_would_prove_me_wrong,
                "notes": entry.notes,
            }
            for entry in journal_for(session, decision.id)
        ],
        "outcomes": [
            {
                "horizon": row.horizon_code.value,
                "status": row.status.value,
                "return": _pct(row.instrument_return_pct),
                "notes": row.notes,
            }
            for row in outcomes_for(session, decision.id)
        ],
        "roles": roles,
    }


def risk_view(session: Session, provider: PortfolioDataProvider | None = None) -> dict:
    chosen, snapshot, status = _book(session, provider)
    if snapshot is None or not status["ready"]:
        return _unavailable(status)
    context = build_risk_context(session, snapshot)
    positions = session.execute(
        select(PositionSnapshot, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == PositionSnapshot.account_id)
        .where(
            PositionSnapshot.portfolio_snapshot_id == snapshot.id,
            PositionSnapshot.data_origin == chosen.origin,
        )
    ).all()
    theme_rows = []
    account_rows = []
    shocks = []
    merged: dict[str, Decimal] = {}
    themes: dict[str, str] = {}
    for position, instrument, account in positions:
        theme_rows.append((instrument.theme, D(position.portfolio_weight), D(position.market_value_gbp)))
        account_rows.append((account.display_name, D(position.portfolio_weight), D(position.market_value_gbp)))
        merged[instrument.symbol] = merged.get(instrument.symbol, D(0)) + D(position.portfolio_weight)
        themes[instrument.symbol] = instrument.theme
    account_rows.append(("Cash", D(snapshot.cash_gbp) / D(snapshot.total_portfolio_value_gbp), D(snapshot.cash_gbp)))
    shock_positions = [ShockPosition(symbol, weight, themes[symbol]) for symbol, weight in merged.items()]
    grid = []
    for position in sorted(shock_positions, key=lambda item: item.weight, reverse=True):
        row = {"symbol": position.key, "weight": _pct(position.weight)}
        for shock in (D("-0.10"), D("-0.20"), D("-0.30")):
            result = run_scenario(
                shock_positions,
                ScenarioDefinition(
                    name=f"{position.key} {shock}",
                    assumption="Single name. Other holdings unchanged.",
                    shocks_by_symbol={position.key: shock},
                ),
            )
            row[f"{int(shock * 100)}%"] = _pct(result.portfolio_impact)
        grid.append(row)
    history = chosen.history(session)
    return {
        "ready": True,
        "as_of": _when(snapshot.snapshot_time),
        "source": status,
        "summary": [
            ("Drawdown from high", _pct(snapshot.drawdown_from_ath)),
            ("Max drawdown", _pct(snapshot.max_drawdown_to_date)),
            ("High-water value", _money(snapshot.ath_portfolio_value_gbp)),
            ("Largest weight", _pct(snapshot.top1_concentration)),
            ("Top 3", _pct(snapshot.top3_concentration)),
            ("Top 5", _pct(snapshot.top5_concentration)),
            ("Cash", _money(snapshot.cash_gbp)),
        ],
        "themes": [
            {"theme": line.key, "weight": _pct(line.weight), "value": _money(line.market_value_gbp)}
            for line in exposure_by(theme_rows)
        ],
        "accounts": [
            {"account": line.key, "weight": _pct(line.weight), "value": _money(line.market_value_gbp)}
            for line in exposure_by(account_rows)
        ],
        "scenarios": [
            {
                "name": item["name"],
                "impact": _pct(D(item["portfolio_impact"])),
                "assumption": item["assumption"],
            }
            for item in context["scenarios"]
        ],
        "grid": grid,
        "alerts": context["alerts"],
        "drawdown_series": [
            {"date": row.snapshot_time.date().isoformat(), "drawdown_pct": float(D(row.drawdown_from_ath) * 100)}
            for row in history
        ],
    }


def research_view(session: Session) -> dict:
    instruments = session.scalars(
        select(Instrument).where(Instrument.data_origin == DataOrigin.MOCK).order_by(Instrument.symbol)
    ).all()
    names = {row.id: row.symbol for row in instruments}
    theses = session.scalars(select(Thesis).order_by(Thesis.updated_at.desc())).all()
    return {
        "instruments": [{"id": str(row.id), "symbol": row.symbol, "name": row.company_name, "theme": row.theme} for row in instruments],
        "watchlist": [
            {"symbol": names.get(row.instrument_id, "—"), "status": row.status.value, "notes": row.notes}
            for row in session.scalars(select(WatchlistItem).order_by(WatchlistItem.added_at.desc())).all()
        ],
        "theses": [
            {
                "id": str(row.id),
                "symbol": names.get(row.instrument_id, "—"),
                "title": row.title,
                "status": row.status.value,
                "horizon": row.time_horizon,
                "summary": row.thesis_summary,
                "bull": row.bull_case,
                "base": row.base_case,
                "bear": row.bear_case,
                "invalidation": row.invalidation_conditions,
            }
            for row in theses
        ],
        "catalysts": [
            {
                "symbol": names.get(row.instrument_id, "—"),
                "description": row.description,
                "window": row.expected_window,
                "status": row.status.value,
            }
            for row in session.scalars(select(Catalyst).order_by(Catalyst.created_at.desc())).all()
        ],
        "notes": [
            {
                "title": row.title,
                "symbol": names.get(row.instrument_id, "—") if row.instrument_id else "—",
                "body": row.body,
                "created_at": _when(row.created_at),
            }
            for row in session.scalars(select(ResearchNote).order_by(ResearchNote.created_at.desc())).all()
        ],
    }


def ai_review(session: Session) -> dict:
    runs = session.scalars(select(AIRun).order_by(AIRun.created_at.desc())).all()
    total_cost = sum((D(run.estimated_cost_usd) for run in runs), start=D(0))
    total_in = sum(run.input_tokens for run in runs)
    total_out = sum(run.output_tokens for run in runs)
    return {
        "totals": {
            "runs": len(runs),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "estimated_cost_usd": f"{q8(total_cost):f}",
        },
        "runs": [
            {
                "id": str(run.id),
                "time": _when(run.created_at),
                "trigger": run.trigger.value,
                "role": run.role.value,
                "provider": run.provider,
                "model": run.model,
                "model_version": run.model_version or "—",
                "prompt_version": run.prompt_version,
                "thinking_level": run.thinking_level,
                "status": run.status,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "estimated_cost_usd": f"{run.estimated_cost_usd:f}",
                "cost_note": run.cost_note,
                "decision_id": str(run.decision_id) if run.decision_id else "",
                "response": run.structured_response_json,
                "claims": [
                    {
                        "type": claim.claim_type.value,
                        "confidence": str(claim.confidence),
                        "text": claim.claim_text,
                        "sources": claim.supporting_evidence,
                        "counter": claim.counter_evidence,
                    }
                    for claim in session.scalars(select(AIClaim).where(AIClaim.ai_run_id == run.id)).all()
                ],
            }
            for run in runs
        ],
    }


def form_choices(session: Session) -> dict:
    instruments = session.scalars(
        select(Instrument).where(Instrument.data_origin == DataOrigin.MOCK).order_by(Instrument.symbol)
    ).all()
    names = {row.id: row.symbol for row in instruments}
    theses = session.scalars(select(Thesis).order_by(Thesis.title)).all()
    trades = session.execute(
        select(Trade, Instrument)
        .join(Instrument, Instrument.id == Trade.instrument_id)
        .where(Trade.data_origin == DataOrigin.MOCK)
        .order_by(Trade.executed_at.desc())
    ).all()
    return {
        "instruments": [
            {"id": str(row.id), "symbol": row.symbol, "name": row.company_name} for row in instruments
        ],
        "theses": [
            {"id": str(row.id), "instrument_id": str(row.instrument_id), "symbol": names.get(row.instrument_id, ""), "title": row.title}
            for row in theses
        ],
        "trades": [
            {
                "id": str(trade.id),
                "label": f"{trade.executed_at.date().isoformat()} {instrument.symbol} {trade.action.value} {trade.quantity}",
            }
            for trade, instrument in trades
        ],
    }
