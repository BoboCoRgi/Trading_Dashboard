"""Detect unusual moves and screen a small research list.

The universe is current holdings plus the watchlist. There is no broad-market
feed, so benchmark fields stay empty and classification stays UNKNOWN.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.events import MoveInput, assess_move, portfolio_fit, zscore
from analytics.outcomes import HORIZONS
from analytics.screens import ScreenInput, screen
from core.config import get_settings
from core.enums import (
    AIRole,
    AnalysisTrigger,
    DataOrigin,
    DecisionType,
    EventAnalysisStatus,
    EventClassification,
    EventTriggerType,
    FitLevel,
    OpportunityStatus,
    OpportunityType,
    OutcomeStatus,
    ResearchPriorityLevel,
    AttentionPriority,
    ThesisStatus,
)
from core.money import D, q2, q8
from db.models import (
    AIRun,
    Instrument,
    MarketEvent,
    OpportunityCandidate,
    OpportunityOutcome,
    PortfolioSnapshot,
    PositionSnapshot,
    PriceObservation,
    Thesis,
    WatchlistItem,
)
from services.decisions import DecisionInput, create_decision
from services.ledger import latest_snapshot, snapshot_before

_KEEP = {
    OpportunityStatus.RESEARCHING,
    OpportunityStatus.WATCHING,
    OpportunityStatus.REJECTED,
    OpportunityStatus.PROMOTED_TO_DECISION,
}
_HORIZON_DAYS = {code: days for code, days in HORIZONS}


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{(D(value) * D(100)).quantize(Decimal('0.01'))}%"


def _money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    amount = q2(D(value))
    sign = "-" if amount < 0 else ""
    return f"{sign}£{abs(amount):,.2f}"


def _sigma(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{D(value).quantize(Decimal('0.1'))}σ"


def refresh_scan(session: Session) -> None:
    settings = get_settings()
    snapshot = latest_snapshot(session)
    if snapshot is None:
        return
    previous = snapshot_before(session, snapshot.snapshot_time)
    instruments = {row.id: row for row in session.scalars(select(Instrument)).all()}
    closes = _closes(session)
    if not closes:
        return
    latest_day = max(rows[-1][0] for rows in closes.values()).date()
    weights = _weights(session, snapshot.id)
    prior_weights = _weights(session, previous.id) if previous else {}
    watch = {
        row.instrument_id
        for row in session.scalars(select(WatchlistItem)).all()
    }
    active_thesis = {
        row.instrument_id: row
        for row in session.scalars(select(Thesis).where(Thesis.status == ThesisStatus.ACTIVE)).all()
    }
    universe = set(weights) | set(watch)
    day_returns: dict[UUID, Decimal] = {}
    for instrument_id in universe:
        series = closes.get(instrument_id) or []
        daily = _return_on(series, latest_day)
        if daily is not None:
            day_returns[instrument_id] = daily

    theme_weight: dict[str, Decimal] = {}
    for instrument_id, weight in weights.items():
        theme = instruments[instrument_id].theme
        theme_weight[theme] = theme_weight.get(theme, D(0)) + weight

    total = D(snapshot.total_portfolio_value_gbp)
    cash_weight = D(snapshot.cash_gbp) / total if total else D(0)
    origin = snapshot.data_origin
    _replace_events(
        session,
        snapshot.snapshot_time,
        instruments,
        closes,
        latest_day,
        day_returns,
        weights,
        prior_weights,
        active_thesis,
        total,
        origin,
        settings,
    )
    _replace_candidates(
        session,
        snapshot.snapshot_time,
        instruments,
        closes,
        latest_day,
        day_returns,
        weights,
        theme_weight,
        cash_weight,
        active_thesis,
        universe,
        origin,
        settings,
    )
    _fill_due_outcomes(session, closes)
    session.flush()


def recent_events(session: Session, limit: int = 8, *, origin: DataOrigin | None = None) -> list[dict]:
    statement = (
        select(MarketEvent, Instrument)
        .join(Instrument, Instrument.id == MarketEvent.instrument_id)
        .order_by(MarketEvent.event_date.desc())
    )
    if origin is not None:
        statement = statement.where(MarketEvent.data_origin == origin)
    rows = session.execute(statement).all()
    ranked = sorted(rows, key=lambda item: _priority_rank(item[0].priority.value))
    cards = []
    for event, instrument in ranked[:limit]:
        thesis = session.get(Thesis, event.linked_thesis_id) if event.linked_thesis_id else None
        cards.append(
            {
                "id": str(event.id),
                "symbol": instrument.symbol,
                "theme": instrument.theme,
                "priority": event.priority.value,
                "price_change": _pct(event.price_change_pct),
                "impact_gbp": _money(event.portfolio_impact_gbp),
                "impact_pct": _pct(event.portfolio_impact_pct),
                "peer": None if event.sector_or_peer_change_pct is None else _pct(event.sector_or_peer_change_pct),
                "abnormality": _sigma(event.abnormality_score),
                "classification": event.event_classification.value,
                "thesis_review": event.analysis_status is EventAnalysisStatus.THESIS_REVIEW,
                "thesis_title": thesis.title if thesis else None,
                "note": event.context_note,
                "triggers": event.trigger_types.replace("_", " ").lower(),
                "linked_decision": event.linked_decision_id is not None,
            }
        )
    return cards


def radar_cards(session: Session, limit: int = 5, *, origin: DataOrigin | None = None) -> list[dict]:
    statement = (
        select(OpportunityCandidate, Instrument)
        .join(Instrument, Instrument.id == OpportunityCandidate.instrument_id)
        .order_by(OpportunityCandidate.detected_at.desc(), OpportunityCandidate.screen_score.desc())
    )
    if origin is not None:
        statement = statement.where(OpportunityCandidate.data_origin == origin)
    rows = session.execute(statement).all()
    cards = []
    for candidate, instrument in rows:
        if len(cards) >= limit:
            break
        cards.append(
            {
                "id": str(candidate.id),
                "symbol": instrument.symbol,
                "type": candidate.opportunity_type.value.replace("_", " ").title(),
                "price_change": _pct(candidate.price_change_pct),
                "relative": None if candidate.relative_change_pct is None else _pct(candidate.relative_change_pct),
                "abnormality": _sigma(candidate.abnormality_score),
                "why": candidate.supporting_evidence,
                "why_market": candidate.counter_evidence,
                "fit": candidate.portfolio_fit.value,
                "fit_reason": candidate.portfolio_fit_reason,
                "priority": candidate.research_priority.value,
                "confidence": candidate.confidence,
                "status": candidate.status.value,
            }
        )
    return cards


def promote_candidate(session: Session, candidate_id: str, decision_type: DecisionType, reasoning: str):
    candidate = session.get(OpportunityCandidate, UUID(str(candidate_id)))
    if candidate is None:
        raise ValueError("unknown candidate")
    if not reasoning.strip():
        raise ValueError("reasoning is required")
    decision = create_decision(
        session,
        DecisionInput(
            instrument_id=candidate.instrument_id,
            decision_type=decision_type,
            proposed_action=f"Record a {decision_type.value} from the opportunity radar.",
            reasoning=reasoning.strip(),
            what_i_thought=reasoning.strip(),
        ),
    )
    candidate.linked_decision_id = decision.id
    candidate.status = OpportunityStatus.PROMOTED_TO_DECISION
    if candidate.linked_market_event_id is not None:
        event = session.get(MarketEvent, candidate.linked_market_event_id)
        if event is not None and event.linked_decision_id is None:
            event.linked_decision_id = decision.id
    return decision


def record_event_decision(session: Session, event_id: str, decision_type: DecisionType, reasoning: str):
    event = session.get(MarketEvent, UUID(str(event_id)))
    if event is None:
        raise ValueError("unknown event")
    if event.linked_decision_id is not None:
        raise ValueError("this event already has a decision")
    if not reasoning.strip():
        raise ValueError("reasoning is required")
    decision = create_decision(
        session,
        DecisionInput(
            instrument_id=event.instrument_id,
            decision_type=decision_type,
            proposed_action=f"Record a {decision_type.value} from a market event.",
            reasoning=reasoning.strip(),
            what_i_thought=reasoning.strip(),
        ),
    )
    event.linked_decision_id = decision.id
    return decision


def attach_event_analysis(session: Session, event_id: str) -> None:
    event = session.get(MarketEvent, UUID(str(event_id)))
    if event is None:
        return
    run = session.scalar(
        select(AIRun)
        .where(
            AIRun.instrument_id == event.instrument_id,
            AIRun.trigger == AnalysisTrigger.LARGE_PRICE_MOVE,
            AIRun.role == AIRole.SYNTHESIS,
        )
        .order_by(AIRun.created_at.desc())
    )
    if run is not None:
        event.linked_ai_run_id = run.id


def _priority_rank(level: str) -> int:
    return {"CRITICAL": 0, "HIGH": 1, "WATCH": 2, "INFO": 3}.get(level, 9)


def _closes(session: Session) -> dict[UUID, list[tuple[datetime, Decimal]]]:
    grouped: dict[UUID, list[tuple[datetime, Decimal]]] = {}
    rows = session.scalars(select(PriceObservation).order_by(PriceObservation.as_of)).all()
    for row in rows:
        grouped.setdefault(row.instrument_id, []).append((row.as_of, D(row.price)))
    return grouped


def _weights(session: Session, snapshot_id) -> dict[UUID, Decimal]:
    totals: dict[UUID, Decimal] = {}
    rows = session.scalars(select(PositionSnapshot).where(PositionSnapshot.portfolio_snapshot_id == snapshot_id)).all()
    for row in rows:
        totals[row.instrument_id] = totals.get(row.instrument_id, D(0)) + D(row.portfolio_weight)
    return totals


def _return_on(series: list[tuple[datetime, Decimal]], day) -> Decimal | None:
    points = [(stamp.date(), price) for stamp, price in series]
    current = next((price for stamp, price in reversed(points) if stamp == day), None)
    prior = [price for stamp, price in points if stamp < day]
    if current is None or not prior or prior[-1] == 0:
        return None
    return current / prior[-1] - 1


def _prior_returns(series: list[tuple[datetime, Decimal]], day) -> tuple[Decimal, ...]:
    points = [(stamp.date(), price) for stamp, price in series if stamp.date() <= day]
    returns = []
    for index in range(1, len(points) - 1):
        previous = points[index - 1][1]
        if previous == 0:
            continue
        returns.append(points[index][1] / previous - 1)
    return tuple(returns[-60:])


def _drawdown(series: list[tuple[datetime, Decimal]], day) -> Decimal | None:
    window = [price for stamp, price in series if stamp.date() <= day][-20:]
    if not window:
        return None
    high = max(window)
    if high == 0:
        return None
    return window[-1] / high - 1


def _peer_return(instrument_id, instruments, day_returns) -> Decimal | None:
    theme = instruments[instrument_id].theme
    peers = [
        value
        for other_id, value in day_returns.items()
        if other_id != instrument_id and instruments[other_id].theme == theme
    ]
    if not peers:
        return None
    return sum(peers, D(0)) / D(len(peers))


def _replace_events(session, when, instruments, closes, latest_day, day_returns, weights, prior_weights, active_thesis, total, origin, settings):
    existing = {
        row.instrument_id: row
        for row in session.scalars(select(MarketEvent).where(MarketEvent.event_date == when)).all()
    }
    locked = {instrument_id for instrument_id, row in existing.items() if row.linked_decision_id or row.linked_ai_run_id}
    seen = set()
    for instrument_id, daily in day_returns.items():
        if instrument_id in locked:
            continue
        weight = prior_weights.get(instrument_id, weights.get(instrument_id, D(0)))
        assessment = assess_move(
            MoveInput(daily, _prior_returns(closes[instrument_id], latest_day), weight, total),
            abs_threshold=settings.move_abs_threshold,
            impact_threshold=settings.move_impact_threshold,
            z_threshold=settings.move_z_threshold,
            critical_impact=settings.move_critical_impact,
        )
        if assessment is None:
            continue
        seen.add(instrument_id)
        peer = _peer_return(instrument_id, instruments, day_returns)
        thesis = active_thesis.get(instrument_id)
        review = thesis is not None and assessment.priority in {"HIGH", "CRITICAL"}
        primary = assessment.triggers[0]
        if "PORTFOLIO_IMPACT" in assessment.triggers:
            primary = "PORTFOLIO_IMPACT"
        values = dict(
            price_change_pct=q8(daily),
            portfolio_impact_gbp=q8(assessment.portfolio_impact_gbp),
            portfolio_impact_pct=q8(assessment.portfolio_impact_pct),
            abnormality_score=None if assessment.abnormality is None else q8(assessment.abnormality),
            trigger_type=EventTriggerType(primary),
            trigger_types=", ".join(assessment.triggers),
            sector_or_peer_change_pct=None if peer is None else q8(peer),
            event_classification=EventClassification.UNKNOWN,
            priority=AttentionPriority(assessment.priority),
            analysis_status=EventAnalysisStatus.THESIS_REVIEW if review else EventAnalysisStatus.NOT_REVIEWED,
            linked_thesis_id=thesis.id if review else None,
            context_note=_context_note(instruments[instrument_id].theme, peer),
            data_origin=origin if origin is DataOrigin.MOCK else DataOrigin.LIVE,
        )
        row = existing.get(instrument_id)
        if row is None:
            session.add(MarketEvent(instrument_id=instrument_id, detected_at=datetime.now(timezone.utc), event_date=when, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    for instrument_id, row in existing.items():
        if instrument_id not in locked and instrument_id not in seen:
            session.delete(row)
    session.flush()


def _context_note(theme: str, peer: Decimal | None) -> str:
    if peer is None:
        return "No held peer in the same theme, and no market benchmark is stored. Classification stays unknown."
    return (
        f"Other holdings in {theme} moved {_pct(peer)} the same day. "
        "No index is stored, so this is context only and the classification stays unknown."
    )


def _replace_candidates(session, when, instruments, closes, latest_day, day_returns, weights, theme_weight, cash_weight, active_thesis, universe, origin, settings):
    existing = {
        row.instrument_id: row
        for row in session.scalars(select(OpportunityCandidate).where(OpportunityCandidate.detected_at == when)).all()
    }
    kept = {instrument_id for instrument_id, row in existing.items() if row.status in _KEEP}
    found = []
    for instrument_id in universe:
        if instrument_id not in instruments or instrument_id in kept:
            continue
        series = closes.get(instrument_id) or []
        if not series:
            continue
        daily = day_returns.get(instrument_id)
        peer = _peer_return(instrument_id, instruments, day_returns) if daily is not None else None
        relative = None if daily is None or peer is None else daily - peer
        abnormality = zscore(daily, _prior_returns(series, latest_day)) if daily is not None else None
        result = screen(
            ScreenInput(
                instruments[instrument_id].symbol,
                daily,
                _drawdown(series, latest_day),
                peer,
                abnormality,
                instrument_id in active_thesis,
            ),
            drawdown_limit=settings.screen_drawdown,
            peer_gap=settings.screen_peer_gap,
            z_threshold=settings.move_z_threshold,
        )
        if result.opportunity_type is None:
            continue
        weight = weights.get(instrument_id, D(0))
        fit, fit_reason = portfolio_fit(
            weight,
            theme_weight.get(instruments[instrument_id].theme, D(0)),
            cash_weight,
            settings.cash_weight_alert,
        )
        priority = "HIGH" if result.score >= 2 or (fit != "LOW" and result.score >= 1) else "MEDIUM"
        if fit == "LOW" and result.score < 2:
            priority = "LOW"
        drawdown = _drawdown(series, latest_day)
        found.append((result.score, abs(drawdown or D(0)), instrument_id, result, daily, relative, abnormality, fit, fit_reason, priority, series))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    events = {
        row.instrument_id: row.id
        for row in session.scalars(select(MarketEvent).where(MarketEvent.event_date == when)).all()
    }
    chosen = {item[2] for item in found[:5]}
    for instrument_id, row in existing.items():
        if instrument_id in kept or instrument_id in chosen:
            continue
        for outcome in session.scalars(select(OpportunityOutcome).where(OpportunityOutcome.candidate_id == row.id)).all():
            session.delete(outcome)
        session.delete(row)
    session.flush()
    for score, _gap, instrument_id, result, daily, relative, abnormality, fit, fit_reason, priority, series in found[:5]:
        price = series[-1][1] if series else None
        values = dict(
            opportunity_type=OpportunityType(result.opportunity_type),
            screen_score=q8(score),
            price_change_pct=None if daily is None else q8(daily),
            relative_change_pct=None if relative is None else q8(relative),
            abnormality_score=None if abnormality is None else q8(abnormality),
            reason_flagged=", ".join(hit.code for hit in result.hits),
            supporting_evidence=" ".join(hit.reason for hit in result.hits),
            counter_evidence=(
                "No news or filing is attached. A lower price can mean the market sees worse fundamentals. "
                "That possibility is not ruled out."
            ),
            portfolio_fit=FitLevel(fit),
            portfolio_fit_reason=fit_reason,
            research_priority=ResearchPriorityLevel(priority),
            linked_market_event_id=events.get(instrument_id),
            data_origin=origin if origin is DataOrigin.MOCK else DataOrigin.LIVE,
        )
        candidate = existing.get(instrument_id)
        if candidate is not None and candidate.status in _KEEP:
            continue
        if candidate is None:
            candidate = OpportunityCandidate(
                instrument_id=instrument_id,
                detected_at=when,
                confidence="LOW",
                status=OpportunityStatus.NEW,
                **values,
            )
            session.add(candidate)
            session.flush()
            for horizon, _days in _HORIZON_DAYS.items():
                session.add(
                    OpportunityOutcome(
                        candidate_id=candidate.id,
                        horizon_code=horizon,
                        status=OutcomeStatus.PENDING,
                        price_at_detection=None if price is None else q8(price),
                        data_origin=candidate.data_origin,
                    )
                )
        else:
            for key, value in values.items():
                setattr(candidate, key, value)


def _fill_due_outcomes(session: Session, closes: dict) -> None:
    pending = session.scalars(select(OpportunityOutcome).where(OpportunityOutcome.status == OutcomeStatus.PENDING)).all()
    for outcome in pending:
        candidate = session.get(OpportunityCandidate, outcome.candidate_id)
        if candidate is None or outcome.price_at_detection is None:
            continue
        series = closes.get(candidate.instrument_id) or []
        later = [price for stamp, price in series if stamp > candidate.detected_at]
        needed = _HORIZON_DAYS[outcome.horizon_code]
        if len(later) < needed:
            continue
        window = later[:needed]
        end = window[-1]
        start = D(outcome.price_at_detection)
        if start == 0:
            continue
        returns = [price / start - 1 for price in window]
        outcome.return_pct = q8(end / start - 1)
        outcome.max_forward_gain = q8(max(returns))
        outcome.max_forward_drawdown = q8(min(returns))
        outcome.status = OutcomeStatus.EVALUATED
