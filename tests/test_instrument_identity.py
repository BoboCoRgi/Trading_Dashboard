"""Current ticker comes from listing metadata. The broker ticker can stay historical."""

from connectors.trading212.client import current_display_symbol, explicit_current_symbol, listing_metadata
from services.instrument_identity import apply_current_identity


def test_current_symbol_uses_metadata_short_name():
    meta = {"ticker": "VACQ_US_EQ", "shortName": "RKLB", "name": "Rocket Lab", "isin": "US7731211089"}
    assert explicit_current_symbol(meta) == "RKLB"
    assert current_display_symbol("VACQ_US_EQ", meta) == "RKLB"
    assert current_display_symbol("VACQ_US_EQ", {"name": "Rocket Lab"}) == "VACQ"


def test_catalogue_short_name_wins_over_the_order_payload():
    merged = listing_metadata(
        {"ticker": "VACQ_US_EQ", "name": "Rocket Lab", "isin": "US7731211089", "currency": "USD"},
        {"ticker": "VACQ_US_EQ", "shortName": "RKLB", "name": "Rocket Lab", "currencyCode": "USD"},
    )
    assert merged["shortName"] == "RKLB"
    assert current_display_symbol("VACQ_US_EQ", merged) == "RKLB"


def test_historical_payload_does_not_restore_the_old_symbol():
    pytest = __import__("pytest")
    psycopg = pytest.importorskip("psycopg")
    try:
        probe = psycopg.connect(
            "postgresql://invest:invest@127.0.0.1:5432/investment_intelligence",
            connect_timeout=3,
        )
        probe.close()
    except Exception as exc:
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")

    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy import func, select

    from core.enums import AliasType, AssetType, Broker, DataOrigin, ProcessingStatus, TradeAction
    from core.money import D
    from db.models import BrokerAccount, Instrument, InstrumentAlias, RawBrokerEvent, Trade
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        token = uuid4().hex[:8]
        instrument = Instrument(
            symbol=f"OLD{token}",
            company_name="Rocket Lab",
            asset_type=AssetType.EQUITY,
            exchange="UNAVAILABLE",
            currency="USD",
            isin="US7731211089",
            trading212_ticker=f"OLD{token}_US_EQ",
            theme="Unassigned",
            data_origin=DataOrigin.LIVE,
        )
        other = Instrument(
            symbol=f"OTHER{token}",
            company_name="Other listing",
            asset_type=AssetType.EQUITY,
            exchange="UNAVAILABLE",
            currency="EUR",
            isin="US7731211089",
            trading212_ticker=f"EUR_{token}_EQ",
            theme="Unassigned",
            data_origin=DataOrigin.LIVE,
        )
        account = BrokerAccount(
            broker=Broker.TRADING212,
            account_type="TEST",
            base_currency="GBP",
            display_name=f"id-{token}",
            active=True,
            data_origin=DataOrigin.LIVE,
        )
        session.add_all([instrument, other, account])
        session.flush()
        moment = datetime(2024, 12, 3, tzinfo=timezone.utc)
        raw = RawBrokerEvent(
            broker=Broker.TRADING212,
            broker_event_id=f"id-{token}",
            event_type="ORDER",
            occurred_at=moment,
            raw_payload_json={"order": {"ticker": "VACQ_US_EQ", "instrument": {"ticker": "VACQ_US_EQ"}}},
            processing_status=ProcessingStatus.PROCESSED,
            data_origin=DataOrigin.LIVE,
        )
        session.add(raw)
        session.flush()
        trade = Trade(
            account_id=account.id,
            instrument_id=instrument.id,
            action=TradeAction.BUY,
            quantity=D("1"),
            fill_price=D("10"),
            trade_currency="USD",
            fx_rate_to_gbp=D("0.78"),
            gross_value_native=D("10"),
            gross_value_gbp=D("8"),
            fees_native=D("0"),
            fees_gbp=D("0"),
            fx_fee_native=D("0"),
            fx_fee_gbp=D("0"),
            executed_at=moment,
            source_event_id=raw.id,
            broker_trade_id=f"trade-{token}",
            data_origin=DataOrigin.LIVE,
        )
        session.add(trade)
        session.flush()
        instrument_id = instrument.id
        catalogue = {
            "ticker": instrument.trading212_ticker,
            "shortName": f"NEW{token}",
            "name": "Rocket Lab",
            "isin": "US7731211089",
            "currencyCode": "USD",
        }
        changed = apply_current_identity(session, instrument, instrument.trading212_ticker, catalogue)
        assert changed == f"OLD{token}"
        assert instrument.symbol == f"NEW{token}"
        assert instrument.id == instrument_id
        assert instrument.trading212_ticker == f"OLD{token}_US_EQ"
        apply_current_identity(
            session,
            instrument,
            instrument.trading212_ticker,
            {"ticker": instrument.trading212_ticker, "name": "Rocket Lab", "currency": "USD"},
        )
        assert instrument.symbol == f"NEW{token}"
        apply_current_identity(session, other, other.trading212_ticker, {"shortName": f"ALT{token}", "name": "Rocket Lab"})
        assert other.symbol == f"ALT{token}"
        assert instrument.symbol == f"NEW{token}"
        aliases = session.scalars(select(InstrumentAlias).where(InstrumentAlias.instrument_id == instrument_id)).all()
        kinds = {(row.symbol, row.alias_type) for row in aliases}
        assert (f"OLD{token}", AliasType.HISTORICAL_TICKER) in kinds
        assert (f"NEW{token}", AliasType.CURRENT_TICKER) in kinds
        assert session.scalar(select(func.count()).select_from(InstrumentAlias).where(InstrumentAlias.instrument_id == instrument_id)) == 2
        linked = session.scalar(select(Trade).where(Trade.id == trade.id))
        assert linked.instrument_id == instrument_id
        assert raw.raw_payload_json["order"]["ticker"] == "VACQ_US_EQ"
    finally:
        session.rollback()
        session.close()
