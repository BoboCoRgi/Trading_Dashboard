from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.outcomes import decision_day_from, price_return_markers
from core.enums import DataOrigin, DecisionStatus, DecisionType, OutcomeHorizon
from core.money import D
from db.models import Decision, DecisionOutcome, Instrument, JournalEntry, PriceObservation, Thesis
from services.ledger import latest_snapshot, snapshot_after, snapshot_before

HORIZON_DAYS = {
    OutcomeHorizon.D1: 1,
    OutcomeHorizon.D5: 5,
    OutcomeHorizon.D20: 20,
    OutcomeHorizon.D60: 60,
}


@dataclass
class DecisionInput:
    instrument_id: UUID
    decision_type: DecisionType
    proposed_action: str
    reasoning: str
    thesis_id: UUID | None = None
    thesis_text: str = ""
    catalyst: str = ""
    expected_horizon: str = ""
    expected_upside_percent: Decimal | None = None
    expected_downside_percent: Decimal | None = None
    invalidation_condition: str = ""
    user_confidence: Decimal | None = None
    actual_action: str | None = None
    linked_trade_id: UUID | None = None
    what_i_thought: str = ""
    what_i_expected: str = ""
    what_would_prove_me_wrong: str = ""
    journal_notes: str = ""
    status: DecisionStatus = DecisionStatus.OPEN
    created_at: datetime | None = None
    data_origin: DataOrigin = DataOrigin.MOCK


def _fraction(percent: Decimal | None) -> Decimal | None:
    if percent is None:
        return None
    return D(percent) / D(100)


def _check(payload: DecisionInput) -> None:
    if payload.user_confidence is not None and not D(0) <= D(payload.user_confidence) <= D(1):
        raise ValueError("confidence must be between 0 and 1")
    if not payload.proposed_action.strip():
        raise ValueError("proposed action is required")
    if not payload.reasoning.strip():
        raise ValueError("reasoning is required")


def _outcome_rows(session: Session, decision: Decision) -> None:
    prices = session.scalars(
        select(PriceObservation)
        .where(PriceObservation.instrument_id == decision.instrument_id)
        .order_by(PriceObservation.as_of)
    ).all()
    closes = {row.as_of.date(): D(row.price) for row in prices}
    for marker in price_return_markers(decision_day_from(decision.created_at), closes):
        session.add(
            DecisionOutcome(
                decision_id=decision.id,
                horizon_code=marker.horizon,
                trading_days=marker.trading_days,
                status=marker.status,
                evaluated_on=datetime.combine(marker.evaluated_on, datetime.min.time(), tzinfo=timezone.utc)
                if marker.evaluated_on
                else None,
                start_price=marker.start_price,
                end_price=marker.end_price,
                instrument_return_pct=marker.instrument_return_pct,
                method="price_return_marker",
                notes=marker.notes,
                data_origin=decision.data_origin,
            )
        )


def create_decision(session: Session, payload: DecisionInput) -> Decision:
    """Record a human decision. This never submits an order."""
    _check(payload)
    instrument = session.get(Instrument, payload.instrument_id)
    if instrument is None:
        raise ValueError("unknown instrument")
    if payload.thesis_id is not None:
        thesis = session.get(Thesis, payload.thesis_id)
        if thesis is None or thesis.instrument_id != instrument.id:
            raise ValueError("thesis does not belong to this instrument")
    created_at = payload.created_at or datetime.now(timezone.utc)
    before = snapshot_before(session, created_at) or latest_snapshot(session)
    after = None
    if payload.linked_trade_id is not None:
        after = snapshot_after(session, created_at)
    decision = Decision(
        created_at=created_at,
        updated_at=created_at,
        instrument_id=instrument.id,
        thesis_id=payload.thesis_id,
        decision_type=payload.decision_type,
        proposed_action=payload.proposed_action.strip(),
        actual_action=payload.actual_action,
        linked_trade_id=payload.linked_trade_id,
        portfolio_before_snapshot_id=before.id if before else None,
        portfolio_after_snapshot_id=after.id if after else None,
        thesis_text=payload.thesis_text.strip(),
        catalyst=payload.catalyst.strip(),
        expected_horizon=payload.expected_horizon.strip(),
        expected_upside_pct=_fraction(payload.expected_upside_percent),
        expected_downside_pct=_fraction(payload.expected_downside_percent),
        invalidation_condition=payload.invalidation_condition.strip(),
        user_confidence=payload.user_confidence,
        reasoning=payload.reasoning.strip(),
        status=payload.status,
        data_origin=payload.data_origin,
    )
    session.add(decision)
    session.flush()
    session.add(
        JournalEntry(
            decision_id=decision.id,
            created_at=created_at,
            what_i_thought=payload.what_i_thought.strip() or payload.reasoning.strip(),
            what_i_expected=payload.what_i_expected.strip(),
            what_would_prove_me_wrong=payload.what_would_prove_me_wrong.strip() or payload.invalidation_condition.strip(),
            notes=payload.journal_notes.strip(),
            data_origin=payload.data_origin,
        )
    )
    _outcome_rows(session, decision)
    return decision


def update_decision(session: Session, decision_id: UUID, payload: DecisionInput) -> Decision:
    decision = session.get(Decision, decision_id)
    if decision is None:
        raise ValueError("unknown decision")
    _check(payload)
    if payload.thesis_id is not None:
        thesis = session.get(Thesis, payload.thesis_id)
        if thesis is None or thesis.instrument_id != payload.instrument_id:
            raise ValueError("thesis does not belong to this instrument")
    decision.instrument_id = payload.instrument_id
    decision.thesis_id = payload.thesis_id
    decision.decision_type = payload.decision_type
    decision.proposed_action = payload.proposed_action.strip()
    decision.actual_action = payload.actual_action
    decision.linked_trade_id = payload.linked_trade_id
    decision.thesis_text = payload.thesis_text.strip()
    decision.catalyst = payload.catalyst.strip()
    decision.expected_horizon = payload.expected_horizon.strip()
    decision.expected_upside_pct = _fraction(payload.expected_upside_percent)
    decision.expected_downside_pct = _fraction(payload.expected_downside_percent)
    decision.invalidation_condition = payload.invalidation_condition.strip()
    decision.user_confidence = payload.user_confidence
    decision.reasoning = payload.reasoning.strip()
    decision.status = payload.status
    decision.updated_at = datetime.now(timezone.utc)
    session.add(
        JournalEntry(
            decision_id=decision.id,
            what_i_thought=payload.what_i_thought.strip() or payload.reasoning.strip(),
            what_i_expected=payload.what_i_expected.strip() or "Updated decision.",
            what_would_prove_me_wrong=payload.what_would_prove_me_wrong.strip() or payload.invalidation_condition.strip(),
            notes="Decision edited. Earlier journal entries are unchanged. MOCK DATA" if payload.data_origin is DataOrigin.MOCK else "Decision edited. Earlier journal entries are unchanged.",
            data_origin=payload.data_origin,
        )
    )
    return decision
