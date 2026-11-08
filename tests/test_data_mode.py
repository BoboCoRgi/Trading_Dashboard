"""DATA_MODE chooses the mock book or the latest live snapshot. It never mixes them."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from core.config import Settings
from core.enums import AssetType, Broker, DataOrigin, SnapshotSource
from core.money import D
from db.models import BrokerAccount, Instrument, PortfolioSnapshot, PositionSnapshot
from services.portfolio_source import (
    LIVE_SNAPSHOT_COMMAND,
    LivePortfolioProvider,
    MockPortfolioProvider,
    describe_source,
    portfolio_provider,
)
from services.queries import risk_view, today_view


def test_data_mode_selects_the_provider():
    assert isinstance(portfolio_provider(Settings(_env_file=None, data_mode="mock")), MockPortfolioProvider)
    assert isinstance(portfolio_provider(Settings(_env_file=None, data_mode=" LIVE ")), LivePortfolioProvider)
    rejected = portfolio_provider(Settings(_env_file=None, data_mode="both"))
    view = today_view(None, rejected)
    assert view["ready"] is False
    assert "mock or live" in view["message"]
    assert "positions" not in view


def test_missing_live_snapshot_is_explicit_and_does_not_fall_back():
    class EmptyLive(LivePortfolioProvider):
        def latest_snapshot(self, session):
            return None

    view = today_view(None, EmptyLive())
    risk = risk_view(None, EmptyLive())
    assert view["ready"] is False
    assert risk["ready"] is False
    assert view["message"] == "No live Trading 212 snapshot is available yet."
    assert view["command"] == LIVE_SNAPSHOT_COMMAND
    assert "create_t212_live_snapshot.py" in view["command"]
    assert view["source"]["origin"] == "LIVE"
    assert "positions" not in view
    assert "summary" not in view


def test_live_snapshot_is_marked_stale_after_the_interval():
    moment = datetime(2026, 10, 11, 16, 42, tzinfo=timezone.utc)
    snapshot = PortfolioSnapshot(
        snapshot_time=moment,
        total_portfolio_value_gbp=D("1"),
        cash_gbp=D("1"),
        invested_value_gbp=D("0"),
        top1_concentration=D("0"),
        top3_concentration=D("0"),
        top5_concentration=D("0"),
        source=SnapshotSource.MANUAL,
        data_origin=DataOrigin.LIVE,
    )
    stale = describe_source(
        LivePortfolioProvider(),
        snapshot,
        now=moment + timedelta(minutes=20),
        interval_minutes=15,
    )
    fresh = describe_source(
        LivePortfolioProvider(),
        snapshot,
        now=moment + timedelta(minutes=10),
        interval_minutes=15,
    )
    mock = describe_source(
        MockPortfolioProvider(),
        snapshot,
        now=moment + timedelta(days=2),
        interval_minutes=15,
    )
    assert stale["stale"] is True
    assert stale["snapshot_clock"] == "16:42"
    assert stale["age_label"] == "Last updated 20 minutes ago"
    assert fresh["stale"] is False
    assert fresh["age_label"] is None
    assert mock["stale"] is False
    assert mock["label"] == "MOCK DATA"


@contextmanager
def _session():
    pytest.importorskip("psycopg")
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _stamp(day: int) -> datetime:
    return datetime(2098, 5, day, 16, 42, tzinfo=timezone.utc)


def _book(session):
    token = uuid4().hex[:8]
    mock_symbol = f"M{token[:6]}"
    old_symbol = f"O{token[:6]}"
    new_symbol = f"N{token[:6]}"
    mock_account = BrokerAccount(
        broker=Broker.TRADING212,
        account_type="TEST",
        base_currency="GBP",
        display_name=f"mock-{token}",
        active=True,
        data_origin=DataOrigin.MOCK,
    )
    live_account = BrokerAccount(
        broker=Broker.TRADING212,
        account_type="TEST",
        base_currency="GBP",
        display_name=f"live-{token}",
        active=True,
        data_origin=DataOrigin.LIVE,
    )
    mock_instrument = _instrument(mock_symbol, DataOrigin.MOCK)
    old_instrument = _instrument(old_symbol, DataOrigin.LIVE)
    new_instrument = _instrument(new_symbol, DataOrigin.LIVE)
    session.add_all([mock_account, live_account, mock_instrument, old_instrument, new_instrument])
    session.flush()
    mock_snapshot = _snapshot(_stamp(1), DataOrigin.MOCK, "810810")
    old_snapshot = _snapshot(_stamp(2), DataOrigin.LIVE, "820820")
    new_snapshot = _snapshot(_stamp(3), DataOrigin.LIVE, "830830")
    session.add_all([mock_snapshot, old_snapshot, new_snapshot])
    session.flush()
    session.add_all(
        [
            _position(mock_snapshot, mock_account, mock_instrument, DataOrigin.MOCK),
            _position(old_snapshot, live_account, old_instrument, DataOrigin.LIVE),
            _position(new_snapshot, live_account, new_instrument, DataOrigin.LIVE),
            _position(new_snapshot, live_account, mock_instrument, DataOrigin.MOCK),
        ]
    )
    session.flush()
    return {
        "mock_symbol": mock_symbol,
        "old_symbol": old_symbol,
        "new_symbol": new_symbol,
        "new_id": str(new_snapshot.id),
        "mock_total": 810810.0,
        "new_total": 830830.0,
    }


def _instrument(symbol: str, origin: DataOrigin) -> Instrument:
    return Instrument(
        symbol=symbol,
        company_name=symbol,
        asset_type=AssetType.EQUITY,
        exchange="TEST",
        currency="GBP",
        theme="Test",
        data_origin=origin,
    )


def _snapshot(moment: datetime, origin: DataOrigin, total: str) -> PortfolioSnapshot:
    amount = D(total)
    return PortfolioSnapshot(
        snapshot_time=moment,
        total_portfolio_value_gbp=amount,
        cash_gbp=D("10"),
        invested_value_gbp=amount - D("10"),
        daily_pnl_gbp=D("0"),
        daily_pnl_pct=D("0"),
        ath_portfolio_value_gbp=amount,
        drawdown_from_ath=D("0"),
        max_drawdown_to_date=D("0"),
        top1_concentration=D("0.5"),
        top3_concentration=D("0.5"),
        top5_concentration=D("0.5"),
        realised_pnl_gbp=D("0"),
        source=SnapshotSource.MANUAL,
        data_origin=origin,
    )


def _position(snapshot, account, instrument, origin: DataOrigin) -> PositionSnapshot:
    return PositionSnapshot(
        portfolio_snapshot_id=snapshot.id,
        account_id=account.id,
        instrument_id=instrument.id,
        quantity=D("1"),
        market_price=D("10"),
        market_price_currency="GBP",
        fx_rate_to_gbp=D("1"),
        market_value_gbp=D("10"),
        average_cost_gbp=D("8"),
        cost_basis_gbp=D("8"),
        unrealised_pnl_gbp=D("2"),
        unrealised_pnl_pct=D("0.25"),
        portfolio_weight=D("0.5"),
        data_origin=origin,
    )


def test_data_mode_mock_returns_mock_portfolio():
    with _session() as session:
        book = _book(session)
        view = today_view(session, portfolio_provider(Settings(_env_file=None, data_mode="mock")))
        assert view["ready"] is True
        assert view["source"]["origin"] == "MOCK"
        assert view["source"]["label"] == "MOCK DATA"
        assert {row["symbol"] for row in view["positions"]} == {book["mock_symbol"]}
        assert book["new_symbol"] not in {row["symbol"] for row in view["positions"]}


def test_data_mode_live_returns_live_portfolio():
    with _session() as session:
        book = _book(session)
        provider = portfolio_provider(Settings(_env_file=None, data_mode="live"))
        view = today_view(session, provider)
        risk = risk_view(session, provider)
        assert view["ready"] is True
        assert view["source"]["label"] == "LIVE · Trading 212"
        assert view["source"]["origin"] == "LIVE"
        assert view["source"]["snapshot_clock"] == "16:42"
        assert {row["symbol"] for row in view["positions"]} == {book["new_symbol"]}
        assert risk["source"]["snapshot_id"] == view["source"]["snapshot_id"]
        assert book["new_symbol"] in {row["symbol"] for row in risk["grid"]}


def test_live_mode_never_returns_mock_rows():
    with _session() as session:
        book = _book(session)
        from app.dashboard.series import allocation_rows, snapshot_history

        view = today_view(session, LivePortfolioProvider())
        symbols = {row["symbol"] for row in view["positions"]}
        assert book["mock_symbol"] not in symbols
        assert book["old_symbol"] not in symbols
        assert all(point["value"] != book["mock_total"] for point in view["value_series"])
        labels = {row["label"] for row in allocation_rows(session, LivePortfolioProvider())}
        assert book["mock_symbol"] not in labels
        history = snapshot_history(session, LivePortfolioProvider())
        assert history[-1]["value"] == book["new_total"]
        assert all(point["value"] != book["mock_total"] for point in history)


def test_latest_live_snapshot_is_selected():
    with _session() as session:
        book = _book(session)
        view = today_view(session, LivePortfolioProvider())
        assert view["source"]["snapshot_id"] == book["new_id"]
        assert view["as_of"] == "2098-05-03 16:42 UTC"
        assert view["value_series"][-1]["value"] == book["new_total"]


def test_position_rows_belong_to_the_selected_live_snapshot():
    with _session() as session:
        book = _book(session)
        view = today_view(session, LivePortfolioProvider())
        rows = session.execute(
            select(PositionSnapshot, Instrument)
            .join(Instrument, Instrument.id == PositionSnapshot.instrument_id)
            .where(PositionSnapshot.portfolio_snapshot_id == view["source"]["snapshot_id"])
        ).all()
        live_symbols = {instrument.symbol for position, instrument in rows if position.data_origin is DataOrigin.LIVE}
        planted = {instrument.symbol for position, instrument in rows if position.data_origin is DataOrigin.MOCK}
        assert live_symbols == {row["symbol"] for row in view["positions"]}
        assert planted == {book["mock_symbol"]}
        assert book["mock_symbol"] not in {row["symbol"] for row in view["positions"]}
