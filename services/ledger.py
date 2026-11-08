from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from analytics.alerts import risk_alerts
from analytics.changes import HeldLine, describe_changes
from analytics.concentration import concentration
from analytics.drawdown import drawdown_series
from analytics.performance import daily_pnl
from analytics.portfolio import Fill, Mark, cash_impact, replay, weights_by_instrument
from analytics.scenarios import ShockPosition, standard_stress
from connectors.ibkr import IBKRReadConnector
from connectors.market_data.series import FX_TO_GBP, business_days, close_at, price_on
from connectors.trading212 import Trading212ReadConnector
from core.config import get_settings
from core.enums import (
    AssetType,
    CashMovementType,
    DataOrigin,
    ProcessingStatus,
    SnapshotSource,
)
from core.money import D, q8
from db.models import (
    BrokerAccount,
    CashMovement,
    Instrument,
    PortfolioSnapshot,
    PositionSnapshot,
    PriceObservation,
    RawBrokerEvent,
    Trade,
)

ORIGIN_TABLES = (
    "broker_account",
    "instrument",
    "raw_broker_event",
    "trade",
    "cash_movement",
    "price_observation",
    "portfolio_snapshot",
    "position_snapshot",
    "thesis",
    "catalyst",
    "decision",
    "journal_entry",
    "decision_outcome",
    "ai_run",
    "watchlist_item",
    "research_note",
)


def assert_local_reset_allowed(session: Session) -> None:
    settings = get_settings()
    if settings.app_env != "local" or not settings.seed_enabled:
        raise RuntimeError("Refusing to reset the database outside the local environment")
    for table in ORIGIN_TABLES:
        count = session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE data_origin = 'LIVE'")).scalar_one()
        if count:
            raise RuntimeError(f"Refusing to seed because {table} contains LIVE rows")


def reset_database(session: Session) -> None:
    assert_local_reset_allowed(session)
    session.execute(text("TRUNCATE TABLE " + ", ".join(ORIGIN_TABLES) + " RESTART IDENTITY CASCADE"))


def _instrument_rows() -> list[Instrument]:
    from connectors.mock_book import INSTRUMENTS

    rows = []
    for spec in INSTRUMENTS:
        rows.append(
            Instrument(
                symbol=spec["symbol"],
                company_name=spec["company_name"],
                asset_type=AssetType.EQUITY,
                exchange=spec["exchange"],
                currency="USD",
                isin=spec["isin"],
                ibkr_conid=spec["conid"],
                trading212_ticker=spec["t212"],
                theme=spec["theme"],
                data_origin=DataOrigin.MOCK,
            )
        )
    return rows


def _account_rows() -> dict[str, BrokerAccount]:
    from connectors.mock_book import ACCOUNTS

    accounts = {}
    for spec in ACCOUNTS:
        account = BrokerAccount(
            broker=spec["broker"],
            account_type=spec["account_type"],
            base_currency="GBP",
            display_name=spec["display_name"],
            active=True,
            external_account_ref=spec["external_account_ref"],
            data_origin=DataOrigin.MOCK,
        )
        accounts[spec["key"]] = account
    return accounts


