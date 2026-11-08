"""Read-only rows for charts.

These functions do not write, and they do not replace the risk engine.
Position contribution is the stored price change on shares that existed in
both of the last two snapshots. Buys and sells are left out.
"""

from datetime import timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.events import rebase_to_start
from core.enums import AIRole, AnalysisTrigger
from core.money import D
from db.models import (
    AIRun,
    BrokerAccount,
    Decision,
    DecisionOutcome,
    Instrument,
    MarketEvent,
    OpportunityCandidate,
    OpportunityOutcome,
    PortfolioSnapshot,
    PositionSnapshot,
    PriceObservation,
    Thesis,
)
from services.portfolio_source import PortfolioDataProvider, portfolio_provider


def _provider(provider: PortfolioDataProvider | None) -> PortfolioDataProvider:
    return provider or portfolio_provider()


def snapshot_history(session: Session, provider: PortfolioDataProvider | None = None) -> list[dict]:
    rows = _provider(provider).history(session)
    history = []
    for row in rows:
        total = D(row.total_portfolio_value_gbp)
        cash_weight = (D(row.cash_gbp) / total * 100) if total else D(0)
        history.append(
            {
                "date": row.snapshot_time.date().isoformat(),
                "value": float(total),
                "daily_pnl": None if row.daily_pnl_gbp is None else float(D(row.daily_pnl_gbp)),
                "drawdown": float(D(row.drawdown_from_ath) * 100),
                "top1": float(D(row.top1_concentration) * 100),
                "cash_weight": float(cash_weight),
            }
        )
    return history


def _positions(session: Session, snapshot_id: UUID):
    return session.execute(
        select(PositionSnapshot, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == PositionSnapshot.account_id)
        .where(PositionSnapshot.portfolio_snapshot_id == snapshot_id)
    ).all()


def _origin_positions(session: Session, snapshot_id: UUID, origin):
    return session.execute(
        select(PositionSnapshot, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == PositionSnapshot.account_id)
        .where(
            PositionSnapshot.portfolio_snapshot_id == snapshot_id,
            PositionSnapshot.data_origin == origin,
        )
    ).all()


def allocation_rows(session: Session, provider: PortfolioDataProvider | None = None) -> list[dict]:
    chosen = _provider(provider)
    snapshot = chosen.latest_snapshot(session)
    if snapshot is None:
        return []
    merged: dict[str, dict] = {}
    for position, instrument, _account in _origin_positions(session, snapshot.id, chosen.origin):
        bucket = merged.setdefault(instrument.symbol, {"weight": D(0), "value": D(0)})
        bucket["weight"] += D(position.portfolio_weight)
        bucket["value"] += D(position.market_value_gbp)
    total = D(snapshot.total_portfolio_value_gbp)
    rows = [
        {
            "label": symbol,
            "weight": float(bucket["weight"] * 100),
            "value": float(bucket["value"]),
            "kind": "position",
        }
        for symbol, bucket in merged.items()
    ]
    if total:
        rows.append(
            {
                "label": "Cash",
                "weight": float(D(snapshot.cash_gbp) / total * 100),
                "value": float(D(snapshot.cash_gbp)),
                "kind": "cash",
            }
        )
    rows.sort(key=lambda item: item["weight"], reverse=True)
    return rows


def contribution_rows(session: Session, provider: PortfolioDataProvider | None = None) -> dict:
    chosen = _provider(provider)
    snaps = list(reversed(chosen.history(session)))
    if len(snaps) < 2:
        return {"ready": False, "by_symbol": [], "by_position": []}
    current, previous = snaps[0], snaps[1]
    prior = {}
    for position, instrument, account in _origin_positions(session, previous.id, chosen.origin):
        prior[(instrument.symbol, account.display_name)] = position
    by_position = []
    for position, instrument, account in _origin_positions(session, current.id, chosen.origin):
        key = (instrument.symbol, account.display_name)
        old = prior.get(key)
        if old is None or position.fx_rate_to_gbp is None or old.fx_rate_to_gbp is None:
            continue
        qty = min(D(old.quantity), D(position.quantity))
        if qty <= 0:
            continue
        gbp = qty * (
            D(position.market_price) * D(position.fx_rate_to_gbp)
            - D(old.market_price) * D(old.fx_rate_to_gbp)
        )
        by_position.append(
            {
                "symbol": instrument.symbol,
                "account": account.display_name,
                "gbp": float(gbp),
            }
        )
    totals: dict[str, float] = {}
    for row in by_position:
        totals[row["symbol"]] = totals.get(row["symbol"], 0.0) + row["gbp"]
    by_symbol = [{"symbol": symbol, "gbp": amount} for symbol, amount in totals.items()]
    by_symbol.sort(key=lambda item: abs(item["gbp"]), reverse=True)
    return {"ready": bool(by_symbol), "by_symbol": by_symbol, "by_position": by_position}


