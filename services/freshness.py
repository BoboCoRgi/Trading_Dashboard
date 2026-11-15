"""Freshness phrases for broker data, market prices, and AI review.

These functions do not call Trading 212 or Twelve Data.
"""

from datetime import datetime, timedelta, timezone

FRESH_AFTER = timedelta(minutes=5)
HEARTBEAT_STALE = timedelta(seconds=90)


def broker_status_line(refreshed_at: datetime | None, now: datetime, *, failed: bool) -> dict:
    """Trading 212 line. Stale only after five minutes, and always with a time."""
    if refreshed_at is None:
        if failed:
            return {"name": "Trading 212", "detail": "Refresh failed. Last successful update: —", "tone": "failed"}
        return {"name": "Trading 212", "detail": "Not refreshed yet", "tone": "muted"}
    stamp = clock(refreshed_at)
    if failed:
        return {
            "name": "Trading 212",
            "detail": f"Refresh failed. Last successful update: {stamp}",
            "tone": "failed",
        }
    age = _age_seconds(refreshed_at, now)
    if age <= 60:
        return {"name": "Trading 212", "detail": "Updated just now", "tone": "ok"}
    mins = max(1, int(age // 60))
    if age <= FRESH_AFTER.total_seconds():
        return {"name": "Trading 212", "detail": f"Updated {mins} min ago", "tone": "ok"}
    return {"name": "Trading 212", "detail": f"Stale · updated {mins} min ago", "tone": "stale"}


def market_status_line(fetched_at: datetime | None) -> dict:
    if fetched_at is None:
        return {"name": "Market prices", "detail": "Not stored yet", "tone": "muted"}
    return {"name": "Market prices", "detail": f"Updated {clock(fetched_at)}", "tone": "ok"}


def ai_status_line(basis: datetime | None) -> dict:
    if basis is None:
        return {"name": "AI review", "detail": "No review stored", "tone": "muted"}
    return {"name": "AI review", "detail": f"Based on {clock(basis)} data", "tone": "ok"}


def worker_label(heartbeat: datetime | None, now: datetime) -> str:
    if heartbeat is None:
        return "Stopped"
    if _age_seconds(heartbeat, now) <= HEARTBEAT_STALE.total_seconds():
        return "Running"
    return "Stopped"


def clock(value: datetime) -> str:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%H:%M UTC")


def full_clock(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return "—"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _age_seconds(moment: datetime, now: datetime) -> float:
    left = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    right = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elapsed = (right.astimezone(timezone.utc) - left.astimezone(timezone.utc)).total_seconds()
    return 0 if elapsed < 0 else elapsed
