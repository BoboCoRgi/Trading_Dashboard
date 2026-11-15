"""Trading 212 refresh that is independent of Streamlit reruns.

One process holds the refresh lock. The dashboard reads PostgreSQL.
This module does not place orders and does not seed mock data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from connectors.trading212.client import RateLimitInfo, Trading212Client, Trading212Error
from core.config import Settings, get_settings
from core.enums import Broker, SnapshotSource
from db.models import AIRun, BrokerSyncStatus, MarketPriceBar
from db.session import engine, session_scope
from services.freshness import ai_status_line, broker_status_line, market_status_line, worker_label
from services.t212_snapshot import create_live_snapshot
from services.t212_sync import sync_trading212_history

logger = logging.getLogger(__name__)

LOCK_KEY = 872121205
RECENT_SECONDS = 45


@dataclass(frozen=True)
class RefreshResult:
    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class RateQuota:
    remaining: int | None = None
    reset_at: datetime | None = None


@dataclass(frozen=True)
class RefreshState:
    connection_status: str = "not_checked"
    last_current_refresh_at: datetime | None = None
    last_history_refresh_at: datetime | None = None
    last_snapshot_time: datetime | None = None
    last_refresh_error: str | None = None
    next_current_refresh_at: datetime | None = None
    next_history_refresh_at: datetime | None = None
    worker_heartbeat_at: datetime | None = None
    quota: RateQuota = RateQuota()


class PostgresRefreshLock:
    """One session-level advisory lock. Released before the connection returns to the pool."""

    def __init__(self, bind=None) -> None:
        self.bind = bind if bind is not None else engine
        self.conn = None
        self.held = False

    def acquire(self) -> bool:
        self.conn = self.bind.connect()
        got = self.conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar()
        self.held = bool(got)
        if not self.held:
            self._close()
        return self.held

    def release(self) -> None:
        if self.conn is None:
            return
        try:
            if self.held:
                self.conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                self.conn.commit()
        finally:
            self._close()

    def _close(self) -> None:
        conn = self.conn
        self.conn = None
        self.held = False
        if conn is not None:
            conn.close()


def run_with_lock(lock, work) -> RefreshResult:
    if not lock.acquire():
        return RefreshResult("busy", "Refresh already in progress")
    try:
        return work()
    finally:
        lock.release()


def apply_refresh_click(*, clicked: bool, refresh):
    """A Streamlit rerun calls this with clicked False and must not touch the broker."""
    if not clicked:
        return None
    return refresh()


def quota_pause(quota: RateQuota, *, now: datetime, floor: int) -> datetime | None:
    """When remaining quota is low, wait until the advertised reset. Do not spin."""
    if quota.remaining is None or quota.remaining >= floor:
        return None
    if quota.reset_at is not None and quota.reset_at > now:
        return quota.reset_at
    if quota.reset_at is not None and quota.reset_at <= now:
        return None
    return now + timedelta(seconds=60)


def history_due(last_history: datetime | None, now: datetime, minutes: int) -> bool:
    if last_history is None:
        return True
    left = last_history if last_history.tzinfo is not None else last_history.replace(tzinfo=timezone.utc)
    right = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return right.astimezone(timezone.utc) - left.astimezone(timezone.utc) >= timedelta(minutes=minutes)


def worker_startup_plan(*, history_is_due: bool) -> list[str]:
    plan = ["current"]
    if history_is_due:
        plan.append("history")
    return plan


def scheduled_intervals(settings: Settings) -> dict[str, int]:
    return {
        "t212_current": settings.t212_current_refresh_minutes,
        "t212_history": settings.t212_history_refresh_minutes,
    }


def configure_jobs(scheduler, settings: Settings, jobs: dict, *, now: datetime) -> None:
    """Current data every five minutes. History is the slower job. Neither fires again immediately."""
    windows = refresh_schedule_kwargs(settings, now=now)
    scheduler.add_job(
        jobs["current"],
        "interval",
        minutes=settings.t212_current_refresh_minutes,
        id="t212_current",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        **windows["t212_current"],
    )
    scheduler.add_job(
        jobs["history"],
        "interval",
        minutes=settings.t212_history_refresh_minutes,
        id="t212_history",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        **windows["t212_history"],
    )
    scheduler.add_job(
        jobs["beat"],
        "interval",
        seconds=30,
        id="t212_heartbeat",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        **windows["t212_heartbeat"],
    )


def refresh_schedule_kwargs(settings: Settings, *, now: datetime) -> dict[str, dict]:
    """First automatic runs are one interval after the opening refresh."""
    return {
        "t212_current": {"next_run_time": now + timedelta(minutes=settings.t212_current_refresh_minutes)},
        "t212_history": {"next_run_time": now + timedelta(minutes=settings.t212_history_refresh_minutes)},
        "t212_heartbeat": {"next_run_time": now},
    }


def public_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    lowered = text.lower()
    if "://" in text or any(word in lowered for word in ("password", "secret", "api_key", "api-key")):
        return "Trading 212 refresh failed"
    return "Trading 212 refresh failed"


def failure_message(exc: BaseException, last_success: datetime | None) -> str:
    from services.freshness import full_clock

    return f"{public_error(exc)}. Last successful update: {full_clock(last_success)}"


def is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, Trading212Error) and "429" in str(exc)


def apply_failure(row, exc: BaseException) -> None:
    """Record the failure. Stored portfolio fields on this status row are left as they were."""
    row.connection_status = "unavailable"
    row.last_refresh_error = _safe_detail(exc)
    row.updated_at = datetime.now(timezone.utc)


def _safe_detail(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    lowered = text.lower()
    if not text or "://" in text or any(word in lowered for word in ("password", "secret", "api_key", "api-key")):
        return exc.__class__.__name__
    return text[:180]


def _run_current(session, client, settings, *, source: SnapshotSource):
    return create_live_snapshot(session, client, settings, source=source, update_existing=True)


def _run_history(session, client):
    return sync_trading212_history(session, client, incremental=True)


def refresh_trading212(session, client, settings: Settings, *, mode: str, now: datetime | None = None) -> RefreshResult:
    """Current data, and for manual or history mode an incremental broker-event check.

    A failure rolls back the snapshot write from this attempt. The last committed portfolio stays.
    """
    moment = now or datetime.now(timezone.utc)
    state = _load_state(session)
    manual = mode == "manual"
    if not manual:
        pause_until = quota_pause(state.quota, now=moment, floor=settings.t212_rate_limit_floor)
        if pause_until is not None:
            _note_pause(session, pause_until)
            session.commit()
            logger.info("Trading 212 refresh paused until %s", pause_until.isoformat())
            return RefreshResult("paused", "Trading 212 refresh paused until the rate limit resets")
        if mode == "current" and _too_recent(state.last_current_refresh_at, moment):
            return RefreshResult("skipped", "Trading 212 was just refreshed")
    source = SnapshotSource.MANUAL if manual else SnapshotSource.SCHEDULER
    current_ok = False
    if mode in {"current", "manual"}:
        try:
            _run_current(session, client, settings, source=source)
            _mark_current_success(session, client, moment)
            session.commit()
            current_ok = True
        except Exception as exc:
            session.rollback()
            _mark_failure(session, exc)
            _store_rate_limit(session, client)
            session.commit()
            logger.warning("Trading 212 current refresh failed: %s", _safe_detail(exc))
            return RefreshResult("failed", failure_message(exc, state.last_current_refresh_at))
    if mode in {"history", "manual"}:
        try:
            _run_history(session, client)
            _mark_history_success(session, client, moment)
            session.commit()
        except Exception as exc:
            session.rollback()
            if not current_ok:
                _mark_failure(session, exc)
            _store_rate_limit(session, client)
            session.commit()
            logger.warning("Trading 212 history refresh failed: %s", _safe_detail(exc))
            if current_ok:
                return RefreshResult("ok", "Updated just now. History check failed.")
            return RefreshResult("failed", failure_message(exc, state.last_current_refresh_at))
    if mode == "history":
        return RefreshResult("ok", "History checked")
    return RefreshResult("ok", "Updated just now")


def request_manual_refresh() -> RefreshResult:
    """Button entry. Called only after an explicit click, not on a plain rerun."""
    settings = get_settings()
    if settings.data_mode.strip().lower() != "live":
        return RefreshResult("skipped", "Trading 212 refresh is used in live mode")
    if not settings.t212_enabled or not settings.t212_read_only:
        return RefreshResult("failed", "Trading 212 refresh failed. Last successful update: —")
    if not settings.t212_api_key.strip() or not settings.t212_api_secret.strip():
        return RefreshResult("failed", "Trading 212 refresh failed. Last successful update: —")
    try:
        client = Trading212Client(settings)
        lock = PostgresRefreshLock(engine)
        return run_with_lock(lock, lambda: _manual_body(client, settings))
    except Exception as exc:
        logger.warning("Trading 212 manual refresh failed: %s", _safe_detail(exc))
        return RefreshResult("failed", "Trading 212 refresh failed. Last successful update: —")


def _manual_body(client, settings: Settings) -> RefreshResult:
    with session_scope() as session:
        return refresh_trading212(session, client, settings, mode="manual")


def run_scheduled(mode: str) -> RefreshResult:
    settings = get_settings()
    if settings.data_mode.strip().lower() != "live" or not settings.t212_read_only:
        return RefreshResult("skipped", "Trading 212 refresh is not active")
    try:
        client = Trading212Client(settings)
        lock = PostgresRefreshLock(engine)
        return run_with_lock(lock, lambda: _scheduled_body(client, settings, mode))
    except Exception as exc:
        logger.warning("Trading 212 scheduled refresh failed: %s", _safe_detail(exc))
        return RefreshResult("failed", public_error(exc))


def _scheduled_body(client, settings: Settings, mode: str) -> RefreshResult:
    with session_scope() as session:
        return refresh_trading212(session, client, settings, mode=mode)


def record_heartbeat(
    session,
    *,
    now: datetime,
    next_current: datetime | None = None,
    next_history: datetime | None = None,
    update_schedule: bool = True,
) -> None:
    row = _status_row(session)
    row.worker_heartbeat_at = now
    if update_schedule:
        row.next_current_refresh_at = next_current
        row.next_history_refresh_at = next_history
    row.updated_at = now


def mark_worker_stopped(session) -> None:
    row = _status_row(session)
    row.worker_heartbeat_at = None
    row.updated_at = datetime.now(timezone.utc)


def load_dashboard_freshness(session, *, now: datetime | None = None, live: bool = True) -> dict:
    """Database-only freshness. No broker client is constructed."""
    moment = now or datetime.now(timezone.utc)
    state = _load_state(session)
    market_at = _optional(session, lambda: session.scalar(select(func.max(MarketPriceBar.fetched_at))))
    ai_basis = _optional(session, lambda: _latest_ai_basis(session))
    failed = bool(state.last_refresh_error) and state.connection_status != "ok"
    lines = []
    if live:
        lines.append(broker_status_line(state.last_current_refresh_at or state.last_snapshot_time, moment, failed=failed))
    lines.append(market_status_line(market_at))
    lines.append(ai_status_line(ai_basis))
    worker = worker_label(state.worker_heartbeat_at, moment)
    broker = "unavailable"
    if live and state.connection_status == "ok" and not failed:
        broker = "Connected"
    return {
        "lines": lines,
        "broker": broker,
        "worker": worker,
        "last_refresh": state.last_current_refresh_at or state.last_snapshot_time,
        "next_refresh": state.next_current_refresh_at if worker == "Running" else None,
        "failed": failed,
        "last_error": state.last_refresh_error,
        "phrase": lines[0]["detail"] if live and lines else None,
        "phrase_stale": bool(live and lines and lines[0]["tone"] in {"stale", "failed"}),
    }


def _latest_ai_basis(session) -> datetime | None:
    run = session.scalar(select(AIRun).order_by(AIRun.created_at.desc()).limit(1))
    if run is None:
        return None
    context = run.input_context_json if isinstance(run.input_context_json, dict) else {}
    totals = context.get("totals") if isinstance(context, dict) else None
    raw = totals.get("snapshot_time") if isinstance(totals, dict) else None
    parsed = _parse_moment(raw)
    if parsed is not None:
        return parsed
    return run.created_at


def _parse_moment(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if text.endswith(" UTC"):
        text = text[:-4] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _too_recent(last: datetime | None, now: datetime) -> bool:
    if last is None:
        return False
    left = last if last.tzinfo is not None else last.replace(tzinfo=timezone.utc)
    right = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return (right - left).total_seconds() < RECENT_SECONDS


def _note_pause(session, resume_at: datetime) -> None:
    row = _status_row(session)
    row.next_current_refresh_at = resume_at
    row.updated_at = datetime.now(timezone.utc)


def _mark_current_success(session, client, now: datetime) -> None:
    row = _status_row(session)
    row.connection_status = "ok"
    row.last_current_refresh_at = now
    row.last_refresh_error = None
    row.updated_at = now
    _store_rate_limit(session, client, row=row)


def _mark_history_success(session, client, now: datetime) -> None:
    row = _status_row(session)
    row.last_history_refresh_at = now
    row.updated_at = now
    _store_rate_limit(session, client, row=row)


def _mark_failure(session, exc: BaseException) -> None:
    apply_failure(_status_row(session), exc)


def _store_rate_limit(session, client, row=None) -> None:
    info = getattr(client, "rate_limit", None)
    if not isinstance(info, RateLimitInfo):
        return
    if info.limit is None and info.remaining is None and info.reset_at is None:
        return
    target = row if row is not None else _status_row(session)
    target.rate_limit_limit = info.limit
    target.rate_limit_remaining = info.remaining
    target.rate_limit_reset_at = info.reset_at


def _status_row(session) -> BrokerSyncStatus:
    row = session.scalar(select(BrokerSyncStatus).where(BrokerSyncStatus.broker == Broker.TRADING212))
    if row is None:
        row = BrokerSyncStatus(broker=Broker.TRADING212, connection_status="not_checked")
        session.add(row)
        session.flush()
    return row


def _load_state(session) -> RefreshState:
    loaded = _optional(session, lambda: _read_state(session))
    return loaded if loaded is not None else RefreshState()


def _read_state(session) -> RefreshState:
    row = session.scalar(select(BrokerSyncStatus).where(BrokerSyncStatus.broker == Broker.TRADING212))
    if row is None:
        return RefreshState()
    return RefreshState(
        connection_status=row.connection_status or "not_checked",
        last_current_refresh_at=row.last_current_refresh_at,
        last_history_refresh_at=row.last_history_refresh_at,
        last_snapshot_time=row.last_snapshot_time,
        last_refresh_error=row.last_refresh_error,
        next_current_refresh_at=row.next_current_refresh_at,
        next_history_refresh_at=row.next_history_refresh_at,
        worker_heartbeat_at=row.worker_heartbeat_at,
        quota=RateQuota(remaining=row.rate_limit_remaining, reset_at=row.rate_limit_reset_at),
    )


def _optional(session, fn):
    """A missing column must not abort the caller's transaction."""
    nested = session.begin_nested()
    try:
        value = fn()
    except Exception:
        nested.rollback()
        return None
    nested.commit()
    return value


def serve(scheduler) -> int:
    """Run until the scheduler stops. Keyboard interrupt shuts it down once."""
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        shutdown_scheduler(scheduler)
    return 0


def shutdown_scheduler(scheduler) -> None:
    running = getattr(scheduler, "running", False)
    if running:
        scheduler.shutdown(wait=False)
