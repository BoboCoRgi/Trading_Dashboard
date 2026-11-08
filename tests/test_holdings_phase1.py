"""Phase 1 holdings maths. No broker API and no fabricated market bars."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from analytics.holdings import AccountLot, combine_lots
from analytics.market_stats import (
    MarketBar,
    cumulative_intraday_volume,
    daily_relative_volume,
    intraday_relative_volume,
    intraday_vwap,
)
from core.money import D
from services.holdings import chart_cost_lines


def _lot(broker: str, quantity: str, average: str | None, currency: str = "USD", cost: str | None = None) -> AccountLot:
    return AccountLot(
        symbol="RKLB",
        company="Rocket Lab",
        instrument_id="same",
        account_id=broker,
        broker_label=broker,
        account_label=broker,
        quantity=D(quantity),
        price=D("30"),
        price_currency="USD",
        market_value_gbp=D(quantity),
        cost_basis_gbp=None if cost is None else D(cost),
        average_cost_gbp=None if average is None else D(average),
        broker_average=None if average is None else D(average),
        broker_average_currency=None if average is None else currency,
        unrealised_gbp=D("1"),
        unrealised_pct=D("0.01"),
        weight=D("0.10"),
        daily_change_pct=D("0.01"),
        daily_pnl_gbp=D("2"),
    )


def test_combined_quantity_weights_average_cost_by_quantity():
    combined = combine_lots([_lot("Trading 212", "500", "10", cost="5000"), _lot("IBKR", "800", "20", cost="16000")])
    assert combined is not None
    assert combined.quantity == D("1300")
    assert combined.gbp_cost.value == D("16.15384615")
    assert combined.gbp_cost.value != D("15")
    assert "equally" not in combined.gbp_cost.method
    assert combined.native_cost.value == D("16.15384615")
    assert combined.native_cost.currency == "USD"


def test_missing_account_cost_does_not_invent_a_combined_average():
    combined = combine_lots([_lot("Trading 212", "500", "10", cost="5000"), _lot("IBKR", "800", None)])
    assert combined is not None
    assert combined.quantity == D("1300")
    assert combined.gbp_cost.value is None
    assert combined.native_cost.value is None


def test_different_cost_currencies_are_not_combined():
    first = _lot("Trading 212", "500", "10", currency="USD", cost="5000")
    second = _lot("IBKR", "800", "20", currency="GBP", cost="16000")
    combined = combine_lots([first, second])
    assert combined is not None
    assert combined.gbp_cost.value == D("16.15384615")
    assert combined.native_cost.value is None
    assert "different currencies" in combined.native_cost.method


def test_cost_line_is_not_drawn_in_a_different_currency():
    combined = combine_lots([_lot("Trading 212", "500", "10", currency="USD", cost="5000")])
    assert chart_cost_lines(combined, "USD")
    assert chart_cost_lines(combined, "GBP") == []


def test_daily_relative_volume_uses_twenty_full_sessions():
    prior = [D("100")] * 20
    assert daily_relative_volume(session_volume=D("180"), prior_session_volumes=prior) == D("1.80000000")
    assert daily_relative_volume(session_volume=D("180"), prior_session_volumes=prior[:-1]) is None


def test_intraday_relative_volume_is_not_a_full_day_ratio():
    prior = [D("25")] * 20
    assert intraday_relative_volume(cumulative_to_clock=D("50"), prior_cumulative_to_same_clock=prior) == D("2.00000000")
    assert intraday_relative_volume(cumulative_to_clock=D("50"), prior_cumulative_to_same_clock=prior[:-1]) is None


def test_cumulative_intraday_volume_refuses_daily_bars():
    start = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    daily = MarketBar(start, "1D", D("1"), D("2"), D("1"), D("2"), D("1000"), "USD", "test")
    assert cumulative_intraday_volume([daily], as_of=start) is None
    bars = [
        MarketBar(start, "1m", D("10"), D("12"), D("9"), D("11"), D("30"), "USD", "test"),
        MarketBar(start + timedelta(minutes=30), "1m", D("11"), D("12"), D("10"), D("12"), D("20"), "USD", "test"),
        MarketBar(start + timedelta(hours=2), "1m", D("12"), D("13"), D("11"), D("12"), D("100"), "USD", "test"),
    ]
    as_of = start + timedelta(minutes=30)
    assert cumulative_intraday_volume(bars, as_of=as_of) == D("50")


def test_relative_lines_share_one_start_date():
    from connectors.market_data.provider import SeriesNote
    from services.holdings import _relative

    def bar(day: int, close: str) -> MarketBar:
        return MarketBar(datetime(2026, 10, day, tzinfo=timezone.utc), "1D", None, None, None, D(close), None, "USD", "test")

    class Stub:
        def get_market_benchmark(self, symbol: str) -> SeriesNote:
            return SeriesNote([bar(1, "100"), bar(2, "110"), bar(3, "120")], "mapped", label="QQQ")

        def get_sector_benchmark(self, symbol: str) -> SeriesNote:
            return SeriesNote([bar(2, "50"), bar(3, "40")], "mapped", label="SOX")

    result = _relative("MU", [bar(1, "10"), bar(2, "20"), bar(3, "30")], Stub())
    assert result["ready"] is True
    dates = [point["date"] for point in result["series"][0]["points"]]
    assert dates == ["2026-10-02", "2026-10-03"]
    assert all(line["points"][0]["value"] == 100 for line in result["series"])
    assert [line["name"] for line in result["series"]] == ["MU", "QQQ", "SOX"]


def test_price_and_volume_share_an_axis_and_name_the_volume():
    from app.dashboard.charts import price_volume_chart

    bars = [
        {
            "time": "2026-10-01T14:00:00+00:00",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 30.0,
            "cumulative_volume": 30.0,
        },
        {
            "time": "2026-10-01T14:30:00+00:00",
            "open": 11.0,
            "high": 13.0,
            "low": 10.0,
            "close": 12.0,
            "volume": 20.0,
            "cumulative_volume": 50.0,
            "vwap": 11.5,
        },
    ]
    candle = price_volume_chart(
        bars,
        style="candle",
        trades=[{"time": bars[0]["time"], "price": 10.0, "action": "BUY", "hover": "BUY fill"}],
        cost_lines=[{"name": "Combined broker average", "price": 10.5}],
        averages={},
        show_volume=True,
    )
    kinds = [trace.type for trace in candle.data]
    assert "candlestick" in kinds
    assert "bar" in kinds
    assert candle.data[-1].yaxis == "y2"
    hover = candle.data[0].hovertext[1]
    assert "Bar volume (this bar)" in hover
    assert "Cumulative intraday volume" in hover
    assert "VWAP" in hover
    assert candle.layout.shapes
    line = price_volume_chart(bars, style="line", trades=[], cost_lines=[], averages={}, show_volume=False)
    assert line.data[0].type == "scatter"
    assert all(trace.type != "bar" for trace in line.data)


def test_vwap_uses_intraday_bars_and_refuses_daily_bars():
    start = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    daily = [MarketBar(start, "1D", D("10"), D("12"), D("9"), D("11"), D("100"), "USD", "test")]
    assert intraday_vwap(daily) is None
    bars = [
        MarketBar(start, "1m", D("10"), D("13"), D("10"), D("13"), D("100"), "USD", "test"),
        MarketBar(start + timedelta(minutes=1), "1m", D("20"), D("22"), D("14"), D("20"), D("100"), "USD", "test"),
    ]
    # typical prices 12 and 18.66666667, equal volume, average 15.33333333
    assert intraday_vwap(bars) == D("15.33333333")
    missing = [MarketBar(start, "1m", None, None, None, D("11"), None, "USD", "close")]
    assert intraday_vwap(missing) is None


def test_live_holdings_ignore_mock_rows_and_do_not_invent_bars():
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
    from uuid import uuid4

    from sqlalchemy import text

    from core.enums import AssetType, Broker, DataOrigin, SnapshotSource
    from db.models import BrokerAccount, Instrument, PortfolioSnapshot, PositionSnapshot
    from db.session import SessionLocal
    from services.portfolio_source import LivePortfolioProvider

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")
    try:
        token = uuid4().hex[:6]
        symbol = f"H{token}"
        instrument = Instrument(
            symbol=symbol,
            company_name="Holdings test",
            asset_type=AssetType.EQUITY,
            exchange="TEST",
            currency="USD",
            theme="Test",
            data_origin=DataOrigin.LIVE,
        )
        live = BrokerAccount(
            broker=Broker.TRADING212,
            account_type="TEST",
            base_currency="GBP",
            display_name=f"t212-{token}",
            active=True,
            data_origin=DataOrigin.LIVE,
        )
        other = BrokerAccount(
            broker=Broker.IBKR,
            account_type="TEST",
            base_currency="GBP",
            display_name=f"ibkr-{token}",
            active=True,
            data_origin=DataOrigin.LIVE,
        )
        session.add_all([instrument, live, other])
        session.flush()
        moment = datetime(2097, 5, 2, 16, 0, tzinfo=timezone.utc)
        snapshot = PortfolioSnapshot(
            snapshot_time=moment,
            total_portfolio_value_gbp=D("1000"),
            cash_gbp=D("100"),
            invested_value_gbp=D("900"),
            daily_pnl_gbp=D("0"),
            daily_pnl_pct=D("0"),
            ath_portfolio_value_gbp=D("1000"),
            drawdown_from_ath=D("0"),
            max_drawdown_to_date=D("0"),
            top1_concentration=D("0.5"),
            top3_concentration=D("0.5"),
            top5_concentration=D("0.5"),
            source=SnapshotSource.MANUAL,
            data_origin=DataOrigin.LIVE,
        )
        session.add(snapshot)
        session.flush()
        for account, quantity, cost in ((live, "500", "5000"), (other, "800", "16000")):
            session.add(
                PositionSnapshot(
                    portfolio_snapshot_id=snapshot.id,
                    account_id=account.id,
                    instrument_id=instrument.id,
                    quantity=D(quantity),
                    market_price=D("30"),
                    market_price_currency="USD",
                    fx_rate_to_gbp=D("0.78"),
                    market_value_gbp=D(quantity),
                    average_cost_gbp=D(cost) / D(quantity),
                    cost_basis_gbp=D(cost),
                    broker_average_price=D(cost) / D(quantity),
                    broker_average_currency="USD",
                    unrealised_pnl_gbp=D("1"),
                    unrealised_pnl_pct=D("0.01"),
                    portfolio_weight=D("0.2"),
                    data_origin=DataOrigin.LIVE,
                )
            )
        mock_instrument = Instrument(
            symbol=f"M{token}",
            company_name="Mock only",
            asset_type=AssetType.EQUITY,
            exchange="TEST",
            currency="USD",
            theme="Test",
            data_origin=DataOrigin.MOCK,
        )
        mock_account = BrokerAccount(
            broker=Broker.TRADING212,
            account_type="TEST",
            base_currency="GBP",
            display_name=f"mock-{token}",
            active=True,
            data_origin=DataOrigin.MOCK,
        )
        session.add_all([mock_instrument, mock_account])
        session.flush()
        mock_snapshot = PortfolioSnapshot(
            snapshot_time=datetime(2097, 6, 2, 16, 0, tzinfo=timezone.utc),
            total_portfolio_value_gbp=D("5"),
            cash_gbp=D("1"),
            invested_value_gbp=D("4"),
            daily_pnl_gbp=D("0"),
            daily_pnl_pct=D("0"),
            ath_portfolio_value_gbp=D("5"),
            drawdown_from_ath=D("0"),
            max_drawdown_to_date=D("0"),
            top1_concentration=D("1"),
            top3_concentration=D("1"),
            top5_concentration=D("1"),
            source=SnapshotSource.MANUAL,
            data_origin=DataOrigin.MOCK,
        )
        session.add(mock_snapshot)
        session.flush()
        session.add(
            PositionSnapshot(
                portfolio_snapshot_id=mock_snapshot.id,
                account_id=mock_account.id,
                instrument_id=mock_instrument.id,
                quantity=D("9999"),
                market_price=D("1"),
                market_price_currency="USD",
                fx_rate_to_gbp=D("1"),
                market_value_gbp=D("1"),
                portfolio_weight=D("0.9"),
                data_origin=DataOrigin.MOCK,
            )
        )
        session.flush()
        from services.holdings import holdings_report, stock_detail

        report = holdings_report(session, LivePortfolioProvider())
        assert report["ready"] is True
        row = next(item for item in report["combined"] if item.symbol == symbol)
        assert row.quantity == D("1300")
        assert row.gbp_cost.value == D("16.15384615")
        assert all(item.symbol != f"M{token}" for item in report["combined"])
        detail = stock_detail(session, symbol, LivePortfolioProvider())
        assert detail["daily"].bars == []
        assert "no daily" in detail["daily"].note.lower()
        assert detail["intraday"].bars == []
        assert detail["vwap"]["value"] is None
    finally:
        session.rollback()
        session.close()
