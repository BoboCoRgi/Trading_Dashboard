from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.context import minimise_context, sanitize_context
from ai.debate import run_brief, run_debate
from ai.providers.base import AIProvider
from core.enums import AnalysisTrigger, DataOrigin
from db.models import AIRun, Decision, Instrument, Thesis
from services.ai_store import store_role_runs
from services.ledger import build_risk_context, latest_snapshot


def _base_context(session: Session, instrument_id: UUID | None, decision_id: UUID | None) -> tuple[dict, object]:
    snapshot = latest_snapshot(session)
    if snapshot is None:
        raise RuntimeError("no portfolio snapshot is stored")
    context = build_risk_context(session, snapshot)
    if instrument_id is not None:
        instrument = session.get(Instrument, instrument_id)
        if instrument is None:
            raise ValueError("unknown instrument")
        context["instrument"] = {
            "symbol": instrument.symbol,
            "company_name": instrument.company_name,
            "theme": instrument.theme,
        }
        thesis = session.scalar(
            select(Thesis).where(Thesis.instrument_id == instrument.id).order_by(Thesis.updated_at.desc())
        )
        if thesis is not None:
            context["thesis"] = {
                "title": thesis.title,
                "status": thesis.status.value,
                "summary": thesis.thesis_summary,
                "bull_case": thesis.bull_case,
                "base_case": thesis.base_case,
                "bear_case": thesis.bear_case,
                "invalidation_conditions": thesis.invalidation_conditions,
                "updated_at": thesis.updated_at.isoformat() if thesis.updated_at else None,
            }
    from services.scan import radar_cards, recent_events

    context["market_events"] = recent_events(session, limit=5)
    context["opportunity_candidates"] = radar_cards(session, limit=5)
    if decision_id is not None:
        decision = session.get(Decision, decision_id)
        if decision is None:
            raise ValueError("unknown decision")
        context["decision"] = {
            "decision_type": decision.decision_type.value,
            "proposed_action": decision.proposed_action,
            "reasoning": decision.reasoning,
            "invalidation_condition": decision.invalidation_condition,
            "status": decision.status.value,
            "created_at": decision.created_at.isoformat(),
        }
        instrument_id = decision.instrument_id
    context["recent_decisions"] = _recent_decisions(session)
    safe = sanitize_context(minimise_context(context))
    return safe, snapshot


def _recent_decisions(session: Session) -> list[dict]:
    rows = session.execute(
        select(Decision, Instrument).join(Instrument, Instrument.id == Decision.instrument_id).order_by(Decision.created_at.desc()).limit(5)
    ).all()
    summaries = []
    for decision, instrument in rows:
        reasoning = (decision.reasoning or "").strip()
        if len(reasoning) > 240:
            reasoning = reasoning[:240].rstrip() + "..."
        summaries.append(
            {
                "symbol": instrument.symbol,
                "decision_type": decision.decision_type.value,
                "status": decision.status.value,
                "created_at": decision.created_at.isoformat() if decision.created_at else None,
                "reasoning": reasoning,
            }
        )
    return summaries


def run_and_store(
    session: Session,
    provider: AIProvider,
    trigger: AnalysisTrigger,
    *,
    instrument_id: UUID | None = None,
    decision_id: UUID | None = None,
    daily: bool = False,
) -> list[AIRun]:
    context, snapshot = _base_context(session, instrument_id, decision_id)
    if decision_id is not None and instrument_id is None:
        instrument_id = session.get(Decision, decision_id).instrument_id
    thesis_id = None
    if instrument_id is not None:
        thesis = session.scalar(
            select(Thesis).where(Thesis.instrument_id == instrument_id).order_by(Thesis.updated_at.desc())
        )
        thesis_id = thesis.id if thesis else None
    runs = run_brief(provider, context) if daily else run_debate(provider, context)
    origin = DataOrigin.MOCK if provider.provider_name.startswith("mock") or snapshot.data_origin is DataOrigin.MOCK else DataOrigin.LIVE
    if provider.provider_name.startswith("mock"):
        origin = DataOrigin.MOCK
    return store_role_runs(
        session,
        runs,
        trigger=trigger,
        context=context,
        snapshot_id=snapshot.id,
        decision_id=decision_id,
        instrument_id=instrument_id,
        thesis_id=thesis_id,
        data_origin=origin,
    )
