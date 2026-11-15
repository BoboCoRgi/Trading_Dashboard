"""Background Trading 212 refresh. No real broker credentials."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from connectors.trading212.client import Trading212TransientError
from core.config import Settings
from core.enums import SnapshotSource
from services.freshness import ai_status_line, broker_status_line, market_status_line
from services.t212_refresh import (
    RateQuota,
    RefreshState,
    apply_failure,
    apply_refresh_click,
    configure_jobs,
    failure_message,
    history_due,
    quota_pause,
    refresh_trading212,
    run_with_lock,
    scheduled_intervals,
    serve,
    worker_startup_plan,
)
from services.t212_sync import _history_page

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class MemSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class Lock:
    def __init__(self, held: bool = False) -> None:
        self.held = held
        self.released = 0

    def acquire(self) -> bool:
        if self.held:
            return False
        self.held = True
        return True

    def release(self) -> None:
        self.held = False
        self.released += 1


class Jobs:
    def __init__(self) -> None:
        self.current = 0
        self.history = 0
        self.portfolio = {"total": "50"}

    def current_fetch(self, *args, **kwargs):
        self.current += 1
        self.portfolio["total"] = "60"

    def history_fetch(self, *args, **kwargs):
        self.history += 1

    def fail_current(self, *args, **kwargs):
        self.current += 1
        raise Trading212TransientError("Trading 212 request failed (HTTP 429)")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        data_mode="live",
        t212_current_refresh_minutes=5,
        t212_history_refresh_minutes=30,
        t212_fresh_minutes=5,
        t212_rate_limit_floor=8,
    )


def _install(monkeypatch, jobs: Jobs, state: RefreshState | None = None) -> None:
    monkeypatch.setattr("services.t212_refresh._load_state", lambda session: state or RefreshState())
    monkeypatch.setattr("services.t212_refresh._run_current", jobs.current_fetch)
    monkeypatch.setattr("services.t212_refresh._run_history", jobs.history_fetch)
    monkeypatch.setattr("services.t212_refresh._mark_current_success", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.t212_refresh._mark_history_success", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.t212_refresh._mark_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.t212_refresh._store_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.t212_refresh._note_pause", lambda *args, **kwargs: None)


def test_broker_phrase_is_fresh_inside_five_minutes_and_stale_after():
    fresh = broker_status_line(NOW - timedelta(minutes=2), NOW, failed=False)
    stale = broker_status_line(NOW - timedelta(minutes=12), NOW, failed=False)
    just_now = broker_status_line(NOW - timedelta(seconds=20), NOW, failed=False)
    assert fresh == {"name": "Trading 212", "detail": "Updated 2 min ago", "tone": "ok"}
    assert stale["detail"] == "Stale · updated 12 min ago"
    assert stale["tone"] == "stale"
    assert just_now["detail"] == "Updated just now"
    failed = broker_status_line(NOW - timedelta(minutes=12), NOW, failed=True)
    assert "Refresh failed" in failed["detail"]
    assert "14:48 UTC" in failed["detail"]


def test_market_prices_and_ai_review_stay_separate():
    market = market_status_line(datetime(2026, 11, 14, 14, 51, tzinfo=timezone.utc))
    review = ai_status_line(datetime(2026, 11, 14, 14, 30, tzinfo=timezone.utc))
    assert market["name"] == "Market prices"
    assert market["detail"] == "Updated 14:51 UTC"
    assert review["name"] == "AI review"
    assert review["detail"] == "Based on 14:30 UTC data"
    assert market_status_line(None)["detail"] == "Not stored yet"
    assert ai_status_line(None)["detail"] == "No review stored"


def test_dashboard_status_does_not_call_trading212(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("Trading 212 client was constructed")

    monkeypatch.setattr("connectors.trading212.client.Trading212Client.__init__", boom)

    class FakeSession:
        def scalar(self, *args, **kwargs):
            return None

        def begin_nested(self):
            return self

        def commit(self):
            return None

        def rollback(self):
            return None

    from app.dashboard.system_status import collect_system_status

    status = collect_system_status(FakeSession(), data_mode="live", now=NOW)
    assert status["dashboard"] == "Running"
    assert status["database"] == "Connected"
    assert status["broker"] == "unavailable"


def test_rerun_without_the_button_does_not_refresh():
    calls = []
    assert apply_refresh_click(clicked=False, refresh=lambda: calls.append("go")) is None
    assert calls == []
    result = apply_refresh_click(clicked=True, refresh=lambda: "done")
    assert result == "done"
    assert apply_refresh_click(clicked=False, refresh=lambda: calls.append("again")) is None
    assert calls == []


def test_schedule_uses_five_minutes_for_current_and_thirty_for_history():
    settings = _settings()
    assert scheduled_intervals(settings) == {"t212_current": 5, "t212_history": 30}

    class Scheduler:
        def __init__(self) -> None:
            self.jobs = []

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append(kwargs | {"trigger": trigger})

    scheduler = Scheduler()
    configure_jobs(scheduler, settings, {"current": lambda: None, "history": lambda: None, "beat": lambda: None}, now=NOW)
    by_id = {job["id"]: job for job in scheduler.jobs}
    assert by_id["t212_current"]["minutes"] == 5
    assert by_id["t212_history"]["minutes"] == 30
    assert by_id["t212_history"]["next_run_time"] - by_id["t212_current"]["next_run_time"] == timedelta(minutes=25)
    assert worker_startup_plan(history_is_due=True) == ["current", "history"]
    assert worker_startup_plan(history_is_due=False) == ["current"]
    assert history_due(None, NOW, 30) is True
    assert history_due(NOW - timedelta(minutes=10), NOW, 30) is False
    assert history_due(NOW - timedelta(minutes=30), NOW, 30) is True


def test_manual_refresh_updates_current_data_and_checks_history(monkeypatch):
    jobs = Jobs()
    _install(monkeypatch, jobs)
    result = refresh_trading212(MemSession(), object(), _settings(), mode="manual", now=NOW)
    assert result.status == "ok"
    assert result.message == "Updated just now"
    assert jobs.current == 1
    assert jobs.history == 1
    assert jobs.portfolio["total"] == "60"


def test_current_job_does_not_run_history(monkeypatch):
    jobs = Jobs()
    _install(monkeypatch, jobs)
    result = refresh_trading212(MemSession(), object(), _settings(), mode="current", now=NOW)
    assert result.status == "ok"
    assert jobs.current == 1
    assert jobs.history == 0


def test_duplicate_refresh_is_refused_without_a_second_run():
    lock = Lock(held=True)
    result = run_with_lock(lock, lambda: (_ for _ in ()).throw(AssertionError("second refresh")))
    assert result.status == "busy"
    assert result.message == "Refresh already in progress"
    assert lock.released == 0


def test_low_quota_pauses_automatic_refresh_but_not_manual(monkeypatch):
    future = NOW + timedelta(minutes=4)
    pause = quota_pause(RateQuota(remaining=2, reset_at=future), now=NOW, floor=8)
    assert pause == future
    assert quota_pause(RateQuota(remaining=2, reset_at=NOW - timedelta(seconds=1)), now=NOW, floor=8) is None
    jobs = Jobs()
    _install(monkeypatch, jobs, RefreshState(quota=RateQuota(remaining=2, reset_at=future)))
    paused = refresh_trading212(MemSession(), object(), _settings(), mode="current", now=NOW)
    assert paused.status == "paused"
    assert jobs.current == 0
    manual = refresh_trading212(MemSession(), object(), _settings(), mode="manual", now=NOW)
    assert manual.status == "ok"
    assert jobs.current == 1


def test_recent_automatic_refresh_is_not_repeated(monkeypatch):
    jobs = Jobs()
    _install(monkeypatch, jobs, RefreshState(last_current_refresh_at=NOW - timedelta(seconds=20)))
    skipped = refresh_trading212(MemSession(), object(), _settings(), mode="current", now=NOW)
    assert skipped.status == "skipped"
    assert jobs.current == 0
    history = refresh_trading212(MemSession(), object(), _settings(), mode="history", now=NOW)
    assert history.status == "ok"
    assert jobs.history == 1
    assert jobs.current == 0


def test_api_failure_preserves_the_last_portfolio_and_reports_the_last_success(monkeypatch):
    jobs = Jobs()
    jobs.current_fetch = jobs.fail_current
    _install(monkeypatch, jobs, RefreshState(last_current_refresh_at=NOW - timedelta(minutes=12)))
    session = MemSession()
    result = refresh_trading212(session, object(), _settings(), mode="manual", now=NOW)
    assert result.status == "failed"
    assert result.message == failure_message(
        Trading212TransientError("Trading 212 request failed (HTTP 429)"),
        NOW - timedelta(minutes=12),
    )
    assert "Trading 212 refresh failed" in result.message
    assert "2026-11-14 14:48 UTC" in result.message
    assert jobs.portfolio["total"] == "50"
    assert jobs.history == 0
    assert jobs.current == 1
    assert session.rollbacks == 1


def test_failure_record_does_not_clear_the_last_good_refresh():
    row = SimpleNamespace(
        connection_status="ok",
        last_current_refresh_at=NOW - timedelta(minutes=12),
        last_snapshot_time=NOW - timedelta(minutes=15),
        last_refresh_error=None,
        position_count=4,
        updated_at=NOW - timedelta(minutes=12),
    )
    apply_failure(row, Trading212TransientError("Trading 212 request failed (HTTP 429)"))
    assert row.connection_status == "unavailable"
    assert row.last_current_refresh_at == NOW - timedelta(minutes=12)
    assert row.last_snapshot_time == NOW - timedelta(minutes=15)
    assert row.position_count == 4
    assert "429" in row.last_refresh_error


def test_live_refresh_uses_the_live_snapshot_service_and_does_not_seed(monkeypatch):
    captured = {}

    def fake_snapshot(session, client, settings, *, source, update_existing=False):
        captured["source"] = source
        captured["update_existing"] = update_existing

    def fake_history(session, client, positions=None, incremental=False):
        captured["incremental"] = incremental

    monkeypatch.setattr("services.t212_refresh.create_live_snapshot", fake_snapshot)
    monkeypatch.setattr("services.t212_refresh.sync_trading212_history", fake_history)
    from services.t212_refresh import _run_current, _run_history

    _run_current(None, None, None, source=SnapshotSource.SCHEDULER)
    _run_history(None, None)
    assert captured["source"] is SnapshotSource.SCHEDULER
    assert captured["update_existing"] is True
    assert captured["incremental"] is True
    text = (ROOT / "services" / "t212_refresh.py").read_text(encoding="utf-8")
    assert "seed_demo" not in text
    assert "scripts.seed" not in text
    assert "SnapshotSource.SEED" not in text


def test_history_page_is_incremental_only_when_ids_are_already_stored():
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def get_historical_orders(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = Client()
    assert _history_page(client, "orders", set()) == []
    assert client.calls == [{}]
    assert _history_page(client, "orders", {"fill:1"}) == []
    assert client.calls[1]["known_ids"] == {"fill:1"}
    assert client.calls[1]["identify"]({"fill": {"id": 1}}) == "fill:1"


def test_scheduler_shutdown_does_not_wait():
    class Scheduler:
        def __init__(self) -> None:
            self.running = True
            self.wait = None

        def start(self) -> None:
            raise KeyboardInterrupt

        def shutdown(self, wait: bool = True) -> None:
            self.wait = wait
            self.running = False

    scheduler = Scheduler()
    assert serve(scheduler) == 0
    assert scheduler.wait is False
    assert scheduler.running is False


def test_current_bucket_update_keeps_older_snapshots():
    pytest.importorskip("psycopg")
    from sqlalchemy import func, select, text

    from uuid import uuid4

    from core.enums import AssetType, Broker, DataOrigin
    from core.money import D, q8
    from db.models import BrokerAccount, Instrument, PortfolioSnapshot, PositionSnapshot
    from db.session import SessionLocal
    from services.t212_snapshot import _store_snapshot

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")
    token = uuid4().hex[:8]
    moment = datetime(2099, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=uuid4().int % 500000)
    try:
        account = BrokerAccount(
            broker=Broker.TRADING212,
            account_type="TEST",
            base_currency="GBP",
            display_name=f"refresh-{token}",
            active=True,
            data_origin=DataOrigin.LIVE,
        )
        instrument = Instrument(
            symbol=token[:8],
            company_name=token,
            asset_type=AssetType.EQUITY,
            exchange="TEST",
            currency="GBP",
            theme="Test",
            data_origin=DataOrigin.LIVE,
        )
        session.add_all([account, instrument])
        session.flush()
        older = moment - timedelta(minutes=15)
        row = {
            "instrument_id": instrument.id,
            "quantity": D("1"),
            "market_price": D("10"),
            "market_price_currency": "GBP",
            "fx_rate_to_gbp": D("1"),
            "market_value_gbp": D("10"),
            "average_cost_gbp": D("8"),
            "cost_basis_gbp": D("8"),
            "broker_average_price": D("8"),
            "broker_average_currency": "GBP",
            "unrealised_pnl_gbp": D("2"),
            "unrealised_pnl_pct": D("0.25"),
            "portfolio_weight": D("1"),
        }
        assert _store_snapshot(
            session,
            account_id=account.id,
            moment=older,
            source=SnapshotSource.SCHEDULER,
            total=D("10"),
            cash=D("0"),
            invested=D("10"),
            realised=None,
            rows=[row],
            top1=D("1"),
            top3=D("1"),
            top5=D("1"),
        )
        assert _store_snapshot(
            session,
            account_id=account.id,
            moment=moment,
            source=SnapshotSource.SCHEDULER,
            total=D("20"),
            cash=D("0"),
            invested=D("20"),
            realised=None,
            rows=[row],
            top1=D("1"),
            top3=D("1"),
            top5=D("1"),
        )
        changed = dict(row)
        changed["market_price"] = D("30")
        changed["market_value_gbp"] = D("30")
        assert _store_snapshot(
            session,
            account_id=account.id,
            moment=moment,
            source=SnapshotSource.MANUAL,
            total=D("30"),
            cash=D("0"),
            invested=D("30"),
            realised=None,
            rows=[changed],
            top1=D("1"),
            top3=D("1"),
            top5=D("1"),
            update_existing=True,
        )
        stored = session.scalars(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.data_origin == DataOrigin.LIVE, PortfolioSnapshot.snapshot_time.in_([older, moment]))
        ).all()

        def _key(value):
            current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return current.astimezone(timezone.utc).replace(microsecond=0)

        by_time = {_key(item.snapshot_time): item for item in stored}
        assert len(by_time) == 2
        assert by_time[_key(older)].total_portfolio_value_gbp == q8(D("10"))
        assert by_time[_key(moment)].total_portfolio_value_gbp == q8(D("30"))
        positions = session.scalar(
            select(func.count())
            .select_from(PositionSnapshot)
            .where(PositionSnapshot.portfolio_snapshot_id == by_time[_key(moment)].id)
        )
        assert positions == 1
    finally:
        session.rollback()
        session.close()
