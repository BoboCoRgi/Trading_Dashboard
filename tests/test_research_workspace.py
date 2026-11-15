"""Research workspace: holdings, watchlist identity, thesis, and stored events."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.dashboard.components import page_banner
from app.dashboard.views.decisions import decision_form_ready
from core.enums import (
    AliasType,
    AssetType,
    Broker,
    CatalystStatus,
    DataOrigin,
    DecisionStatus,
    DecisionType,
    EventClassification,
    SnapshotSource,
    ThesisStatus,
    WatchlistStatus,
)
from core.money import D
from db.models import (
    BrokerAccount,
    Decision,
    Instrument,
    InstrumentAlias,
    PortfolioSnapshot,
    PositionSnapshot,
    Thesis,
    WatchlistItem,
)
from services.portfolio_source import LivePortfolioProvider
from services.queries import ai_review, research_view
from services.research import (
    SECTIONS,
    apply_open,
    assemble_universe,
    dive_context,
    event_timeline,
    events_for,
    fallback_source,
    load_universe,
    remove_watch,
    resolve_instrument,
    returns_from_closes,
    save_thesis,
    thesis_context,
    add_watch,
)


def test_sections_are_the_four_workspace_tabs():
    assert SECTIONS == ("Watchlist", "Theses", "Catalysts & Events", "Deep Dive")


def test_current_holding_appears_once_and_keeps_its_instrument_id():
    rows = assemble_universe(
        holdings=[
            {
                "instrument_id": "holding-1",
                "symbol": "AAA",
                "company": "Aaa Plc",
                "weight": Decimal("0.20"),
            }
        ],
        watches=[{"instrument_id": "holding-1", "symbol": "AAA", "company": "Aaa Plc", "status": "PASS"}],
        references=[],
        theses={"holding-1": {"active": True, "reviewed": "2026-11-01"}},
        catalysts={"holding-1": "Results"},
        prices={"holding-1": {"price": Decimal("10"), "currency": "GBP", "day": Decimal("0.01"), "month": None}},
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["instrument_id"] == "holding-1"
    assert rows[0]["instrument_id"] != rows[0]["symbol"]
    assert rows[0]["status"] == "HOLDING"
    assert rows[0]["removable"] is False
    assert rows[0]["active_thesis"] is True
    assert rows[0]["next_catalyst"] == "Results"


def test_manual_watch_and_referenced_name_use_their_own_status():
    rows = assemble_universe(
        holdings=[],
        watches=[{"instrument_id": "watch-1", "symbol": "BBB", "company": "Bee", "status": "WATCHING"}],
        references=[{"instrument_id": "ref-1", "symbol": "CCC", "company": "Cee"}],
        theses={},
        catalysts={},
        prices={},
    )
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["BBB"]["status"] == "WATCHING"
    assert by_symbol["BBB"]["removable"] is True
    assert by_symbol["BBB"]["instrument_id"] == "watch-1"
    assert by_symbol["CCC"]["status"] == "RESEARCH"
    assert by_symbol["CCC"]["removable"] is False


def test_opening_a_ticker_selects_that_instrument_for_deep_dive():
    state = {}
    apply_open(state, "instrument-42")
    assert state["research_instrument_id"] == "instrument-42"
    assert state["research_section_widget"] == "Deep Dive"


def test_live_mode_does_not_show_the_mock_data_banner():
    live = page_banner(
        {
            "mode": "live",
            "label": "LIVE · Trading 212",
            "ready": True,
            "stale": False,
            "broker_phrase": "Updated 2 min ago",
        }
    )
    assert "LIVE · Trading 212" in live
    assert "Mock data" not in live
    assert "Brokers are not connected" not in live
    fallback = page_banner(fallback_source("live"))
    assert "Mock data" not in fallback
    assert "MOCK DATA" not in fallback
    assert "Brokers are not connected" not in fallback


def test_missing_source_follows_the_configured_data_mode(monkeypatch):
    monkeypatch.setattr("core.config.get_settings", lambda: type("Settings", (), {"data_mode": "live"})())
    live_fallback = page_banner(None)
    assert "LIVE · Trading 212" in live_fallback
    assert "Mock data" not in live_fallback
    assert "MOCK DATA" not in live_fallback
    assert "Brokers are not connected" not in live_fallback

    monkeypatch.setattr("core.config.get_settings", lambda: type("Settings", (), {"data_mode": "mock"})())
    mock_fallback = page_banner(None)
    assert "MOCK DATA" in mock_fallback
    assert "Brokers are not connected" in mock_fallback
    assert "LIVE · Trading 212" not in mock_fallback


def test_decision_form_waits_for_an_instrument_and_keeps_other_decisions_readable():
    assert decision_form_ready({"instruments": []}) is False
    assert decision_form_ready({"instruments": [{"symbol": "RKLB"}]}) is True
    assert decision_form_ready({"instruments": [{"symbol": "RKLB"}]}, {"symbol": "SPCX"}) is False
    assert decision_form_ready({"instruments": [{"symbol": "RKLB"}]}, {"symbol": "RKLB"}) is True


def test_empty_events_do_not_crash_and_are_not_invented():
    assert event_timeline([], []) == []
    event = _Event()
    catalyst = _Catalyst()
    rows = event_timeline([event], [catalyst])
    assert [row["kind"] for row in rows] == ["unusual market move", "catalyst"]
    assert rows[0]["title"] == "Company"
    assert "Earnings" not in rows[0]["title"]
    assert rows[1]["when_label"] == "Next quarter"
    assert all(row["source"] == "stored" for row in rows)


def test_stored_closes_drive_day_and_month_changes():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    result = returns_from_closes(
        [
            (start, Decimal("100"), "GBP"),
            (start + timedelta(days=43), Decimal("110"), "GBP"),
            (start + timedelta(days=44), Decimal("121"), "GBP"),
        ]
    )
    assert result["price"] == Decimal("121")
    assert result["day"] == Decimal("0.1")
    assert result["month"] == Decimal("0.21")


def test_thesis_context_carries_the_fields_gemini_reads():
    thesis = Thesis(
        instrument_id=uuid4(),
        title="Note",
        thesis_summary="Demand holds",
        time_horizon="2 years",
        bull_case="Bull",
        base_case="Base",
        bear_case="Bear",
        catalysts="Launch",
        invalidation_conditions="Margin breaks",
        assumptions="Pricing power",
        risks="Competition",
        conviction=4,
        last_reviewed_at=datetime(2026, 11, 14, tzinfo=timezone.utc),
        status=ThesisStatus.ACTIVE,
        data_origin=DataOrigin.LIVE,
    )
    payload = thesis_context(thesis)
    assert payload["summary"] == "Demand holds"
    assert payload["assumptions"] == "Pricing power"
    assert payload["risks"] == "Competition"
    assert payload["conviction"] == 4
    assert payload["invalidation_conditions"] == "Margin breaks"
    assert payload["status"] == "ACTIVE"
    assert payload["last_reviewed"].startswith("2026-11-14")


def test_live_holding_appears_in_the_watchlist_by_instrument_id():
    with _session() as session:
        book = _holding(session)
        rows = load_universe(session, LivePortfolioProvider())
        match = next(row for row in rows if row["symbol"] == book["symbol"])
        assert match["status"] == "HOLDING"
        assert match["instrument_id"] == book["instrument_id"]
        assert match["removable"] is False
        assert match["price"] == D("10")


def test_manually_added_ticker_persists_on_the_canonical_instrument():
    with _session() as session:
        instrument = _instrument(f"N{_token()}", DataOrigin.LIVE)
        alias = f"O{_token()}"
        session.add(instrument)
        session.flush()
        session.add(
            InstrumentAlias(
                instrument_id=instrument.id,
                symbol=alias,
                alias_type=AliasType.HISTORICAL_TICKER,
                source="TEST",
            )
        )
        session.flush()
        before = session.scalar(select(func.count()).select_from(Instrument))
        found = resolve_instrument(session, alias.lower(), origin=DataOrigin.LIVE)
        added = add_watch(session, alias, origin=DataOrigin.LIVE, status=WatchlistStatus.WATCHING)
        after = session.scalar(select(func.count()).select_from(Instrument))
        assert found.id == instrument.id
        assert added["instrument_id"] == str(instrument.id)
        assert before == after
        session.expire_all()
        stored = session.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument.id))
        assert stored is not None
        assert stored.status == WatchlistStatus.WATCHING
        rows = load_universe(session, LivePortfolioProvider())
        match = next(row for row in rows if row["instrument_id"] == str(instrument.id))
        assert match["symbol"] == instrument.symbol
        assert match["status"] == "WATCHING"
        changed = add_watch(session, instrument.symbol, origin=DataOrigin.LIVE, status=WatchlistStatus.PASS)
        assert changed["instrument_id"] == str(instrument.id)
        assert session.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.instrument_id == instrument.id)) == 1


def test_thesis_create_and_edit_persist_and_show_in_deep_dive():
    with _session() as session:
        book = _holding(session)
        before = session.scalar(select(func.count()).select_from(Decision))
        created = save_thesis(
            session,
            book["instrument_id"],
            {"summary": "First view", "bull_case": "Growth", "conviction": 2, "status": "ACTIVE"},
            origin=DataOrigin.LIVE,
        )
        created_rows = load_universe(session, LivePortfolioProvider())
        created_match = next(row for row in created_rows if row["instrument_id"] == book["instrument_id"])
        assert created_match["active_thesis"] is True
        edited = save_thesis(
            session,
            book["instrument_id"],
            {
                "summary": "Revised view",
                "bull_case": "Growth",
                "bear_case": "Competition",
                "assumptions": "Pricing power",
                "risks": "Dilution",
                "catalysts": "Launch",
                "invalidation": "Guide cut",
                "conviction": 5,
                "time_horizon": "18 months",
                "status": "WATCH",
            },
            origin=DataOrigin.LIVE,
        )
        after = session.scalar(select(func.count()).select_from(Decision))
        assert created == edited
        assert before == after
        session.expire_all()
        context = dive_context(session, book["instrument_id"], origin=DataOrigin.LIVE)
        assert context["instrument_id"] == book["instrument_id"]
        assert context["thesis"]["summary"] == "Revised view"
        assert context["thesis"]["assumptions"] == "Pricing power"
        assert context["thesis"]["risks"] == "Dilution"
        assert context["thesis"]["invalidation_conditions"] == "Guide cut"
        assert context["thesis"]["conviction"] == 5
        assert context["thesis"]["status"] == "WATCH"
        assert context["thesis"]["last_reviewed"] is not None
        assert context["events"] == []
        rows = load_universe(session, LivePortfolioProvider())
        match = next(row for row in rows if row["instrument_id"] == book["instrument_id"])
        assert match["active_thesis"] is False
        assert match["status"] == "HOLDING"


def test_empty_event_query_returns_an_empty_list():
    with _session() as session:
        instrument = _instrument(f"E{_token()}", DataOrigin.LIVE)
        session.add(instrument)
        session.flush()
        assert events_for(session, str(instrument.id)) == []


def test_held_name_cannot_be_removed_and_a_watch_can():
    with _session() as session:
        book = _holding(session)
        refused = remove_watch(session, book["instrument_id"], held=True)
        assert refused["removed"] is False
        instrument = _instrument(f"W{_token()}", DataOrigin.LIVE)
        session.add(instrument)
        session.flush()
        add_watch(session, instrument.symbol, origin=DataOrigin.LIVE)
        removed = remove_watch(session, str(instrument.id), held=False)
        assert removed["removed"] is True
        assert session.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument.id)) is None


def test_existing_decision_and_review_queries_still_return():
    with _session() as session:
        view = research_view(session)
        review = ai_review(session)
        assert {"instruments", "watchlist", "theses", "catalysts", "notes"} <= set(view)
        assert "runs" in review and "totals" in review
        assert DecisionType.BUY.value == "BUY"
        assert DecisionStatus.OPEN.value == "OPEN"


class _Event:
    instrument_id = "event-1"
    event_date = datetime(2026, 11, 1, tzinfo=timezone.utc)
    event_classification = EventClassification.COMPANY
    context_note = "Stored move"
    price_change_pct = Decimal("-0.04")


class _Catalyst:
    instrument_id = "event-1"
    created_at = datetime(2026, 11, 2, tzinfo=timezone.utc)
    description = "Contract note already on file"
    expected_window = "Next quarter"
    status = CatalystStatus.UPCOMING


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


def _token() -> str:
    return uuid4().hex[:6].upper()


def _instrument(symbol: str, origin: DataOrigin) -> Instrument:
    return Instrument(
        symbol=symbol,
        company_name=f"{symbol} Plc",
        asset_type=AssetType.EQUITY,
        exchange="TEST",
        currency="GBP",
        theme="Test",
        data_origin=origin,
    )


def _holding(session) -> dict:
    symbol = f"H{_token()}"
    instrument = _instrument(symbol, DataOrigin.LIVE)
    account = BrokerAccount(
        broker=Broker.TRADING212,
        account_type="TEST",
        base_currency="GBP",
        display_name=f"live-{symbol}",
        active=True,
        data_origin=DataOrigin.LIVE,
    )
    session.add_all([instrument, account])
    session.flush()
    snapshot = PortfolioSnapshot(
        snapshot_time=datetime(2199, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=uuid4().int % 50000),
        total_portfolio_value_gbp=D("100"),
        cash_gbp=D("10"),
        invested_value_gbp=D("90"),
        daily_pnl_gbp=D("0"),
        daily_pnl_pct=D("0"),
        ath_portfolio_value_gbp=D("100"),
        drawdown_from_ath=D("0"),
        max_drawdown_to_date=D("0"),
        top1_concentration=D("0.5"),
        top3_concentration=D("0.5"),
        top5_concentration=D("0.5"),
        realised_pnl_gbp=D("0"),
        source=SnapshotSource.MANUAL,
        data_origin=DataOrigin.LIVE,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        PositionSnapshot(
            portfolio_snapshot_id=snapshot.id,
            account_id=account.id,
            instrument_id=instrument.id,
            quantity=D("2"),
            market_price=D("10"),
            market_price_currency="GBP",
            fx_rate_to_gbp=D("1"),
            market_value_gbp=D("20"),
            average_cost_gbp=D("8"),
            cost_basis_gbp=D("16"),
            unrealised_pnl_gbp=D("4"),
            unrealised_pnl_pct=D("0.25"),
            portfolio_weight=D("0.2"),
            data_origin=DataOrigin.LIVE,
        )
    )
    session.flush()
    return {"symbol": symbol, "instrument_id": str(instrument.id)}
