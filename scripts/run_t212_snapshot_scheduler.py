"""Store a live Trading 212 snapshot on a fixed UTC interval.

There is no market-hours calendar in this project. The job runs whenever this
process is running, including outside exchange hours. Stop it with Ctrl+C.
The dashboard does not start this process.

A snapshot time is the start of the interval, for example 12:00, 12:15, 12:30
when the interval is 15 minutes. A second run for that same timestamp does
not insert another row.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apscheduler.schedulers.blocking import BlockingScheduler

from connectors.trading212.client import Trading212Client, Trading212Error
from core.config import get_settings
from core.enums import SnapshotSource
from core.logging import configure_logging
from db.session import session_scope
from services.t212_snapshot import create_live_snapshot


def run_once() -> None:
    settings = get_settings()
    if not settings.t212_enabled or not settings.t212_read_only:
        print("Trading 212 snapshot scheduler: skipped. Enable the read-only client first.")
        return
    if not settings.t212_api_key.strip() or not settings.t212_api_secret.strip():
        print("Trading 212 snapshot scheduler: skipped. API key and secret are required.")
        return
    try:
        client = Trading212Client(settings)
        with session_scope() as session:
            report = create_live_snapshot(session, client, settings, source=SnapshotSource.SCHEDULER)
        print(report.render())
    except (Trading212Error, ValueError) as exc:
        print(f"Trading 212 snapshot scheduler: failed ({exc})")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.t212_read_only:
        print("Trading 212 snapshot scheduler: refused because T212_READ_ONLY is not true.")
        return 1
    minutes = settings.snapshot_interval_minutes
    print(
        "Trading 212 snapshot scheduler: "
        f"every {minutes} minutes UTC. Not limited to market hours. Stop with Ctrl+C."
    )
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_once, "interval", minutes=minutes, id="t212_live_snapshot", max_instances=1, coalesce=True)
    run_once()
    scheduler.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