def ingest_connector(session: Session, connector, account: BrokerAccount) -> int:
    created = 0
    instruments = {row.symbol: row for row in session.scalars(select(Instrument)).all()}
    for event in connector.fetch_events():
        exists = session.scalar(
            select(RawBrokerEvent).where(
                RawBrokerEvent.broker == event.broker,
                RawBrokerEvent.broker_event_id == event.broker_event_id,
            )
        )
        if exists is not None:
            continue
        raw = RawBrokerEvent(
            broker=event.broker,
            broker_event_id=event.broker_event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            raw_payload_json=event.raw_payload,
            processing_status=ProcessingStatus.PENDING,
            data_origin=DataOrigin.MOCK,
        )
        session.add(raw)
        session.flush()
        effect = connector.normalise(event)
        if effect.trade is not None:
            instrument = instruments[effect.trade.symbol]
            draft = effect.trade
            gross_native = draft.quantity * draft.fill_price
            trade = Trade(
                account_id=account.id,
                instrument_id=instrument.id,
                action=draft.action,
                quantity=draft.quantity,
                fill_price=draft.fill_price,
                trade_currency=draft.trade_currency,
                fx_rate_to_gbp=draft.fx_rate_to_gbp,
                gross_value_native=q8(gross_native),
                gross_value_gbp=q8(gross_native * draft.fx_rate_to_gbp),
                fees_native=draft.fees_native,
                fees_gbp=q8(draft.fees_native * draft.fx_rate_to_gbp),
                fx_fee_native=draft.fx_fee_native,
                fx_fee_gbp=q8(draft.fx_fee_native * draft.fx_rate_to_gbp),
                executed_at=draft.executed_at,
                broker_trade_id=draft.broker_trade_id,
                source_event_id=raw.id,
                data_origin=DataOrigin.MOCK,
            )
            session.add(trade)
            session.flush()
            fill = Fill(
                account_id=str(account.id),
                instrument_id=str(instrument.id),
                action=draft.action.value,
                quantity=draft.quantity,
                fill_price=draft.fill_price,
                fx_rate_to_gbp=draft.fx_rate_to_gbp,
                fees_native=draft.fees_native,
                fx_fee_native=draft.fx_fee_native,
                executed_at=draft.executed_at,
            )
            session.add(
                CashMovement(
                    account_id=account.id,
                    movement_type=CashMovementType.TRADE,
                    amount_gbp=q8(cash_impact(fill)),
                    occurred_at=draft.executed_at,
                    source_trade_id=trade.id,
                    source_event_id=raw.id,
                    note="MOCK DATA trade cash",
                    data_origin=DataOrigin.MOCK,
                )
            )
        elif effect.cash is not None:
            session.add(
                CashMovement(
                    account_id=account.id,
                    movement_type=effect.cash.movement_type,
                    amount_gbp=effect.cash.amount_gbp,
                    occurred_at=effect.cash.occurred_at,
                    source_event_id=raw.id,
                    note=effect.cash.note,
                    data_origin=DataOrigin.MOCK,
                )
            )
        else:
            raw.processing_status = ProcessingStatus.IGNORED
            continue
        raw.processing_status = ProcessingStatus.PROCESSED
        created += 1
    return created


def load_prices(session: Session) -> None:
    instruments = session.scalars(select(Instrument)).all()
    for day in business_days():
        as_of = close_at(day)
        for instrument in instruments:
            session.add(
                PriceObservation(
                    instrument_id=instrument.id,
                    as_of=as_of,
                    price=price_on(instrument.symbol, day),
                    currency=instrument.currency,
                    fx_rate_to_gbp=FX_TO_GBP,
                    source="mock_piecewise_close",
                    data_origin=DataOrigin.MOCK,
                )
            )


def _fills(session: Session) -> list[Fill]:
    trades = session.scalars(
        select(Trade).where(Trade.data_origin == DataOrigin.MOCK).order_by(Trade.executed_at)
    ).all()
    return [
        Fill(
            account_id=str(trade.account_id),
            instrument_id=str(trade.instrument_id),
            action=trade.action.value,
            quantity=D(trade.quantity),
            fill_price=D(trade.fill_price),
            fx_rate_to_gbp=D(trade.fx_rate_to_gbp),
            fees_native=D(trade.fees_native),
            fx_fee_native=D(trade.fx_fee_native),
            executed_at=trade.executed_at,
        )
        for trade in trades
    ]


def _opening_cash(session: Session) -> Decimal:
    movements = session.scalars(
        select(CashMovement).where(
            CashMovement.movement_type == CashMovementType.OPENING,
            CashMovement.data_origin == DataOrigin.MOCK,
        )
    ).all()
    return sum((D(movement.amount_gbp) for movement in movements if movement.amount_gbp is not None), start=D(0))


