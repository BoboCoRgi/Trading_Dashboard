"""Archive Trading 212 history and rebuild positions from those rows.

This does not place orders and does not write portfolio snapshots.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from connectors.trading212.client import current_display_symbol, display_symbol, listing_metadata, position_ticker
from connectors.trading212.history import (
    cash_gbp_fields,
    classify_order,
    dividend_event_id,
    fx_rate_to_gbp,
    order_event_id,
    order_occurred_at,
    order_ticker,
    parse_utc,
    reconcile_positions,
    reconstruct_quantities,
    split_taxes,
    transaction_event_id,
    transaction_type,
)
from core.enums import (
    AssetType,
    Broker,
    CashMovementType,
    CorporateActionType,
    DataOrigin,
    ProcessingStatus,
    TradeAction,
)
from core.money import D, q8
from db.models import BrokerAccount, CashMovement, CorporateAction, Instrument, RawBrokerEvent, Trade
from services.instrument_identity import apply_current_identity, refresh_instrument_identity

_LIVE_ACCOUNT_NAME = "Trading 212 live"


@dataclass
class SyncReport:
    orders_fetched: int = 0
    transactions_fetched: int = 0
    dividends_fetched: int = 0
    instruments_fetched: int = 0
    raw_inserted: int = 0
    raw_known: int = 0
    trades_created: int = 0
    corporate_actions_created: int = 0
    cash_created: int = 0
    dividends_created: int = 0
    unmapped_instruments: int = 0
    labels: dict = field(default_factory=dict)
    normalisation_errors: int = 0
    duplicate_executions: int = 0
    reconciliation: dict = field(default_factory=dict)

    def render(self) -> str:
        recon = self.reconciliation or {}
        lines = [
            "Trading 212 historical sync",
            "",
            f"Historical orders fetched: {self.orders_fetched}",
            f"Transactions fetched: {self.transactions_fetched}",
            f"Dividends fetched: {self.dividends_fetched}",
            f"Instrument records fetched: {self.instruments_fetched}",
            "",
            f"Raw broker events inserted: {self.raw_inserted}",
            f"Raw broker events already known: {self.raw_known}",
            "",
            f"Canonical trades created: {self.trades_created}",
            f"Corporate actions created: {self.corporate_actions_created}",
            f"Cash events created: {self.cash_created}",
            f"Dividends created: {self.dividends_created}",
            "",
            f"Unmapped instruments: {self.unmapped_instruments}",
            f"Normalisation errors: {self.normalisation_errors}",
            "",
            "Reconciliation:",
            recon.get("overall", "FAIL"),
        ]
        for line in recon.get("lines") or []:
            lines.extend(
                [
                    "",
                    self.labels.get(line["ticker"]) or display_symbol(line["ticker"]),
                    f"API quantity: {_qty(line['api_quantity'])}",
                    f"Reconstructed quantity: {_qty(line['reconstructed_quantity'])}",
                    f"Difference: {_qty(line['difference'])}",
                    f"Status: {line['status']}",
                ]
            )
        lines.extend(
            [
                "",
                "Summary",
                f"Live positions: {recon.get('live_positions', 0)}",
                f"Reconstructed open positions: {recon.get('reconstructed_open_positions', 0)}",
                f"Matched: {recon.get('matched', 0)}",
                f"Quantity mismatches: {recon.get('quantity_mismatches', 0)}",
                f"Missing from history: {recon.get('missing_from_history', 0)}",
                f"Unexpected reconstructed positions: {recon.get('unexpected_reconstructed_positions', 0)}",
                f"Unmapped instruments: {recon.get('unmapped_instruments', 0)}",
                f"Duplicate broker events: {recon.get('duplicate_broker_events', 0)}",
                "",
                f"Overall: {recon.get('overall', 'FAIL')}",
            ]
        )
        return "\n".join(lines)


def sync_trading212_history(session: Session, client, positions: list[dict] | None = None) -> SyncReport:
    report = SyncReport()
    catalogue = client.get_instruments()
    orders = client.get_historical_orders()
    transactions = client.get_transactions()
    dividends = client.get_dividends()
    report.instruments_fetched = len(catalogue)
    report.orders_fetched = len(orders)
    report.transactions_fetched = len(transactions)
    report.dividends_fetched = len(dividends)
    by_ticker = {
        str(row.get("ticker")): row
        for row in catalogue
        if isinstance(row, dict) and row.get("ticker")
    }
    account = _live_account(session, client)
    refresh_instrument_identity(session, by_ticker)
    report.labels = {
        ticker: current_display_symbol(ticker, meta) for ticker, meta in by_ticker.items() if isinstance(meta, dict)
    }
    for row in orders:
        _archive_order(session, account, row, by_ticker, report)
    for row in transactions:
        _archive_transaction(session, account, row, report)
    for row in dividends:
        _archive_dividend(session, account, row, by_ticker, report)
    report.duplicate_executions = _duplicate_executions(session, account.id)
    rehome_stock_splits(session)
    live_positions = positions if positions is not None else client.get_positions()
    rebuilt = _reconstruct_account(session, account.id)
    report.reconciliation = reconcile_positions(
        live_positions,
        rebuilt,
        unmapped=report.unmapped_instruments,
        duplicate_executions=report.duplicate_executions,
        normalisation_errors=report.normalisation_errors,
    )
    return report


def _account_currency(client) -> str:
    summary = client.get_account_summary()
    currency = summary.get("currency") if isinstance(summary, dict) else None
    return str(currency) if currency else ""


def _live_account(session: Session, client) -> BrokerAccount:
    account = session.scalar(
        select(BrokerAccount).where(
            BrokerAccount.broker == Broker.TRADING212,
            BrokerAccount.data_origin == DataOrigin.LIVE,
            BrokerAccount.display_name == _LIVE_ACCOUNT_NAME,
        )
    )
    if account is not None:
        return account
    account = BrokerAccount(
        broker=Broker.TRADING212,
        account_type="LIVE",
        base_currency=_account_currency(client),
        display_name=_LIVE_ACCOUNT_NAME,
        active=True,
        external_account_ref=None,
        data_origin=DataOrigin.LIVE,
    )
    session.add(account)
    session.flush()
    return account


def _archive_order(session, account, item, catalogue, report: SyncReport) -> None:
    if not isinstance(item, dict):
        report.normalisation_errors += 1
        return
    event_id = order_event_id(item)
    try:
        occurred = order_occurred_at(item)
    except ValueError:
        occurred = parse_utc("1970-01-01T00:00:00+00:00")
        raw = _insert_raw(session, event_id, "ORDER", occurred, item, report)
        if raw is None:
            return
        _fail(raw, "order had no timestamp")
        report.normalisation_errors += 1
        return
    raw = _insert_raw(session, event_id, "ORDER", occurred, item, report)
    if raw is None or raw.processing_status is ProcessingStatus.PROCESSED:
        return
    if raw.processing_status is ProcessingStatus.IGNORED and classify_order(item) not in {"trade", "corporate"}:
        return
    kind = classify_order(item)
    if kind == "corporate":
        try:
            _write_corporate(session, account, raw, item, catalogue, report)
        except Exception as exc:
            _fail(raw, _safe_error(exc))
            report.normalisation_errors += 1
        return
    if kind == "ignore":
        raw.processing_status = ProcessingStatus.IGNORED
        raw.processing_error = None
        return
    if kind == "error":
        _fail(raw, "executed order was missing a side, ticker, quantity, or price")
        report.normalisation_errors += 1
        if not order_ticker(item):
            report.unmapped_instruments += 1
        return
    try:
        _write_trade(session, account, raw, item, catalogue, report)
    except Exception as exc:
        _fail(raw, _safe_error(exc))
        report.normalisation_errors += 1


def _write_trade(session, account, raw, item, catalogue, report: SyncReport) -> None:
    order = item["order"]
    fill = item["fill"]
    ticker = order_ticker(item)
    instrument = _instrument(session, ticker, listing_metadata(order.get("instrument"), catalogue.get(ticker)), report)
    if instrument is None:
        _fail(raw, "instrument could not be mapped")
        report.normalisation_errors += 1
        return
    trade_currency = str((order.get("instrument") or {}).get("currency") or order.get("currency") or instrument.currency or "")
    if not trade_currency:
        _fail(raw, "trade currency was not provided")
        report.normalisation_errors += 1
        return
    wallet = fill.get("walletImpact") if isinstance(fill.get("walletImpact"), dict) else {}
    rate = fx_rate_to_gbp(trade_currency, wallet)
    if rate is None:
        _fail(raw, "FX rate to GBP was not provided")
        report.normalisation_errors += 1
        return
    quantity = abs(D(str(fill["quantity"])))
    price = D(str(fill["price"]))
    if quantity <= 0:
        _fail(raw, "fill quantity was not positive")
        report.normalisation_errors += 1
        return
    fees_native, fees_gbp, fx_native, fx_gbp = split_taxes(wallet.get("taxes"), trade_currency, rate)
    broker_trade_id = f"fill:{fill['id']}"
    exists = session.scalar(
        select(Trade).where(Trade.account_id == account.id, Trade.broker_trade_id == broker_trade_id)
    )
    if exists is not None:
        raw.processing_status = ProcessingStatus.PROCESSED
        raw.processing_error = None
        return
    gross_native = quantity * price
    trade = Trade(
        account_id=account.id,
        instrument_id=instrument.id,
        action=TradeAction(order["side"]),
        quantity=q8(quantity),
        fill_price=q8(price),
        trade_currency=trade_currency,
        fx_rate_to_gbp=q8(rate),
        gross_value_native=q8(gross_native),
        gross_value_gbp=q8(gross_native * rate),
        fees_native=q8(fees_native),
        fees_gbp=q8(fees_gbp),
        fx_fee_native=q8(fx_native),
        fx_fee_gbp=q8(fx_gbp),
        executed_at=order_occurred_at(item),
        broker_trade_id=broker_trade_id,
        source_event_id=raw.id,
        data_origin=DataOrigin.LIVE,
    )
    session.add(trade)
    session.flush()
    raw.processing_status = ProcessingStatus.PROCESSED
    raw.processing_error = None
    report.trades_created += 1


def _archive_transaction(session, account, item, report: SyncReport) -> None:
    if not isinstance(item, dict):
        report.normalisation_errors += 1
        return
    event_id = transaction_event_id(item)
    try:
        occurred = parse_utc(item.get("dateTime"))
    except ValueError:
        raw = _insert_raw(session, event_id, "TRANSACTION", parse_utc("1970-01-01T00:00:00+00:00"), item, report)
        if raw is not None and raw.processing_status is ProcessingStatus.PENDING:
            _fail(raw, "transaction had no timestamp")
            report.normalisation_errors += 1
        return
    raw = _insert_raw(session, event_id, "TRANSACTION", occurred, item, report)
    if raw is None or raw.processing_status in {ProcessingStatus.PROCESSED, ProcessingStatus.IGNORED}:
        return
    if item.get("amount") is None or not item.get("currency"):
        _fail(raw, "transaction amount or currency was not provided")
        report.normalisation_errors += 1
        return
    _write_cash(
        session,
        account,
        raw,
        movement_type=transaction_type(item.get("type")),
        amount=D(str(item["amount"])),
        currency=str(item["currency"]),
        occurred=occurred,
        instrument=None,
        note=str(item.get("type") or "OTHER"),
        report=report,
        dividend=False,
    )


def _archive_dividend(session, account, item, catalogue, report: SyncReport) -> None:
    if not isinstance(item, dict):
        report.normalisation_errors += 1
        return
    event_id = dividend_event_id(item)
    try:
        occurred = parse_utc(item.get("paidOn"))
    except ValueError:
        raw = _insert_raw(session, event_id, "DIVIDEND", parse_utc("1970-01-01T00:00:00+00:00"), item, report)
        if raw is not None and raw.processing_status is ProcessingStatus.PENDING:
            _fail(raw, "dividend had no timestamp")
            report.normalisation_errors += 1
        return
    raw = _insert_raw(session, event_id, "DIVIDEND", occurred, item, report)
    if raw is None or raw.processing_status in {ProcessingStatus.PROCESSED, ProcessingStatus.IGNORED}:
        return
    if item.get("amount") is None or not item.get("currency"):
        _fail(raw, "dividend amount or currency was not provided")
        report.normalisation_errors += 1
        return
    ticker = str(item.get("ticker") or "")
    instrument = None
    if ticker:
        instrument = _instrument(session, ticker, listing_metadata(item.get("instrument"), catalogue.get(ticker)), report)
    elif item.get("instrument"):
        report.unmapped_instruments += 1
    _write_cash(
        session,
        account,
        raw,
        movement_type=CashMovementType.DIVIDEND,
        amount=D(str(item["amount"])),
        currency=str(item["currency"]),
        occurred=occurred,
        instrument=instrument,
        note="DIVIDEND",
        report=report,
        dividend=True,
    )


def _write_corporate(session, account, raw, item, catalogue, report: SyncReport) -> None:
    fill = item["fill"]
    ticker = order_ticker(item)
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    instrument = _instrument(session, ticker, listing_metadata(order.get("instrument"), catalogue.get(ticker)), report)
    if instrument is None:
        _fail(raw, "instrument could not be mapped")
        report.normalisation_errors += 1
        return
    fill_type = str(fill.get("type") or "")
    if fill_type == "REVERSE_SPLIT":
        action_type = CorporateActionType.REVERSE_SPLIT
    elif fill_type == "STOCK_SPLIT":
        action_type = CorporateActionType.STOCK_SPLIT
    else:
        action_type = CorporateActionType.OTHER
    exists = session.scalar(
        select(CorporateAction).where(
            CorporateAction.account_id == account.id,
            CorporateAction.broker_event_id == raw.broker_event_id,
        )
    )
    if exists is None:
        session.add(
            CorporateAction(
                account_id=account.id,
                instrument_id=instrument.id,
                action_type=action_type,
                effective_at=order_occurred_at(item),
                quantity_delta=q8(D(str(fill["quantity"]))),
                split_ratio_from=None,
                split_ratio_to=None,
                broker_event_id=raw.broker_event_id,
                source_event_id=raw.id,
                notes="Signed fill quantity. The broker did not provide a split ratio. This is not an investor buy or sell.",
                data_origin=DataOrigin.LIVE,
            )
        )
        session.flush()
        report.corporate_actions_created += 1
    raw.processing_status = ProcessingStatus.PROCESSED
    raw.processing_error = None


def rehome_stock_splits(session: Session) -> int:
    """Move stock-split trades into corporate_action.

    Those rows were stored as executions so quantity reconciliation could pass.
    A split is not an investor decision. Safe to run more than once.
    """
    rows = session.execute(
        select(Trade, RawBrokerEvent)
        .join(RawBrokerEvent, RawBrokerEvent.id == Trade.source_event_id)
        .where(Trade.data_origin == DataOrigin.LIVE)
    ).all()
    moved = 0
    for trade, raw in rows:
        payload = raw.raw_payload_json or {}
        fill = payload.get("fill") if isinstance(payload.get("fill"), dict) else {}
        if str(fill.get("type") or "") not in {"STOCK_SPLIT", "REVERSE_SPLIT"}:
            continue
        exists = session.scalar(
            select(CorporateAction).where(
                CorporateAction.account_id == trade.account_id,
                CorporateAction.broker_event_id == raw.broker_event_id,
            )
        )
        if exists is None:
            fill_type = str(fill.get("type") or "")
            action_type = CorporateActionType.REVERSE_SPLIT if fill_type == "REVERSE_SPLIT" else CorporateActionType.STOCK_SPLIT
            session.add(
                CorporateAction(
                    account_id=trade.account_id,
                    instrument_id=trade.instrument_id,
                    action_type=action_type,
                    effective_at=trade.executed_at,
                    quantity_delta=q8(D(str(fill["quantity"]))),
                    split_ratio_from=None,
                    split_ratio_to=None,
                    broker_event_id=raw.broker_event_id,
                    source_event_id=raw.id,
                    notes="Moved out of trade. Signed fill quantity. The broker did not provide a split ratio.",
                    data_origin=DataOrigin.LIVE,
                )
            )
            session.flush()
        session.execute(
            update(CashMovement).where(CashMovement.source_trade_id == trade.id).values(source_trade_id=None)
        )
        session.delete(trade)
        raw.processing_status = ProcessingStatus.PROCESSED
        raw.processing_error = None
        moved += 1
    session.flush()
    return moved


def _write_cash(session, account, raw, *, movement_type, amount, currency, occurred, instrument, note, report, dividend: bool) -> None:
    amount_gbp, fx_status = cash_gbp_fields(amount, currency)
    if fx_status.value == "MISSING":
        note = f"{note}; GBP amount unavailable"[:256]
    session.add(
        CashMovement(
            account_id=account.id,
            movement_type=movement_type,
            amount_gbp=amount_gbp,
            fx_conversion_status=fx_status,
            currency=currency,
            amount_native=q8(amount),
            instrument_id=None if instrument is None else instrument.id,
            occurred_at=occurred,
            source_event_id=raw.id,
            note=note[:256],
            data_origin=DataOrigin.LIVE,
        )
    )
    session.flush()
    raw.processing_status = ProcessingStatus.PROCESSED
    report.cash_created += 1
    if dividend:
        report.dividends_created += 1


def _insert_raw(session, event_id: str, event_type: str, occurred, payload: dict, report: SyncReport) -> RawBrokerEvent | None:
    existing = session.scalar(
        select(RawBrokerEvent).where(
            RawBrokerEvent.broker == Broker.TRADING212,
            RawBrokerEvent.broker_event_id == event_id,
        )
    )
    if existing is not None:
        report.raw_known += 1
        return existing
    raw = RawBrokerEvent(
        broker=Broker.TRADING212,
        broker_event_id=event_id,
        event_type=event_type,
        occurred_at=occurred,
        raw_payload_json=payload,
        processing_status=ProcessingStatus.PENDING,
        data_origin=DataOrigin.LIVE,
    )
    session.add(raw)
    session.flush()
    report.raw_inserted += 1
    return raw


def _instrument(session, ticker: str, meta: dict, report: SyncReport) -> Instrument | None:
    if not ticker:
        report.unmapped_instruments += 1
        return None
    found = session.scalar(select(Instrument).where(Instrument.trading212_ticker == ticker))
    if found is not None:
        apply_current_identity(session, found, ticker, meta if isinstance(meta, dict) else {})
        return found
    symbol = current_display_symbol(ticker, meta if isinstance(meta, dict) else None)
    by_symbol = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if by_symbol is not None:
        if by_symbol.trading212_ticker and by_symbol.trading212_ticker != ticker:
            report.unmapped_instruments += 1
            return None
        by_symbol.trading212_ticker = ticker
        apply_current_identity(session, by_symbol, ticker, meta if isinstance(meta, dict) else {})
        return by_symbol
    meta = meta if isinstance(meta, dict) else {}
    instrument = Instrument(
        symbol=symbol,
        company_name=str(meta.get("name") or meta.get("shortName") or symbol),
        asset_type=AssetType.EQUITY,
        exchange="UNAVAILABLE",
        currency=str(meta.get("currency") or meta.get("currencyCode") or ""),
        isin=meta.get("isin"),
        trading212_ticker=ticker,
        theme="Unassigned",
        data_origin=DataOrigin.LIVE,
    )
    session.add(instrument)
    session.flush()
    apply_current_identity(session, instrument, ticker, meta)
    return instrument


def _reconstruct_account(session: Session, account_id) -> dict[str, Decimal]:
    rows = session.execute(
        select(Trade, Instrument)
        .join(Instrument, Instrument.id == Trade.instrument_id)
        .where(Trade.account_id == account_id, Trade.data_origin == DataOrigin.LIVE)
    ).all()
    trades = []
    for trade, instrument in rows:
        ticker = instrument.trading212_ticker or instrument.symbol
        trades.append((ticker, trade.action, D(trade.quantity)))
    corporate_rows = session.execute(
        select(CorporateAction, Instrument)
        .join(Instrument, Instrument.id == CorporateAction.instrument_id)
        .where(CorporateAction.account_id == account_id)
    ).all()
    corporate = []
    for action, instrument in corporate_rows:
        if action.quantity_delta is None:
            continue
        ticker = instrument.trading212_ticker or instrument.symbol
        corporate.append((ticker, D(action.quantity_delta)))
    return reconstruct_quantities(trades, corporate)


def _duplicate_executions(session: Session, account_id) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(Trade)
        .where(Trade.account_id == account_id, Trade.data_origin == DataOrigin.LIVE)
    )
    distinct = session.scalar(
        select(func.count(func.distinct(Trade.broker_trade_id))).where(
            Trade.account_id == account_id,
            Trade.data_origin == DataOrigin.LIVE,
        )
    )
    if count is None or distinct is None:
        return 0
    return int(count) - int(distinct)


def _fail(raw: RawBrokerEvent, message: str) -> None:
    raw.processing_status = ProcessingStatus.FAILED
    raw.processing_error = message[:500]


def _safe_error(exc: Exception) -> str:
    text = exc.__class__.__name__
    return f"normalisation failed: {text}"


def _qty(value) -> str:
    if value is None:
        return "unavailable"
    return f"{D(value).normalize():f}"
