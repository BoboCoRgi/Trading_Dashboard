"""Which stored portfolio the dashboard reads.

Streamlit pages do not call Trading 212. Ingestion writes snapshots, and this
module chooses the mock book or the live book from DATA_MODE. A live request
never falls back to MOCK rows.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.enums import DataOrigin
from db.models import PortfolioSnapshot
from services.ledger import latest_snapshot

LIVE_SNAPSHOT_COMMAND = r".venv\Scripts\python.exe scripts/create_t212_live_snapshot.py"
_MISSING_LIVE = "No live Trading 212 snapshot is available yet."


def _blank_status(*, mode: str, origin: str | None, label: str, message: str, command: str | None) -> dict:
    return {
        "mode": mode,
        "origin": origin,
        "label": label,
        "ready": False,
        "stale": False,
        "snapshot_clock": None,
        "age_label": None,
        "message": message,
        "command": command,
        "snapshot_id": None,
    }


def describe_source(
    provider: "PortfolioDataProvider",
    snapshot: PortfolioSnapshot | None,
    *,
    now: datetime,
    interval_minutes: int,
) -> dict:
    """Banner facts for one already-selected snapshot. None means that book is empty."""
    if snapshot is None:
        if provider.origin is DataOrigin.LIVE:
            return _blank_status(
                mode="live",
                origin=DataOrigin.LIVE.value,
                label=provider.label,
                message=_MISSING_LIVE,
                command=LIVE_SNAPSHOT_COMMAND,
            )
        return _blank_status(
            mode="mock",
            origin=DataOrigin.MOCK.value,
            label=provider.label,
            message="No snapshot is stored yet.",
            command=None,
        )
    moment = snapshot.snapshot_time
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    elapsed = (current - moment).total_seconds()
    minutes = 0 if elapsed < 0 else int(elapsed // 60)
    stale = provider.origin is DataOrigin.LIVE and minutes >= interval_minutes
    age_label = None
    if stale:
        unit = "minute" if minutes == 1 else "minutes"
        age_label = f"Last updated {minutes} {unit} ago"
    return {
        "mode": "live" if provider.origin is DataOrigin.LIVE else "mock",
        "origin": provider.origin.value,
        "label": provider.label,
        "ready": True,
        "stale": stale,
        "snapshot_clock": moment.strftime("%H:%M"),
        "age_label": age_label,
        "message": None,
        "command": None,
        "snapshot_id": str(snapshot.id),
    }


class PortfolioDataProvider:
    """One origin. History, the latest snapshot, and its positions stay in that origin."""

    origin: DataOrigin
    label: str

    def latest_snapshot(self, session: Session) -> PortfolioSnapshot | None:
        return latest_snapshot(session, origin=self.origin)

    def history(self, session: Session) -> list[PortfolioSnapshot]:
        return list(
            session.scalars(
                select(PortfolioSnapshot)
                .where(PortfolioSnapshot.data_origin == self.origin)
                .order_by(PortfolioSnapshot.snapshot_time)
            ).all()
        )

    def status(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        interval_minutes: int | None = None,
        snapshot: PortfolioSnapshot | None = None,
        loaded: bool = False,
    ) -> dict:
        if not loaded:
            snapshot = self.latest_snapshot(session)
        settings = get_settings()
        return describe_source(
            self,
            snapshot,
            now=now or datetime.now(timezone.utc),
            interval_minutes=settings.snapshot_interval_minutes if interval_minutes is None else interval_minutes,
        )


class MockPortfolioProvider(PortfolioDataProvider):
    origin = DataOrigin.MOCK
    label = "MOCK DATA"


class LivePortfolioProvider(PortfolioDataProvider):
    origin = DataOrigin.LIVE
    label = "LIVE · Trading 212"


class InvalidDataModeProvider(PortfolioDataProvider):
    """An unrecognised DATA_MODE. It does not read the mock book."""

    origin = DataOrigin.LIVE
    label = "DATA MODE"

    def __init__(self, raw: str) -> None:
        self.raw = raw

    def latest_snapshot(self, session: Session) -> PortfolioSnapshot | None:
        return None

    def history(self, session: Session) -> list[PortfolioSnapshot]:
        return []

    def status(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        interval_minutes: int | None = None,
        snapshot: PortfolioSnapshot | None = None,
        loaded: bool = False,
    ) -> dict:
        return _blank_status(
            mode="invalid",
            origin=None,
            label=self.label,
            message=f"DATA_MODE must be mock or live. It is set to {self.raw!r}.",
            command=None,
        )


def portfolio_provider(settings: Settings | None = None) -> PortfolioDataProvider:
    """Interpret DATA_MODE. Only the exact live value leaves the mock book."""
    chosen = settings if settings is not None else get_settings()
    mode = chosen.data_mode.strip().lower()
    if mode == "live":
        return LivePortfolioProvider()
    if mode == "mock":
        return MockPortfolioProvider()
    return InvalidDataModeProvider(chosen.data_mode)
