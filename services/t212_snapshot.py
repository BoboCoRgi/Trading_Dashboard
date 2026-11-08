"""Store one current Trading 212 portfolio snapshot.

The snapshot uses the live account summary and live positions. It does not
reconstruct past market values, and it does not change figures to force the
account total to match the sum of the parts.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from analytics.concentration import concentration
from analytics.cost_basis import CostEvent, compare_average, overall_cost_status, reconstruct_cost
from analytics.drawdown import drawdown_series
from analytics.performance import daily_pnl
from analytics.snapshot_integrity import portfolio_weights, snapshot_bucket, snapshot_integrity
from connectors.trading212.client import (
    Trading212Error,
    current_display_symbol,
    display_symbol,
    listing_metadata,
    position_ticker,
)
from connectors.trading212.history import reconcile_positions
from core.enums import Broker, DataOrigin, SnapshotSource
from core.money import D, q8
from db.models import (
    BrokerSyncStatus,
    CorporateAction,
    Instrument,
    PortfolioSnapshot,
    PositionSnapshot,
    RawBrokerEvent,
    Trade,
)
from services.instrument_identity import refresh_instrument_identity
from services.t212_sync import _instrument, _live_account, _reconstruct_account, rehome_stock_splits


@dataclass
class LiveSnapshotReport:
    created: bool
    snapshot_time: datetime
    total_gbp: Decimal | None
    cash_gbp: Decimal | None
    invested_gbp: Decimal | None
    position_count: int
    largest_symbol: str | None
    largest_value_gbp: Decimal | None
    top1: Decimal
    integrity: dict
    invested_gap_gbp: Decimal | None
    position_reconciliation: dict
    cost_lines: list[dict] = field(default_factory=list)
    symbols: dict = field(default_factory=dict)
    cost_status: str = "UNAVAILABLE"

    def render(self) -> str:
        lines = [
            "Trading 212 live snapshot",
            "",
            f"Total account value: {_gbp(self.total_gbp)}",
            f"Cash: {_gbp(self.cash_gbp)}",
            f"Invested value: {_gbp(self.invested_gbp)}",
            f"Positions: {self.position_count}",
            "",
            "Largest position:",
            _largest(self.largest_symbol, self.largest_value_gbp),
            "",
            "Top 1 concentration:",
            f"{_pct(self.top1)}",
            "",
            "Snapshot integrity:",
            self.integrity.get("status", "FAIL"),
            f"Position market values plus cash: {_gbp(self.integrity.get('components'))}",
            f"Difference: {_gbp(self.integrity.get('difference'))}",
        ]
        if self.invested_gap_gbp is not None:
            lines.append(f"Invested value minus position market values: {_gbp(self.invested_gap_gbp)}")
        lines.extend(["", "Position reconciliation:", self.position_reconciliation.get("overall", "FAIL")])
        for line in self.position_reconciliation.get("lines") or []:
            lines.extend(
                [
                    "",
                    self.symbols.get(line["ticker"]) or display_symbol(line["ticker"]),
                    f"Broker quantity: {_plain(line['api_quantity'])}",
                    f"Reconstructed quantity: {_plain(line['reconstructed_quantity'])}",
                    f"Quantity: {line['status']}",
                ]
            )
        lines.extend(["", "Cost basis reconciliation:", self.cost_status])
        for line in self.cost_lines:
            lines.extend(
                [
                    "",
                    line["symbol"],
                    f"Broker quantity: {_plain(line['broker_quantity'])}",
                    f"Reconstructed quantity: {_plain(line['reconstructed_quantity'])}",
                    f"Quantity: {line['quantity_status']}",
                    f"Broker average price: {_price(line['broker_average'], line['broker_currency'])}",
                    f"Reconstructed average price: {_price(line['reconstructed_average'], line['reconstructed_currency'])}",
                    f"Difference: {_plain(line['difference'])}",
                    f"Cost basis status: {line['status']}",
                ]
            )
            if line.get("note"):
                lines.append(line["note"])
        lines.extend(
            [
                "",
                "Stored:" if self.created else "Stored snapshot already existed for this time:",
                self.snapshot_time.strftime("%Y-%m-%d %H:%M UTC"),
            ]
        )
        return "\n".join(lines)


def create_live_snapshot(session: Session, client, settings, *, source: SnapshotSource) -> LiveSnapshotReport:
    """Fetch the current account, check it, and store one snapshot for this interval."""
    rehome_stock_splits(session)
    summary = client.get_account_summary()
    positions = client.get_positions()
    if not isinstance(summary, dict):
        raise ValueError("account summary was not an object")
    currency = str(summary.get("currency") or "")
    if currency != "GBP":
        raise ValueError("account currency is not GBP, so GBP totals were not stored")
    account = _live_account(session, client)
    catalogue = _catalogue(client)
    refresh_instrument_identity(session, catalogue)
    total = _money(summary.get("totalValue"))
    cash_block = summary.get("cash") if isinstance(summary.get("cash"), dict) else {}
    investments = summary.get("investments") if isinstance(summary.get("investments"), dict) else {}
    cash = _sum_present(cash_block.get("availableToTrade"), cash_block.get("reservedForOrders"), cash_block.get("inPies"))
    invested = _money(investments.get("currentValue"))
    built = _position_rows(session, account, positions, catalogue)
    position_values = [row["market_value_gbp"] for row in built if row["market_value_gbp"] is not None]
    position_sum = sum(position_values, start=D(0))
    if total is None or cash is None:
        raise ValueError("account total or cash was not provided")
    integrity = snapshot_integrity(total=total, position_market_value=position_sum, cash=cash)
    invested_gap = None if invested is None else invested - position_sum
    weights = portfolio_weights([row["market_value_gbp"] or D(0) for row in built], total)
    for row, weight in zip(built, weights, strict=True):
        row["portfolio_weight"] = weight
    named = [(row["symbol"], row["portfolio_weight"] or D(0)) for row in built]
    conc = concentration(named)
    moment = snapshot_bucket(datetime.now(timezone.utc), settings.snapshot_interval_minutes)
    created = _store_snapshot(
        session,
        account_id=account.id,
        moment=moment,
        source=source,
        total=total,
        cash=cash,
        invested=invested if invested is not None else position_sum,
        realised=_money(investments.get("realizedProfitLoss")),
        rows=built,
        top1=conc.top1,
        top3=conc.top3,
        top5=conc.top5,
    )
    rebuilt = _reconstruct_account(session, account.id)
    position_reconciliation = reconcile_positions(positions, rebuilt)
    cost_lines = _cost_lines(session, account.id, positions, position_reconciliation, catalogue)
    cost_status = overall_cost_status([line["status"] for line in cost_lines])
    largest = max(built, key=lambda row: row["market_value_gbp"] or D(0)) if built else None
    _record_status(
        session,
        connection_status="ok",
        last_snapshot_time=moment,
        position_reconciliation_status=position_reconciliation.get("overall"),
        cost_basis_reconciliation_status=cost_status,
        snapshot_integrity_status=integrity["status"],
        position_count=len(built),
    )
    return LiveSnapshotReport(
        created=created,
        snapshot_time=moment,
        total_gbp=total,
        cash_gbp=cash,
        invested_gbp=invested,
        position_count=len(built),
        largest_symbol=None if largest is None else largest["symbol"],
        symbols={ticker: current_display_symbol(ticker, meta) for ticker, meta in catalogue.items()},
        largest_value_gbp=None if largest is None else largest["market_value_gbp"],
        top1=conc.top1,
        integrity=integrity,
        invested_gap_gbp=invested_gap,
        position_reconciliation=position_reconciliation,
        cost_lines=cost_lines,
        cost_status=cost_status,
    )


def _position_rows(session, account, positions: list, catalogue: dict) -> list[dict]:
    rows = []
    for raw in positions:
        if not isinstance(raw, dict):
            continue
        ticker = position_ticker(raw)
        if not ticker or raw.get("quantity") is None or raw.get("currentPrice") is None:
            continue
        payload = raw.get("instrument") if isinstance(raw.get("instrument"), dict) else {}
        instrument = _instrument(
            session,
            ticker,
            listing_metadata(payload, catalogue.get(ticker)),
            type("R", (), {"unmapped_instruments": 0})(),
        )
        if instrument is None:
            continue
        quantity = D(str(raw["quantity"]))
        price = D(str(raw["currentPrice"]))
        meta = raw.get("instrument") if isinstance(raw.get("instrument"), dict) else {}
        price_currency = str(meta.get("currency") or instrument.currency or "UNAVAILABLE")
        wallet = raw.get("walletImpact") if isinstance(raw.get("walletImpact"), dict) else {}
        wallet_currency = str(wallet.get("currency") or "")
        market_value = _money(wallet.get("currentValue")) if wallet_currency == "GBP" else None
        total_cost = _money(wallet.get("totalCost")) if wallet_currency == "GBP" else None
        unrealised = _money(wallet.get("unrealizedProfitLoss")) if wallet_currency == "GBP" else None
        broker_average = _money(raw.get("averagePricePaid"))
        rows.append(
            {
                "account_id": account.id,
                "instrument_id": instrument.id,
                "symbol": instrument.symbol,
                "ticker": ticker,
                "quantity": q8(quantity),
                "market_price": q8(price),
                "market_price_currency": price_currency[:8],
                "fx_rate_to_gbp": _implied_fx(price_currency, quantity, price, market_value),
                "market_value_gbp": None if market_value is None else q8(market_value),
                "average_cost_gbp": _per_share(total_cost, quantity),
                "cost_basis_gbp": None if total_cost is None else q8(total_cost),
                "broker_average_price": None if broker_average is None else q8(broker_average),
                "broker_average_currency": price_currency[:8] if broker_average is not None else None,
                "unrealised_pnl_gbp": None if unrealised is None else q8(unrealised),
                "unrealised_pnl_pct": _per_share(unrealised, total_cost) if total_cost not in (None, D(0)) else None,
                "realised_pnl_gbp": None,
                "portfolio_weight": None,
            }
        )
    return rows


def _store_snapshot(session, *, account_id, moment, source, total, cash, invested, realised, rows, top1, top3, top5) -> bool:
    existing = session.scalar(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.data_origin == DataOrigin.LIVE,
            PortfolioSnapshot.snapshot_time == moment,
        )
    )
    if existing is not None:
        return False
    history = session.scalars(
        select(PortfolioSnapshot.total_portfolio_value_gbp)
        .where(PortfolioSnapshot.data_origin == DataOrigin.LIVE, PortfolioSnapshot.snapshot_time < moment)
        .order_by(PortfolioSnapshot.snapshot_time)
    ).all()
    series = [D(value) for value in history] + [total]
    drawdown = drawdown_series(series)
    pnl = None
    pnl_pct = None
    if history:
        pnl, pnl_pct = daily_pnl(total, D(history[-1]))
    snapshot = PortfolioSnapshot(
        snapshot_time=moment,
        total_portfolio_value_gbp=q8(total),
        cash_gbp=q8(cash),
        invested_value_gbp=q8(invested),
        daily_pnl_gbp=None if pnl is None else q8(pnl),
        daily_pnl_pct=None if pnl_pct is None else q8(pnl_pct),
        ath_portfolio_value_gbp=q8(drawdown.ath),
        drawdown_from_ath=q8(drawdown.current),
        max_drawdown_to_date=q8(drawdown.maximum),
        top1_concentration=q8(top1),
        top3_concentration=q8(top3),
        top5_concentration=q8(top5),
        realised_pnl_gbp=None if realised is None else q8(realised),
        risk_score=None,
        source=source,
        data_origin=DataOrigin.LIVE,
    )
    session.add(snapshot)
    session.flush()
    for row in rows:
        session.add(
            PositionSnapshot(
                portfolio_snapshot_id=snapshot.id,
                account_id=account_id,
                instrument_id=row["instrument_id"],
                quantity=row["quantity"],
                market_price=row["market_price"],
                market_price_currency=row["market_price_currency"],
                fx_rate_to_gbp=row["fx_rate_to_gbp"],
                market_value_gbp=row["market_value_gbp"],
                average_cost_gbp=row["average_cost_gbp"],
                cost_basis_gbp=row["cost_basis_gbp"],
                broker_average_price=row["broker_average_price"],
                broker_average_currency=row["broker_average_currency"],
                unrealised_pnl_gbp=row["unrealised_pnl_gbp"],
                unrealised_pnl_pct=row["unrealised_pnl_pct"],
                realised_pnl_gbp=None,
                portfolio_weight=row["portfolio_weight"],
                data_origin=DataOrigin.LIVE,
            )
        )
    session.flush()
    return True


def _catalogue(client) -> dict:
    try:
        rows = client.get_instruments()
    except Trading212Error:
        return {}
    return {str(row.get("ticker")): row for row in rows if isinstance(row, dict) and row.get("ticker")}


def _cost_lines(session, account_id, positions: list, reconciliation: dict, catalogue: dict) -> list[dict]:
    quantity_status = {line["ticker"]: line["status"] for line in reconciliation.get("lines") or []}
    events = _cost_events(session, account_id)
    lines = []
    for raw in positions:
        if not isinstance(raw, dict):
            continue
        ticker = position_ticker(raw)
        if not ticker:
            continue
        meta = raw.get("instrument") if isinstance(raw.get("instrument"), dict) else {}
        broker_currency = str(meta.get("currency") or "")
        broker_average = _money(raw.get("averagePricePaid"))
        try:
            state = reconstruct_cost(events.get(ticker, []))
        except ValueError:
            state = None
        reconstructed = None if state is None else state.average_native
        reconstructed_currency = "" if state is None else state.currency
        note = None
        if state is not None and broker_currency and reconstructed_currency and broker_currency != reconstructed_currency:
            status = "UNAVAILABLE"
            note = "Reconstructed and broker averages are in different currencies, so they were not compared."
        elif broker_average is None:
            status = "UNAVAILABLE"
            note = "Broker average price was not provided."
        else:
            status = compare_average(reconstructed, broker_average)
        difference = None
        if reconstructed is not None and broker_average is not None and status != "UNAVAILABLE":
            difference = reconstructed - broker_average
        gbp_note = None
        if state is not None and not state.gbp_complete:
            gbp_note = "GBP cost basis was not reconstructed because a historical FX rate is missing."
        elif state is not None and state.average_gbp is not None:
            gbp_note = f"Reconstructed GBP average cost: {_gbp(state.average_gbp)}"
        extra = " ".join(part for part in (note, gbp_note) if part)
        lines.append(
            {
                "symbol": current_display_symbol(ticker, catalogue.get(ticker)),
                "ticker": ticker,
                "broker_quantity": _money(raw.get("quantity")),
                "reconstructed_quantity": None if state is None else state.quantity,
                "quantity_status": quantity_status.get(ticker, "FAIL"),
                "broker_average": broker_average,
                "broker_currency": broker_currency,
                "reconstructed_average": reconstructed,
                "reconstructed_currency": reconstructed_currency,
                "difference": difference,
                "status": status,
                "note": extra or None,
            }
        )
    return lines


def _cost_events(session, account_id) -> dict[str, list[CostEvent]]:
    grouped: dict[str, list[CostEvent]] = {}
    trades = session.execute(
        select(Trade, Instrument)
        .join(Instrument, Instrument.id == Trade.instrument_id)
        .where(Trade.account_id == account_id, Trade.data_origin == DataOrigin.LIVE)
    ).all()
    for trade, instrument in trades:
        ticker = instrument.trading212_ticker or instrument.symbol
        grouped.setdefault(ticker, []).append(
            CostEvent(
                at=trade.executed_at,
                kind=trade.action.value,
                quantity=D(trade.quantity),
                price=D(trade.fill_price),
                fees_native=D(trade.fees_native) + D(trade.fx_fee_native),
                currency=trade.trade_currency,
                fx_rate_to_gbp=None if trade.fx_rate_to_gbp is None else D(trade.fx_rate_to_gbp),
            )
        )
    actions = session.execute(
        select(CorporateAction, Instrument)
        .join(Instrument, Instrument.id == CorporateAction.instrument_id)
        .where(CorporateAction.account_id == account_id)
    ).all()
    for action, instrument in actions:
        if action.quantity_delta is None:
            continue
        ticker = instrument.trading212_ticker or instrument.symbol
        grouped.setdefault(ticker, []).append(
            CostEvent(
                at=action.effective_at,
                kind="CORPORATE",
                quantity=D(action.quantity_delta),
                currency=instrument.currency or "",
            )
        )
    return grouped


def _record_status(session, **fields) -> None:
    last_sync = session.scalar(
        select(func.max(RawBrokerEvent.received_at)).where(
            RawBrokerEvent.broker == Broker.TRADING212,
            RawBrokerEvent.data_origin == DataOrigin.LIVE,
        )
    )
    row = session.scalar(select(BrokerSyncStatus).where(BrokerSyncStatus.broker == Broker.TRADING212))
    if row is None:
        row = BrokerSyncStatus(broker=Broker.TRADING212, connection_status=fields["connection_status"])
        session.add(row)
    row.connection_status = fields["connection_status"]
    row.last_successful_sync = last_sync
    row.last_snapshot_time = fields["last_snapshot_time"]
    row.position_reconciliation_status = fields["position_reconciliation_status"]
    row.cost_basis_reconciliation_status = fields["cost_basis_reconciliation_status"]
    row.snapshot_integrity_status = fields["snapshot_integrity_status"]
    row.position_count = fields["position_count"]
    row.updated_at = datetime.now(timezone.utc)
    session.flush()


def live_status(session: Session) -> dict:
    """Status safe to show later in the UI. No secrets and no raw payload."""
    row = session.scalar(select(BrokerSyncStatus).where(BrokerSyncStatus.broker == Broker.TRADING212))
    if row is None:
        return {
            "broker": "Trading 212",
            "connection_status": "not_checked",
            "last_successful_sync": None,
            "last_snapshot_time": None,
            "position_reconciliation_status": None,
            "cost_basis_reconciliation_status": None,
            "snapshot_integrity_status": None,
            "position_count": None,
        }
    return {
        "broker": "Trading 212",
        "connection_status": row.connection_status,
        "last_successful_sync": row.last_successful_sync,
        "last_snapshot_time": row.last_snapshot_time,
        "position_reconciliation_status": row.position_reconciliation_status,
        "cost_basis_reconciliation_status": row.cost_basis_reconciliation_status,
        "snapshot_integrity_status": row.snapshot_integrity_status,
        "position_count": row.position_count,
    }


def current_live_snapshot(session: Session) -> PortfolioSnapshot | None:
    from services.ledger import latest_snapshot

    return latest_snapshot(session, origin=DataOrigin.LIVE)


def current_live_positions(session: Session) -> list[PositionSnapshot]:
    snapshot = current_live_snapshot(session)
    if snapshot is None:
        return []
    return list(
        session.scalars(select(PositionSnapshot).where(PositionSnapshot.portfolio_snapshot_id == snapshot.id)).all()
    )


def _implied_fx(currency: str, quantity: Decimal, price: Decimal, market_value: Decimal | None) -> Decimal | None:
    """FX implied by this snapshot's own GBP value and quoted price.

    This is not a historical rate and is not used to fill old cash rows.
    """
    if currency == "GBP":
        return q8(D(1))
    if market_value is None:
        return None
    notional = quantity * price
    if notional == 0:
        return None
    return q8(market_value / notional)


def _per_share(amount: Decimal | None, quantity: Decimal | None) -> Decimal | None:
    if amount is None or quantity in (None, D(0)):
        return None
    return q8(amount / quantity)


def _money(value) -> Decimal | None:
    if value is None:
        return None
    return D(str(value))


def _sum_present(*values) -> Decimal | None:
    present = [_money(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present, start=D(0))


def _gbp(value) -> str:
    if value is None:
        return "unavailable"
    return f"£{D(value):,.2f}"


def _pct(value: Decimal) -> str:
    return f"{D(value) * 100:.2f}%"


def _plain(value) -> str:
    if value is None:
        return "unavailable"
    return f"{D(value).normalize():f}"


def _price(value, currency: str) -> str:
    if value is None:
        return "unavailable"
    suffix = f" {currency}" if currency else ""
    return f"{D(value).normalize():f}{suffix}"


def _largest(symbol: str | None, value: Decimal | None) -> str:
    if symbol is None:
        return "unavailable"
    return f"{symbol} {_gbp(value)}"