def rebuild_snapshots(session: Session, source: SnapshotSource = SnapshotSource.SEED) -> int:
    """Build close snapshots for price observations that do not yet have one.

    Drawdown uses the full stored history plus the new closes. The scheduler
    writes a row only when a newer price exists, at the configured interval.
    """
    stored = session.scalars(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.data_origin == DataOrigin.MOCK)
        .order_by(PortfolioSnapshot.snapshot_time)
    ).all()
    if stored and source is SnapshotSource.SEED:
        raise RuntimeError("snapshot table is not empty")

    instruments = {str(row.id): row for row in session.scalars(select(Instrument)).all()}
    labels = {key: row.symbol for key, row in instruments.items()}
    prices = session.scalars(select(PriceObservation).order_by(PriceObservation.as_of)).all()
    by_day: dict[datetime, dict[str, Mark]] = {}
    for price in prices:
        by_day.setdefault(price.as_of, {})[str(price.instrument_id)] = Mark(
            instrument_id=str(price.instrument_id),
            price=D(price.price),
            fx_rate_to_gbp=D(price.fx_rate_to_gbp),
            currency=price.currency,
        )
    fills = _fills(session)
    opening = _opening_cash(session)
    totals = [D(row.total_portfolio_value_gbp) for row in stored]
    previous = totals[-1] if totals else None
    last_time = stored[-1].snapshot_time if stored else None
    count = 0
    for as_of in sorted(by_day):
        if last_time is not None and as_of <= last_time:
            continue
        usable = [fill for fill in fills if fill.executed_at <= as_of]
        state = replay(usable, by_day[as_of], opening)
        totals.append(state.total_value_gbp)
        drawdown = drawdown_series(totals)
        pnl, pnl_pct = daily_pnl(state.total_value_gbp, previous)
        merged = weights_by_instrument(state, labels)
        conc = concentration(merged)
        snapshot = PortfolioSnapshot(
            snapshot_time=as_of,
            total_portfolio_value_gbp=state.total_value_gbp,
            cash_gbp=state.cash_gbp,
            invested_value_gbp=state.invested_value_gbp,
            daily_pnl_gbp=q8(pnl),
            daily_pnl_pct=q8(pnl_pct),
            ath_portfolio_value_gbp=drawdown.ath,
            drawdown_from_ath=q8(drawdown.current),
            max_drawdown_to_date=q8(drawdown.maximum),
            top1_concentration=q8(conc.top1),
            top3_concentration=q8(conc.top3),
            top5_concentration=q8(conc.top5),
            realised_pnl_gbp=state.realised_pnl_gbp,
            risk_score=None,
            source=source,
            data_origin=DataOrigin.MOCK,
        )
        session.add(snapshot)
        session.flush()
        for position in state.positions:
            session.add(
                PositionSnapshot(
                    portfolio_snapshot_id=snapshot.id,
                    account_id=UUID(position.account_id),
                    instrument_id=UUID(position.instrument_id),
                    quantity=position.quantity,
                    market_price=position.market_price or D(0),
                    market_price_currency=position.market_currency,
                    fx_rate_to_gbp=position.market_fx or D(0),
                    market_value_gbp=position.market_value_gbp,
                    average_cost_gbp=q8(position.average_cost_gbp),
                    cost_basis_gbp=position.cost_basis_gbp,
                    unrealised_pnl_gbp=position.unrealised_pnl_gbp,
                    unrealised_pnl_pct=q8(position.unrealised_pnl_pct),
                    realised_pnl_gbp=q8(position.realised_pnl_gbp),
                    portfolio_weight=q8(position.portfolio_weight),
                    data_origin=DataOrigin.MOCK,
                )
            )
        previous = state.total_value_gbp
        count += 1
    return count


def latest_snapshot(session: Session, *, origin: DataOrigin = DataOrigin.MOCK) -> PortfolioSnapshot | None:
    return session.scalar(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.data_origin == origin)
        .order_by(PortfolioSnapshot.snapshot_time.desc())
    )


def snapshot_before(session: Session, moment: datetime, *, origin: DataOrigin = DataOrigin.MOCK) -> PortfolioSnapshot | None:
    return session.scalar(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.data_origin == origin, PortfolioSnapshot.snapshot_time < moment)
        .order_by(PortfolioSnapshot.snapshot_time.desc())
    )


def snapshot_after(session: Session, moment: datetime, *, origin: DataOrigin = DataOrigin.MOCK) -> PortfolioSnapshot | None:
    return session.scalar(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.data_origin == origin, PortfolioSnapshot.snapshot_time >= moment)
        .order_by(PortfolioSnapshot.snapshot_time)
    )


def position_lines(session: Session, snapshot_id) -> list[HeldLine]:
    rows = session.execute(
        select(Instrument.symbol, PositionSnapshot.portfolio_weight, PositionSnapshot.market_value_gbp)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .where(PositionSnapshot.portfolio_snapshot_id == snapshot_id)
    ).all()
    merged: dict[str, list[Decimal]] = {}
    for symbol, weight, value in rows:
        bucket = merged.setdefault(symbol, [D(0), D(0)])
        bucket[0] += D(weight)
        bucket[1] += D(value)
    return [HeldLine(symbol, pair[0], pair[1]) for symbol, pair in merged.items()]