def decision_chart_data(session: Session, decision_id: str) -> dict:
    decision = session.get(Decision, UUID(str(decision_id)))
    if decision is None:
        return {"ready": False}
    instrument = session.get(Instrument, decision.instrument_id)
    symbol = instrument.symbol if instrument else "—"

    def side(snapshot_id: UUID | None) -> dict | None:
        if snapshot_id is None:
            return None
        snapshot = session.get(PortfolioSnapshot, snapshot_id)
        if snapshot is None:
            return None
        total = D(snapshot.total_portfolio_value_gbp)
        weight = D(0)
        for position, held, _account in _positions(session, snapshot.id):
            if held.symbol == symbol:
                weight += D(position.portfolio_weight)
        return {
            "weight": float(weight * 100),
            "cash_weight": float((D(snapshot.cash_gbp) / total * 100) if total else 0),
            "top1": float(D(snapshot.top1_concentration) * 100),
            "value": float(total),
        }

    prices = session.scalars(
        select(PriceObservation)
        .where(PriceObservation.instrument_id == decision.instrument_id)
        .order_by(PriceObservation.as_of)
    ).all()
    created = decision.created_at.astimezone(timezone.utc).date() if decision.created_at.tzinfo else decision.created_at.date()
    path = [
        {
            "date": row.as_of.date().isoformat(),
            "price": float(D(row.price)),
            "currency": row.currency,
        }
        for row in prices
    ]
    markers = []
    for outcome in session.scalars(select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)).all():
        if outcome.status.value != "EVALUATED" or outcome.evaluated_on is None or outcome.instrument_return_pct is None:
            continue
        markers.append(
            {
                "horizon": outcome.horizon_code.value,
                "date": outcome.evaluated_on.date().isoformat(),
                "return_pct": float(D(outcome.instrument_return_pct) * 100),
            }
        )
    return {
        "ready": True,
        "symbol": symbol,
        "decision_type": decision.decision_type.value,
        "created_on": created.isoformat(),
        "upside": None if decision.expected_upside_pct is None else float(D(decision.expected_upside_pct) * 100),
        "downside": None if decision.expected_downside_pct is None else float(D(decision.expected_downside_pct) * 100),
        "confidence": None if decision.user_confidence is None else float(D(decision.user_confidence)),
        "before": side(decision.portfolio_before_snapshot_id),
        "after": side(decision.portfolio_after_snapshot_id),
        "prices": path,
        "markers": markers,
    }


def _aligned_rebase(series: list[tuple], dates: list) -> list[dict] | None:
    by_date = {stamp.date(): D(price) for stamp, price in series}
    overlap = [(day, by_date[day]) for day in dates if day in by_date]
    if len(overlap) < 2:
        return None
    rebased = rebase_to_start([price for _day, price in overlap])
    if rebased is None:
        return None
    return [
        {"date": day.isoformat(), "value": float(value)}
        for (day, _price), value in zip(overlap, rebased, strict=True)
    ]


