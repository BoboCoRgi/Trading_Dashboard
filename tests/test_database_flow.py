"""Runs against the local database when Postgres is up and the mock book is seeded."""

from uuid import UUID

import pytest
from sqlalchemy import select

from core.enums import AnalysisTrigger, DecisionType
from core.money import D
from db.models import Decision
from db.session import SessionLocal
from services.decisions import DecisionInput, create_decision
from services.portfolio_source import MockPortfolioProvider
from services.queries import ai_review, decision_detail, research_view, risk_view, today_view
from services.triggers import TriggerDispatcher


def _session_or_skip():
    session = SessionLocal()
    try:
        view = today_view(session, MockPortfolioProvider())
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")
    if not view.get("ready"):
        session.close()
        pytest.skip("database is not seeded")
    return session, view


def test_seeded_today_risk_and_research_views():
    session, today = _session_or_skip()
    try:
        assert today["as_of"].endswith("21:00 UTC")
        assert today["positions"]
        assert any(row["symbol"] == "RKLB" for row in today["positions"])
        assert any(row[0] == "Drawdown from high" and row[1].startswith("-") for row in today["summary"])
        risk = risk_view(session, MockPortfolioProvider())
        assert any("RKLB -30%" in item["name"] for item in risk["scenarios"])
        research = research_view(session)
        statuses = {row["status"] for row in research["theses"]}
        assert {"ACTIVE", "WEAKENED", "CLOSED"} <= statuses
        review = ai_review(session)
        assert review["totals"]["runs"] >= 9
        assert "MOCK-T212-ISA" not in str(review)
    finally:
        session.close()


def test_manual_pass_and_mock_debate_do_not_persist_when_rolled_back():
    session, _today = _session_or_skip()
    try:
        before = session.scalar(select(Decision.id))
        instrument = next(row for row in research_view(session)["instruments"] if row["symbol"] == "NOK")
        created = create_decision(
            session,
            DecisionInput(
                instrument_id=UUID(instrument["id"]),
                decision_type=DecisionType.PASS,
                proposed_action="Verification pass.",
                reasoning="Temporary row used to prove the decision service. MOCK DATA.",
                user_confidence=D("0.4"),
                invalidation_condition="A primary source.",
            ),
        )
        result = TriggerDispatcher().emit(
            session,
            AnalysisTrigger.PRE_DECISION_REVIEW,
            instrument_symbol="NOK",
            decision_id=created.id,
        )
        assert result.status == "completed"
        detail = decision_detail(session, str(created.id))
        assert set(detail["roles"]) == {"ANALYST", "SKEPTIC", "RISK_OFFICER", "SYNTHESIS"}
        assert detail["roles"]["SYNTHESIS"]["response"]["is_trading_instruction"] is False
        assert "MOCK-IBKR-ISA" not in str(detail)
        session.rollback()
        assert session.get(Decision, created.id) is None
        assert before is not None
    finally:
        session.rollback()
        session.close()