def build_risk_context(session: Session, snapshot: PortfolioSnapshot) -> dict:
    """Calculated context for a model. No broker account numbers."""
    settings = get_settings()
    positions = session.execute(
        select(PositionSnapshot, Instrument, BrokerAccount)
        .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
        .join(BrokerAccount, BrokerAccount.id == PositionSnapshot.account_id)
        .where(PositionSnapshot.portfolio_snapshot_id == snapshot.id)
    ).all()
    merged: dict[str, dict] = {}
    position_rows = []
    for position, instrument, _account in positions:
        position_rows.append(
            {
                "symbol": instrument.symbol,
                "company_name": instrument.company_name,
                "theme": instrument.theme,
                "quantity": str(position.quantity),
                "market_price": str(position.market_price),
                "market_price_currency": position.market_price_currency,
                "average_cost_gbp": None if position.average_cost_gbp is None else str(position.average_cost_gbp),
                "market_value_gbp": str(position.market_value_gbp),
                "portfolio_weight": str(position.portfolio_weight),
                "unrealised_pnl_gbp": str(position.unrealised_pnl_gbp),
                "unrealised_pnl_pct": str(position.unrealised_pnl_pct),
            }
        )
        bucket = merged.setdefault(
            instrument.symbol,
            {"weight": D(0), "theme": instrument.theme, "value": D(0)},
        )
        bucket["weight"] += D(position.portfolio_weight)
        bucket["value"] += D(position.market_value_gbp)
    shocks = [
        ShockPosition(symbol, data["weight"], data["theme"]) for symbol, data in merged.items()
    ]
    scenarios = [
        {
            "name": result.name,
            "assumption": result.assumption,
            "portfolio_impact": str(q8(result.portfolio_impact)),
        }
        for result in standard_stress(shocks)
    ]
    worst = None
    for shock in shocks:
        impact = shock.weight * D("-0.20")
        if worst is None or impact < worst[1]:
            worst = (shock.key, impact)
    cash_weight = D(snapshot.cash_gbp) / D(snapshot.total_portfolio_value_gbp)
    largest = max(merged.items(), key=lambda item: item[1]["weight"])[0] if merged else None
    alerts = [
        {"code": alert.code, "severity": alert.severity, "message": alert.message, "evidence": alert.evidence}
        for alert in risk_alerts(
            largest_symbol=largest,
            top1=D(snapshot.top1_concentration),
            top1_limit=settings.concentration_top1_alert,
            drawdown=D(snapshot.drawdown_from_ath),
            drawdown_limit=settings.drawdown_alert,
            cash_weight=cash_weight,
            cash_limit=settings.cash_weight_alert,
            worst_single_name=worst,
            impact_limit=settings.scenario_impact_alert,
        )
    ]
    previous = snapshot_before(session, snapshot.snapshot_time, origin=snapshot.data_origin)
    changes = describe_changes(
        position_lines(session, previous.id) if previous else None,
        position_lines(session, snapshot.id),
        D(previous.total_portfolio_value_gbp) if previous else None,
        D(snapshot.total_portfolio_value_gbp),
    )
    return {
        "data_origin": snapshot.data_origin.value,
        "label": "MOCK DATA" if snapshot.data_origin is DataOrigin.MOCK else "LIVE",
        "totals": {
            "snapshot_time": snapshot.snapshot_time.isoformat(),
            "reporting_currency": "GBP",
            "total_gbp": str(snapshot.total_portfolio_value_gbp),
            "cash_gbp": str(snapshot.cash_gbp),
            "invested_gbp": str(snapshot.invested_value_gbp),
            "daily_pnl_gbp": str(snapshot.daily_pnl_gbp),
            "daily_pnl_pct": str(snapshot.daily_pnl_pct),
            "drawdown_from_ath": str(snapshot.drawdown_from_ath),
            "max_drawdown_to_date": str(snapshot.max_drawdown_to_date),
            "ath_gbp": str(snapshot.ath_portfolio_value_gbp),
            "top1_concentration": str(snapshot.top1_concentration),
            "top3_concentration": str(snapshot.top3_concentration),
            "top5_concentration": str(snapshot.top5_concentration),
            "largest_symbol": largest or "",
            "cash_weight": str(q8(cash_weight)),
        },
        "positions": position_rows,
        "scenarios": scenarios,
        "alerts": alerts,
        "changes": [{"code": change.code, "message": change.message, "evidence": change.evidence} for change in changes],
    }
