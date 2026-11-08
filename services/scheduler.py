"""Portfolio snapshot job. It never places an order."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from core.config import get_settings
from core.enums import SnapshotSource
from core.logging import configure_logging
from db.session import session_scope
from services.ledger import rebuild_snapshots

logger = logging.getLogger(__name__)


def take_snapshot() -> int:
    with session_scope() as session:
        created = rebuild_snapshots(session, source=SnapshotSource.SCHEDULER)
    logger.info("snapshot job wrote %s rows", created)
    return created


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    scheduler = BlockingScheduler()
    scheduler.add_job(
        take_snapshot,
        "interval",
        minutes=settings.snapshot_interval_minutes,
        id="portfolio_snapshot",
    )
    logger.info(
        "scheduler started; interval=%s minutes; orders are not available",
        settings.snapshot_interval_minutes,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