def event_context(
    session: Session,
    event_id: str,
    provider: PortfolioDataProvider | None = None,
) -> dict | None:
    event = session.get(MarketEvent, UUID(str(event_id)))
    if event is None:
        return None
    instrument = session.get(Instrument, event.instrument_id)
    if instrument is None:
        return None
    prices = session.scalars(
        select(PriceObservation)
        .where(PriceObservation.instrument_id == instrument.id)
        .order_by(PriceObservation.as_of)
    ).all()
    day = event.event_date.astimezone(timezone.utc).date() if event.event_date.tzinfo else event.event_date.date()
    window = [row for row in prices if row.as_of.date() <= day][-63:]
    price_points = [{"date": row.as_of.date().isoformat(), "price": float(D(row.price))} for row in window]
    dates = [row.as_of.date() for row in window]
    relative = []
    relative_note = "Requires market data integration for an index or sector benchmark."
    own = _aligned_rebase([(row.as_of, row.price) for row in window], dates)
    if own:
        relative.append({"name": instrument.symbol, "points": own})
    peers = session.scalars(select(Instrument).where(Instrument.theme == instrument.theme, Instrument.id != instrument.id)).all()
    for peer in peers:
        peer_prices = session.scalars(
            select(PriceObservation).where(PriceObservation.instrument_id == peer.id).order_by(PriceObservation.as_of)
        ).all()
        line = _aligned_rebase([(row.as_of, row.price) for row in peer_prices], dates)
        if line:
            relative.append({"name": peer.symbol, "points": line})
    if len(relative) > 1:
        relative_note = (
            f"Held names in {instrument.theme}, each rebased to 100 on the first shared close. "
            "This compares paths. It does not identify a cause. No index is stored."
        )
    elif relative:
        relative_note = "No other holding shares this theme. " + relative_note

    thesis_row = session.get(Thesis, event.linked_thesis_id) if event.linked_thesis_id else None
    if thesis_row is None:
        thesis_row = session.scalar(
            select(Thesis).where(Thesis.instrument_id == instrument.id).order_by(Thesis.updated_at.desc())
        )
    thesis = None
    if thesis_row is not None:
        thesis = {
            "title": thesis_row.title,
            "stored_status": thesis_row.status.value,
            "overall": "UNCERTAIN",
            "summary": thesis_row.thesis_summary,
            "invalidation": thesis_row.invalidation_conditions,
            "note": "Separate thesis pillars are not stored. The stored status was not changed because the price moved.",
        }

    runs = session.scalars(
        select(AIRun)
        .where(
            AIRun.instrument_id == instrument.id,
            AIRun.trigger == AnalysisTrigger.LARGE_PRICE_MOVE,
            AIRun.role.in_([AIRole.ANALYST, AIRole.SKEPTIC, AIRole.RISK_OFFICER, AIRole.SYNTHESIS]),
        )
        .order_by(AIRun.created_at.desc())
    ).all()
    picked: dict[str, AIRun] = {}
    for run in runs:
        role = run.role.value
        if role not in picked:
            picked[role] = run
    analyses = [
        {
            "role": role,
            "response": run.structured_response_json,
            "meta": f"{run.provider} · {run.model}",
        }
        for role, run in picked.items()
    ]
    when = event.event_date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if event.event_date.tzinfo else str(event.event_date)
    return {
        "id": str(event.id),
        "symbol": instrument.symbol,
        "prices": price_points,
        "relative": relative,
        "relative_note": relative_note,
        "contribution": None,
        "timeline": [
            {
                "when": when,
                "text": f"{instrument.symbol} close stored. No intraday print, news, or filing is stored.",
            }
        ],
        "thesis": thesis,
        "evidence": event.context_note,
        "counter": "No news or filing is attached. A price move can have a cause that is not in this database.",
        "would_change": [
            "A primary source that explains the move.",
            "An index or sector series, so the classification can leave unknown.",
            "Evidence that meets or breaks the stored invalidation condition.",
        ],
        "analyses": analyses,
        "linked_decision": event.linked_decision_id is not None,
    }


def outcome_board(session: Session) -> list[dict]:
    rows = session.execute(
        select(OpportunityCandidate, Instrument)
        .join(Instrument, Instrument.id == OpportunityCandidate.instrument_id)
        .order_by(OpportunityCandidate.detected_at.desc())
    ).all()
    board = []
    for candidate, instrument in rows:
        outcomes = session.scalars(
            select(OpportunityOutcome).where(OpportunityOutcome.candidate_id == candidate.id)
        ).all()
        decision = "Not recorded"
        if candidate.linked_decision_id is not None:
            linked = session.get(Decision, candidate.linked_decision_id)
            if linked is not None:
                decision = linked.decision_type.value
        horizons = []
        horizon_order = {"D1": 1, "D5": 5, "D20": 20, "D60": 60}
        outcomes = sorted(outcomes, key=lambda item: horizon_order.get(item.horizon_code.value, 99))
        for outcome in outcomes:
            if outcome.return_pct is None:
                horizons.append(f"{outcome.horizon_code.value} awaiting later closes")
            else:
                horizons.append(f"{outcome.horizon_code.value} {float(D(outcome.return_pct) * 100):.1f}%")
        board.append(
            {
                "symbol": instrument.symbol,
                "detected": candidate.detected_at.date().isoformat() if candidate.detected_at else "—",
                "type": candidate.opportunity_type.value.replace("_", " ").title(),
                "status": candidate.status.value.replace("_", " ").title(),
                "decision": decision,
                "horizons": "; ".join(horizons) if horizons else "Awaiting later closes",
            }
        )
    return board


def thesis_questions(session: Session) -> dict[str, str]:
    return {str(row.id): row.catalysts for row in session.scalars(select(Thesis)).all()}


def expectation_points(session: Session) -> list[dict]:
    rows = session.execute(
        select(Decision, Instrument).join(Instrument, Instrument.id == Decision.instrument_id).order_by(Decision.created_at)
    ).all()
    points = []
    for decision, instrument in rows:
        if decision.expected_upside_pct is None or decision.expected_downside_pct is None:
            continue
        points.append(
            {
                "symbol": instrument.symbol,
                "decision": decision.decision_type.value,
                "upside": float(abs(D(decision.expected_upside_pct) * 100)),
                "downside": float(abs(D(decision.expected_downside_pct) * 100)),
                "confidence": float(D(decision.user_confidence)) if decision.user_confidence is not None else None,
            }
        )
    return points
