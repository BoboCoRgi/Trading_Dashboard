"""Decision journal assembly. Pages and the API both use this."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import AIRun, DecisionOutcome, JournalEntry


def journal_for(session: Session, decision_id) -> list[JournalEntry]:
    return list(
        session.scalars(
            select(JournalEntry).where(JournalEntry.decision_id == decision_id).order_by(JournalEntry.created_at)
        ).all()
    )


def outcomes_for(session: Session, decision_id) -> list[DecisionOutcome]:
    return list(
        session.scalars(
            select(DecisionOutcome)
            .where(DecisionOutcome.decision_id == decision_id)
            .order_by(DecisionOutcome.trading_days)
        ).all()
    )


def analyses_for(session: Session, decision_id) -> list[AIRun]:
    return list(
        session.scalars(select(AIRun).where(AIRun.decision_id == decision_id).order_by(AIRun.created_at)).all()
    )
