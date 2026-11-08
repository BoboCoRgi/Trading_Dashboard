from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.enums import DecisionStatus, DecisionType
from core.money import D
from db.session import get_session, session_scope
from services.decisions import DecisionInput, create_decision
from services.queries import ai_review, decision_detail, decision_list, research_view, risk_view, today_view
from services.triggers import TriggerDispatcher

app = FastAPI(title="Investment Intelligence", version="0.1.0")


class DecisionBody(BaseModel):
    instrument_id: UUID
    decision_type: DecisionType
    proposed_action: str
    reasoning: str
    thesis_id: UUID | None = None
    thesis_text: str = ""
    catalyst: str = ""
    expected_horizon: str = ""
    expected_upside_percent: float | None = None
    expected_downside_percent: float | None = None
    invalidation_condition: str = ""
    user_confidence: float | None = Field(default=None, ge=0, le=1)
    status: DecisionStatus = DecisionStatus.OPEN


class AnalysisBody(BaseModel):
    symbol: str
    decision_id: UUID | None = None
    allow_external: bool = False


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "order_execution": "disabled",
        "broker_mode": "read_only",
        "trading_enabled": False,
    }


@app.get("/today")
def today(session: Session = Depends(get_session)) -> dict:
    return today_view(session)


@app.get("/decisions")
def decisions(session: Session = Depends(get_session)) -> list[dict]:
    return decision_list(session)


@app.get("/decisions/{decision_id}")
def decision(decision_id: UUID, session: Session = Depends(get_session)) -> dict:
    detail = decision_detail(session, str(decision_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return detail


@app.post("/decisions")
def post_decision(body: DecisionBody) -> dict:
    payload = DecisionInput(
        instrument_id=body.instrument_id,
        decision_type=body.decision_type,
        proposed_action=body.proposed_action,
        reasoning=body.reasoning,
        thesis_id=body.thesis_id,
        thesis_text=body.thesis_text,
        catalyst=body.catalyst,
        expected_horizon=body.expected_horizon,
        expected_upside_percent=None if body.expected_upside_percent is None else D(str(body.expected_upside_percent)),
        expected_downside_percent=None
        if body.expected_downside_percent is None
        else D(str(body.expected_downside_percent)),
        invalidation_condition=body.invalidation_condition,
        user_confidence=None if body.user_confidence is None else D(str(body.user_confidence)),
        status=body.status,
    )
    with session_scope() as session:
        row = create_decision(session, payload)
        return {"id": str(row.id), "order_sent": False}


@app.get("/risk")
def risk(session: Session = Depends(get_session)) -> dict:
    return risk_view(session)


@app.get("/research")
def research(session: Session = Depends(get_session)) -> dict:
    return research_view(session)


@app.get("/ai/runs")
def runs(session: Session = Depends(get_session)) -> dict:
    return ai_review(session)


@app.post("/ai/debate")
def debate(body: AnalysisBody) -> dict:
    from core.enums import AnalysisTrigger

    with session_scope() as session:
        result = TriggerDispatcher().emit(
            session,
            AnalysisTrigger.MANUAL_DEEP_DIVE if body.decision_id is None else AnalysisTrigger.PRE_DECISION_REVIEW,
            instrument_symbol=body.symbol,
            decision_id=body.decision_id,
            allow_external=body.allow_external,
        )
    return {"status": result.status, "detail": result.detail, "order_sent": False}
