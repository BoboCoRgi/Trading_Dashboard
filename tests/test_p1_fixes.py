"""P1 fixes: chart views do not store setups, daily P&L keeps a missing FX name visible, live decisions use the live book."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from analytics.market_stats import MarketBar
from app.dashboard.views import stock_detail
from core.enums import DataOrigin
from core.money import D, q8
from services.attribution import attribute_positions, last_session_move, reporting_fx
from services.setups import choose_attention, observe_displayed_bars, setup_id

NOW = datetime(2026, 11, 15, 18, 30, tzinfo=timezone.utc)


def test_opening_a_chart_does_not_call_the_setup_writer():
    source = inspect.getsource(stock_detail)
    assert "sync_displayed_bars" not in source
    assert "observe_displayed_bars" in source
    assert "session" not in inspect.signature(observe_displayed_bars).parameters


def test_the_same_candle_keeps_one_setup_id():
    moment = datetime(2026, 11, 13, 14, 30, tzinfo=timezone.utc)
    first = setup_id("abc", "5m", "zone:60:61", moment, DataOrigin.LIVE)
    again = setup_id("abc", "5m", "zone:60:61", moment, DataOrigin.LIVE)
    other = setup_id("abc", "5m", "zone:60:61", datetime(2026, 11, 10, tzinfo=timezone.utc), DataOrigin.LIVE)
    assert first == again
    assert first != other


def test_old_intraday_bounces_do_not_push_a_daily_warning_off_today():
    warning = _row("1D", "TREND_WARNING", datetime(2026, 10, 8, tzinfo=timezone.utc))
    friday = [
        _row("5m", "SUPPORT_BOUNCE", datetime(2026, 11, 13, 15, tzinfo=timezone.utc), key="a"),
        _row("5m", "SUPPORT_BOUNCE", datetime(2026, 11, 13, 16, tzinfo=timezone.utc), key="b"),
    ]
    older = [
        _row("5m", "SUPPORT_BOUNCE", datetime(2026, 11, 10, 15, tzinfo=timezone.utc), key=key)
        for key in ("c", "d", "e")
    ]
    chosen = choose_attention([*older, *friday, warning], NOW, limit=5)
    assert chosen[0].timeframe == "1D"
    assert [row.timeframe for row in chosen].count("5m") == 1
    assert all(row.candle_time.date().isoformat() != "2026-11-10" for row in chosen)


def test_usd_session_pnl_uses_the_stored_rate_and_names_a_missing_one():
    ready = _line("RKLB", "10", "64.07", "61.53", "0.75")
    blocked = _line("SPCX", "4", "140", "139", None)
    result = attribute_positions([ready, blocked])
    native = D("10") * (D("61.53") - D("64.07"))
    assert result["ready"] is True
    assert result["rows"][0]["contribution_gbp"] == q8(native * D("0.75"))
    assert result["rows"][0]["symbol"] == "RKLB"
    assert "SPCX: no GBP rate for USD" in result["reason"]
    assert reporting_fx("GBP", None) == D(1)
    assert reporting_fx("USD", None) is None
    empty = attribute_positions([blocked])
    assert empty["ready"] is False
    assert "no GBP rate" in empty["reason"]


def test_last_session_move_is_the_two_latest_daily_closes():
    bars = [
        _bar(datetime(2026, 11, 12, tzinfo=timezone.utc), "64.07"),
        _bar(datetime(2026, 11, 13, tzinfo=timezone.utc), "61.53"),
    ]
    previous, current, as_of = last_session_move(bars)
    assert previous == D("64.07")
    assert current == D("61.53")
    assert as_of == datetime(2026, 11, 13, tzinfo=timezone.utc)


def test_live_decision_form_lists_live_holdings_and_not_a_mock_only_name():
    pytest.importorskip("psycopg")
    from db.session import SessionLocal
    from services.queries import form_choices
    from tests.test_research_workspace import _instrument, _token

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")
    try:
        mock = _instrument(f"M{_token()}", DataOrigin.MOCK)
        session.add(mock)
        session.flush()
        live = form_choices(session, DataOrigin.LIVE)
        symbols = {row["symbol"] for row in live["instruments"]}
        assert "RKLB" in symbols
        assert mock.symbol not in symbols
        assert all(row["symbol"] != mock.symbol for row in live["instruments"])
    finally:
        session.rollback()
        session.close()


def test_observe_displayed_bars_returns_without_a_session():
    assert observe_displayed_bars(symbol="RKLB", timeframe="1D", bars=[]) == []


def _line(symbol, quantity, previous, current, fx):
    return {
        "instrument_id": symbol,
        "symbol": symbol,
        "quantity": D(quantity),
        "previous": D(previous),
        "current": D(current),
        "as_of": datetime(2026, 11, 13, tzinfo=timezone.utc),
        "currency": "USD",
        "fx": None if fx is None else D(fx),
        "fills": [],
    }


def _bar(start: datetime, close: str) -> MarketBar:
    price = D(close)
    return MarketBar(start, "1D", price, price, price, price, D("1"), "USD", "twelvedata")


def _row(timeframe: str, setup_type: str, candle_time: datetime, key: str = "zone"):
    return SimpleNamespace(
        timeframe=timeframe,
        setup_type=setup_type,
        status="CONFIRMED",
        current=True,
        symbol="RKLB",
        candle_time=candle_time,
        structure_key=key,
    )
