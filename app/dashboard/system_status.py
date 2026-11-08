"""Sidebar status and the database-unavailable message.

Neither path prints credentials or broker account identifiers.
"""

from datetime import datetime, timezone

import streamlit as st

from core.config import get_settings
from core.enums import DataOrigin
from db.session import session_scope
from services.ledger import latest_snapshot
from services.t212_snapshot import live_status

LIVE_DATABASE_MESSAGE = "Local data services are unavailable. Start the dashboard using the Investment Dashboard launcher."
MOCK_DATABASE_MESSAGE = "Database unavailable or not seeded. Start Postgres, migrate, then run python -m scripts.seed."


def database_unavailable_message(data_mode: str) -> str:
    if str(data_mode).strip().lower() == "live":
        return LIVE_DATABASE_MESSAGE
    return MOCK_DATABASE_MESSAGE


def collect_system_status(session, *, data_mode: str) -> dict:
    live = str(data_mode).strip().lower() == "live"
    origin = DataOrigin.LIVE if live else DataOrigin.MOCK
    snapshot = latest_snapshot(session, origin=origin)
    broker = "unavailable"
    last_sync = None
    reconciliation = None
    if live:
        status = live_status(session)
        broker = "Connected" if status.get("connection_status") == "ok" else "unavailable"
        last_sync = status.get("last_successful_sync")
        reconciliation = status.get("position_reconciliation_status")
    return {
        "mode": "live" if live else "mock",
        "database": "Connected",
        "broker": broker,
        "last_sync": last_sync,
        "snapshot": None if snapshot is None else snapshot.snapshot_time,
        "reconciliation": reconciliation,
    }


def system_status_markup(status: dict) -> str:
    mode = "LIVE · Trading 212" if status.get("mode") == "live" else "MOCK"
    reconciliation = status.get("reconciliation") or "—"
    return (
        '<div class="sys-status">'
        f'<p class="sys-mode">{_esc(mode)}</p>'
        f"<p>Database: {_esc(status.get('database') or 'unavailable')}</p>"
        f"<p>Broker: {_esc(status.get('broker') or 'unavailable')}</p>"
        f"<p>Last broker sync: {_esc(_clock(status.get('last_sync')))}</p>"
        f"<p>Latest portfolio snapshot: {_esc(_clock(status.get('snapshot')))}</p>"
        f"<p>Position reconciliation: {_esc(str(reconciliation))}</p>"
        "</div>"
    )


def render_system_status() -> None:
    try:
        mode = get_settings().data_mode
        with session_scope() as session:
            status = collect_system_status(session, data_mode=mode)
    except Exception:
        mode = "live"
        try:
            mode = get_settings().data_mode
        except Exception:
            mode = "mock"
        status = {
            "mode": "live" if str(mode).strip().lower() == "live" else "mock",
            "database": "unavailable",
            "broker": "unavailable",
            "last_sync": None,
            "snapshot": None,
            "reconciliation": None,
        }
    st.sidebar.markdown(system_status_markup(status), unsafe_allow_html=True)


def _clock(value) -> str:
    if not isinstance(value, datetime):
        return "—"
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _esc(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
