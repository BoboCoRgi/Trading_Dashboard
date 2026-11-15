"""Background Trading 212 refresh. The dashboard does not call the broker while rendering.

Current account data runs every five minutes. Incremental history runs every
thirty minutes. Stop this process with the dashboard stop script.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apscheduler.schedulers.blocking import BlockingScheduler

from core.config import get_settings
from core.logging import configure_logging
from db.session import session_scope
from services.t212_refresh import (
    configure_jobs,
    history_due,
    mark_worker_stopped,
    record_heartbeat,
    run_scheduled,
    serve,
    worker_startup_plan,
)
from services.t212_refresh import _load_state

PID_NAME = "t212_refresh.pid"


def pid_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "logs" / PID_NAME


def write_pid(root: Path | None = None) -> None:
    path = pid_path(root)
    path.parent.mkdir(exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def clear_pid(root: Path | None = None) -> None:
    path = pid_path(root)
    if path.is_file() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
        path.unlink()


def opening_modes(last_history, now: datetime, minutes: int) -> list[str]:
    return worker_startup_plan(history_is_due=history_due(last_history, now, minutes))


def run_opening(settings) -> None:
    now = datetime.now(timezone.utc)
    last_history = None
    try:
        with session_scope() as session:
            last_history = _load_state(session).last_history_refresh_at
            record_heartbeat(
                session,
                now=now,
                next_current=now + timedelta(minutes=settings.t212_current_refresh_minutes),
                next_history=now + timedelta(minutes=settings.t212_history_refresh_minutes),
            )
    except Exception as exc:
        print(f"Trading 212 refresh worker: status was not recorded ({exc.__class__.__name__})")
    for mode in opening_modes(last_history, now, settings.t212_history_refresh_minutes):
        run_scheduled(mode)


def beat(scheduler=None) -> None:
    try:
        current = None if scheduler is None else scheduler.get_job("t212_current")
        history = None if scheduler is None else scheduler.get_job("t212_history")
        with session_scope() as session:
            record_heartbeat(
                session,
                now=datetime.now(timezone.utc),
                next_current=None if current is None else current.next_run_time,
                next_history=None if history is None else history.next_run_time,
                update_schedule=scheduler is not None and current is not None,
            )
    except Exception as exc:
        print(f"Trading 212 refresh worker: heartbeat failed ({exc.__class__.__name__})")


def heartbeat_loop(stop: threading.Event, scheduler_box: dict) -> None:
    while not stop.is_set():
        beat(scheduler_box.get("scheduler"))
        stop.wait(30)


def stop_worker() -> None:
    try:
        with session_scope() as session:
            mark_worker_stopped(session)
    except Exception:
        pass
    clear_pid(ROOT)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.data_mode.strip().lower() != "live":
        print("Trading 212 refresh worker: not started because DATA_MODE is not live.")
        return 0
    if not settings.t212_read_only:
        print("Trading 212 refresh worker: refused because T212_READ_ONLY is not true.")
        return 1
    write_pid(ROOT)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler_box: dict = {}
    stop = threading.Event()
    pulse = threading.Thread(target=heartbeat_loop, args=(stop, scheduler_box), name="t212-heartbeat", daemon=True)
    pulse.start()
    try:
        run_opening(settings)
        now = datetime.now(timezone.utc)
        configure_jobs(
            scheduler,
            settings,
            {
                "current": lambda: run_scheduled("current"),
                "history": lambda: run_scheduled("history"),
                "beat": lambda: beat(scheduler),
            },
            now=now,
        )
        scheduler_box["scheduler"] = scheduler
        beat(scheduler)
        print(
            "Trading 212 refresh worker: "
            f"current every {settings.t212_current_refresh_minutes} minutes, "
            f"history every {settings.t212_history_refresh_minutes} minutes."
        )
        return serve(scheduler)
    finally:
        stop.set()
        stop_worker()


if __name__ == "__main__":
    raise SystemExit(main())
