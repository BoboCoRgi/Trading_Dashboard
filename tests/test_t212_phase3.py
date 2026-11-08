"""Phase 3: unknown GBP amounts, corporate actions, snapshots, and cost basis.

No test in this file calls Trading 212.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from analytics.cost_basis import CostEvent, compare_average, reconstruct_cost
from analytics.snapshot_integrity import portfolio_weights, snapshot_bucket, snapshot_integrity
from connectors.trading212.history import cash_gbp_fields, classify_order, reconcile_positions, reconstruct_quantities
from core.enums import (
    AssetType,
    Broker,
    CashMovementType,
    CorporateActionType,
    DataOrigin,
    FxConversionStatus,
    ProcessingStatus,
    SnapshotSource,
    TradeAction,
)
from core.money import D
from db.models import BrokerAccount, CashMovement, CorporateAction, Instrument, PortfolioSnapshot, RawBrokerEvent, Trade


def test_unknown_gbp_amount_is_null_not_zero():
    amount, status = cash_gbp_fields(D("100"), "USD")
    assert amount is None
    assert status is FxConversionStatus.MISSING


def test_genuine_gbp_zero_stays_zero():
    amount, status = cash_gbp_fields(D("0"), "GBP")
    assert amount == D("0")
    assert status is FxConversionStatus.NOT_REQUIRED
    unknown, unknown_status = cash_gbp_fields(D("100"), "USD")
    assert unknown is None
    assert unknown_status is not status


def test_stock_split_is_a_corporate_action_and_not_a_user_trade():
    item = {
        "fill": {"id": 1, "quantity": "-200", "price": "0.28", "type": "STOCK_SPLIT", "filledAt": "2026-01-01T00:00:00Z"},
        "order": {
            "status": "FILLED",
            "side": "SELL",
            "ticker": "SMDM_US_EQ",
            "instrument": {"ticker": "SMDM_US_EQ"},
        },
    }
    assert classify_order(item) == "corporate"
    events = [
        CostEvent(at=_at(1), kind="BUY", quantity=D("200"), price=D("10"), currency="USD"),
        CostEvent(at=_at(2), kind="CORPORATE", quantity=D("-200"), currency="USD"),
        CostEvent(at=_at(2), kind="CORPORATE", quantity=D("1"), currency="USD"),
    ]
    investor_trades = [event for event in events if event.kind in {"BUY", "SELL"}]
    assert len(investor_trades) == 1


def test_trade_plus_corporate_action_reconstructs_quantity():
    totals = reconstruct_quantities(
        [("SMDM_US_EQ", TradeAction.BUY, D("200"))],
        [("SMDM_US_EQ", D("-200")), ("SMDM_US_EQ", D("1"))],
    )
    assert totals["SMDM_US_EQ"] == D("1")


def test_known_open_positions_still_pass_with_the_split_as_a_corporate_action():
    """The five live quantities, with the SMDM split applied as quantity deltas."""
    trades = [
        ("MU_US_EQ", TradeAction.BUY, D("12.1000365")),
        ("NOK_US_EQ", TradeAction.BUY, D("400.56706048")),
        ("SPCX_US_EQ", TradeAction.BUY, D("30")),
        ("VACQ_US_EQ", TradeAction.BUY, D("1282.18205969")),
        ("SMDM_US_EQ", TradeAction.BUY, D("895.6459")),
    ]
    corporate = [("SMDM_US_EQ", D("-895.6459")), ("SMDM_US_EQ", D("4.4782295"))]
    rebuilt = reconstruct_quantities(trades, corporate)
    api = [
        {"instrument": {"ticker": "MU_US_EQ"}, "quantity": "12.1000365"},
        {"instrument": {"ticker": "NOK_US_EQ"}, "quantity": "400.56706048"},
        {"instrument": {"ticker": "SPCX_US_EQ"}, "quantity": "30"},
        {"instrument": {"ticker": "VACQ_US_EQ"}, "quantity": "1282.18205969"},
        {"instrument": {"ticker": "SMDM_US_EQ"}, "quantity": "4.4782295"},
    ]
    result = reconcile_positions(api, rebuilt)
    assert result["overall"] == "PASS"
    assert result["matched"] == 5


def test_portfolio_weights_sum_to_the_invested_share_of_the_account():
    weights = portfolio_weights([D("25"), D("75")], D("200"))
    assert weights == [D("0.12500000"), D("0.37500000")]
    assert sum(weights, start=D(0)) == D("0.50000000")


def test_largest_position_concentration_is_its_weight():
    from analytics.concentration import concentration

    report = concentration([("VACQ", D("0.80")), ("MU", D("0.10")), ("NOK", D("0.04"))])
    assert report.largest_key == "VACQ"
    assert report.top1 == D("0.80")


def test_snapshot_bucket_is_stable_inside_one_interval():
    first = datetime(2026, 9, 29, 12, 7, tzinfo=timezone.utc)
    second = datetime(2026, 9, 29, 12, 14, 59, tzinfo=timezone.utc)
    assert snapshot_bucket(first, 15) == snapshot_bucket(second, 15)
    assert snapshot_bucket(first, 15) == datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_snapshot_integrity_reports_a_difference_without_changing_the_total():
    passed = snapshot_integrity(total=D("100.05"), position_market_value=D("80"), cash=D("20"))
    assert passed["status"] == "PASS"
    assert passed["total"] == D("100.05")
    warned = snapshot_integrity(total=D("100"), position_market_value=D("80"), cash=D("19.50"))
    assert warned["status"] == "WARN"
    assert warned["difference"] == D("0.50")
    failed = snapshot_integrity(total=D("100"), position_market_value=D("80"), cash=D("10"))
    assert failed["status"] == "FAIL"


def test_negative_fee_cashflow_increases_cost():
    state = reconstruct_cost(
        [CostEvent(at=_at(1), kind="BUY", quantity=D("10"), price=D("5"), fees_native=D("-1"), currency="USD", fx_rate_to_gbp=D("1"))]
    )
    assert state.cost_native == D("51")
    assert state.average_native == D("5.1")


def test_simple_buy_average_cost():
    state = reconstruct_cost([CostEvent(at=_at(1), kind="BUY", quantity=D("10"), price=D("5"), currency="USD", fx_rate_to_gbp=D("0.8"))])
    assert state.quantity == D("10")
    assert state.average_native == D("5")
    assert state.cost_native == D("50")
    assert state.average_gbp == D("4")


def test_multiple_buys_use_weighted_average_cost():
    state = reconstruct_cost(
        [
            CostEvent(at=_at(1), kind="BUY", quantity=D("10"), price=D("2"), currency="USD", fx_rate_to_gbp=D("1")),
            CostEvent(at=_at(2), kind="BUY", quantity=D("10"), price=D("4"), currency="USD", fx_rate_to_gbp=D("1")),
        ]
    )
    assert state.quantity == D("20")
    assert state.average_native == D("3")
    assert state.cost_native == D("60")


def test_partial_sell_removes_average_cost():
    state = reconstruct_cost(
        [
            CostEvent(at=_at(1), kind="BUY", quantity=D("10"), price=D("2"), currency="USD", fx_rate_to_gbp=D("1")),
            CostEvent(at=_at(2), kind="BUY", quantity=D("10"), price=D("4"), currency="USD", fx_rate_to_gbp=D("1")),
            CostEvent(at=_at(3), kind="SELL", quantity=D("5"), price=D("6"), currency="USD", fx_rate_to_gbp=D("1")),
        ]
    )
    assert state.quantity == D("15")
    assert state.average_native == D("3")
    assert state.cost_native == D("45")


def test_stock_split_changes_quantity_and_keeps_cost():
    state = reconstruct_cost(
        [
            CostEvent(at=_at(1), kind="BUY", quantity=D("200"), price=D("10"), currency="USD", fx_rate_to_gbp=D("0.75")),
            CostEvent(at=_at(2), kind="CORPORATE", quantity=D("-200"), currency="USD"),
            CostEvent(at=_at(2), kind="CORPORATE", quantity=D("1"), currency="USD"),
        ]
    )
    assert state.quantity == D("1")
    assert state.cost_native == D("2000")
    assert state.average_native == D("2000")
    assert state.gbp_complete is True
    assert state.cost_gbp == D("1500")


def test_cost_basis_mismatch_is_a_warning():
    assert compare_average(D("3"), D("4")) == "WARN"


def test_missing_broker_cost_basis_is_unavailable():
    assert compare_average(D("3"), None) == "UNAVAILABLE"


def test_missing_fx_leaves_gbp_cost_unknown():
    state = reconstruct_cost([CostEvent(at=_at(1), kind="BUY", quantity=D("2"), price=D("5"), currency="USD")])
    assert state.average_native == D("5")
    assert state.cost_gbp is None
    assert state.gbp_complete is False


def test_cash_rows_and_rehomed_splits_keep_zero_and_null_apart(monkeypatch):
    pytest.importorskip("psycopg")
    from db.session import SessionLocal
    from services.t212_sync import rehome_stock_splits

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")
    try:
        account = BrokerAccount(
            broker=Broker.TRADING212,
            account_type="LIVE",
            base_currency="GBP",
            display_name=f"phase3-test-{uuid4().hex[:8]}",
            active=True,
            external_account_ref=None,
            data_origin=DataOrigin.LIVE,
        )
        instrument = Instrument(
            symbol=f"P3{uuid4().hex[:6]}",
            company_name="Phase 3 test",
            asset_type=AssetType.EQUITY,
            exchange="UNAVAILABLE",
            currency="USD",
            trading212_ticker=f"P3_{uuid4().hex[:6]}_US_EQ",
            theme="Unassigned",
            data_origin=DataOrigin.LIVE,
        )
        session.add(account)
        session.add(instrument)
        session.flush()
        raw = RawBrokerEvent(
            broker=Broker.TRADING212,
            broker_event_id=f"fill:test-{uuid4().hex}",
            event_type="ORDER",
            occurred_at=_at(2),
            raw_payload_json={"fill": {"type": "STOCK_SPLIT", "quantity": "-4.5", "price": "1"}, "order": {"side": "SELL"}},
            processing_status=ProcessingStatus.PROCESSED,
            data_origin=DataOrigin.LIVE,
        )
        session.add(raw)
        session.flush()
        trade = Trade(
            account_id=account.id,
            instrument_id=instrument.id,
            action=TradeAction.SELL,
            quantity=D("4.5"),
            fill_price=D("1"),
            trade_currency="USD",
            fx_rate_to_gbp=D("0.75"),
            gross_value_native=D("4.5"),
            gross_value_gbp=D("3.375"),
            fees_native=D("0"),
            fees_gbp=D("0"),
            fx_fee_native=D("0"),
            fx_fee_gbp=D("0"),
            executed_at=_at(2),
            broker_trade_id=raw.broker_event_id,
            source_event_id=raw.id,
            data_origin=DataOrigin.LIVE,
        )
        session.add(trade)
        session.add(
            CashMovement(
                account_id=account.id,
                movement_type=CashMovementType.DEPOSIT,
                amount_gbp=None,
                fx_conversion_status=FxConversionStatus.MISSING,
                currency="USD",
                amount_native=D("100"),
                occurred_at=_at(1),
                note="DEPOSIT; GBP amount unavailable",
                data_origin=DataOrigin.LIVE,
            )
        )
        session.add(
            CashMovement(
                account_id=account.id,
                movement_type=CashMovementType.FEE,
                amount_gbp=D("0"),
                fx_conversion_status=FxConversionStatus.NOT_REQUIRED,
                currency="GBP",
                amount_native=D("0"),
                occurred_at=_at(1),
                note="FEE",
                data_origin=DataOrigin.LIVE,
            )
        )
        session.flush()
        moved = rehome_stock_splits(session)
        assert moved >= 1
        assert session.get(Trade, trade.id) is None
        corporate = session.scalar(
            select(CorporateAction).where(CorporateAction.broker_event_id == raw.broker_event_id)
        )
        assert corporate is not None
        assert corporate.action_type is CorporateActionType.STOCK_SPLIT
        assert corporate.quantity_delta == D("4.5") * -1 or corporate.quantity_delta == D("-4.5")
        assert corporate.quantity_delta == D("-4.50000000") or corporate.quantity_delta == D("-4.5")
        stored = session.scalars(select(CashMovement).where(CashMovement.account_id == account.id)).all()
        by_currency = {row.currency: row for row in stored}
        assert by_currency["USD"].amount_gbp is None
        assert by_currency["GBP"].amount_gbp == D("0")
        moment = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        from services.t212_snapshot import _store_snapshot

        first = _store_snapshot(
            session,
            account_id=account.id,
            moment=moment,
            source=SnapshotSource.MANUAL,
            total=D("100"),
            cash=D("40"),
            invested=D("60"),
            realised=None,
            rows=[
                {
                    "instrument_id": instrument.id,
                    "quantity": D("1"),
                    "market_price": D("60"),
                    "market_price_currency": "USD",
                    "fx_rate_to_gbp": D("1"),
                    "market_value_gbp": D("60"),
                    "average_cost_gbp": D("50"),
                    "cost_basis_gbp": D("50"),
                    "broker_average_price": D("50"),
                    "broker_average_currency": "USD",
                    "unrealised_pnl_gbp": D("10"),
                    "unrealised_pnl_pct": D("0.2"),
                    "portfolio_weight": D("0.6"),
                }
            ],
            top1=D("0.6"),
            top3=D("0.6"),
            top5=D("0.6"),
        )
        second = _store_snapshot(
            session,
            account_id=account.id,
            moment=moment,
            source=SnapshotSource.SCHEDULER,
            total=D("100"),
            cash=D("40"),
            invested=D("60"),
            realised=None,
            rows=[],
            top1=D("0.6"),
            top3=D("0.6"),
            top5=D("0.6"),
        )
        assert first is True
        assert second is False
        count = session.scalar(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.data_origin == DataOrigin.LIVE,
                PortfolioSnapshot.snapshot_time == moment,
            )
        )
        assert count is not None
    finally:
        session.rollback()
        session.close()


def _at(hour: int) -> datetime:
    return datetime(2026, 1, 1, hour, tzinfo=timezone.utc)
