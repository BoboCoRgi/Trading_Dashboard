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


def collect_system_status(session, *, data_mode: str, now: datetime | None = None) -> dict:
    live = str(data_mode).strip().lower() == "live"
    origin = DataOrigin.LIVE if live else DataOrigin.MOCK
    snapshot = latest_snapshot(session, origin=origin)
    reconciliation = None
    fresh: dict = {}
    if live:
        try:
            from services.t212_refresh import load_dashboard_freshness

            fresh = load_dashboard_freshness(session, now=now, live=True) or {}
        except Exception:
            fresh = {}
        broker_row = _optional_live_status(session)
        reconciliation = broker_row.get("position_reconciliation_status")
        if not fresh:
            fresh = {
                "broker": "Connected" if broker_row.get("connection_status") == "ok" else "unavailable",
                "last_refresh": broker_row.get("last_snapshot_time"),
                "worker": "Stopped",
            }
    elif session is not None:
        try:
            from services.t212_refresh import load_dashboard_freshness

            fresh = load_dashboard_freshness(session, now=now, live=False)
        except Exception:
            fresh = {}
    return {
        "mode": "live" if live else "mock",
        "dashboard": "Running",
        "database": "Connected",
        "broker": fresh.get("broker") or ("unavailable" if live else "—"),
        "last_sync": fresh.get("last_refresh"),
        "last_refresh": fresh.get("last_refresh"),
        "next_refresh": fresh.get("next_refresh"),
        "worker": fresh.get("worker") or "Stopped",
        "snapshot": None if snapshot is None else snapshot.snapshot_time,
        "reconciliation": reconciliation,
        "freshness": fresh.get("lines") or [],
        "refresh_failed": bool(fresh.get("failed")),
    }


def system_status_markup(status: dict) -> str:
    mode = "LIVE · Trading 212" if status.get("mode") == "live" else "MOCK"
    reconciliation = status.get("reconciliation") or "—"
    if status.get("mode") != "live":
        return (
            '<div class="sys-status">'
            f'<p class="sys-mode">{_esc(mode)}</p>'
            f"<p>Dashboard: {_esc(status.get('dashboard') or 'Running')}</p>"
            f"<p>Database: {_esc(status.get('database') or 'unavailable')}</p>"
            "</div>"
        )
    return (
        '<div class="sys-status">'
        f'<p class="sys-mode">{_esc(mode)}</p>'
        f"<p>Dashboard: {_esc(status.get('dashboard') or 'Running')}</p>"
        f"<p>Database: {_esc(status.get('database') or 'unavailable')}</p>"
        f"<p>Trading 212: {_esc(status.get('broker') or 'unavailable')}</p>"
        f"<p>Background refresh: {_esc(status.get('worker') or 'Stopped')}</p>"
        f"<p>Position reconciliation: {_esc(str(reconciliation))}</p>"
        "</div>"
    )


def _optional_live_status(session) -> dict:
    nested = None
    try:
        nested = session.begin_nested()
        row = live_status(session)
        nested.commit()
        return row
    except Exception:
        if nested is not None:
            try:
                nested.rollback()
            except Exception:
                pass
        return {}


def freshness_markup(lines: list[dict] | None) -> str:
    rows = []
    for line in lines or []:
        tone = line.get("tone") or "muted"
        if tone not in {"ok", "stale", "failed", "muted"}:
            tone = "muted"
        rows.append(
            f'<p class="fresh-name">{_esc(line.get("name") or "")}</p>'
            f'<p class="fresh-detail fresh-{tone}">{_esc(line.get("detail") or "")}</p>'
        )
    if not rows:
        return ""
    return f'<div class="freshness">{"".join(rows)}</div>'


def render_system_status() -> None:
    status = _load_render_status()
    st.sidebar.markdown(system_status_markup(status), unsafe_allow_html=True)
    fresh = freshness_markup(status.get("freshness"))
    if fresh:
        st.sidebar.markdown(fresh, unsafe_allow_html=True)
    if status.get("mode") == "live":
        render_refresh_button()


def render_refresh_button() -> None:
    from services.t212_refresh import apply_refresh_click, request_manual_refresh

    clicked = st.sidebar.button("Refresh data", key="t212_refresh_data")

    def _run():
        with st.spinner("Refreshing..."):
            return request_manual_refresh()

    result = apply_refresh_click(clicked=clicked, refresh=_run)
    if result is None:
        notice = st.session_state.pop("t212_refresh_notice", None)
        if notice:
            st.sidebar.caption(notice)
        return
    if result.status == "ok":
        st.session_state["t212_refresh_notice"] = result.message
        st.rerun()
    st.sidebar.caption(result.message)


def _load_render_status() -> dict:
    try:
        mode = get_settings().data_mode
        with session_scope() as session:
            return collect_system_status(session, data_mode=mode)
    except Exception:
        mode = "mock"
        try:
            mode = get_settings().data_mode
        except Exception:
            mode = "mock"
        live = str(mode).strip().lower() == "live"
        return {
            "mode": "live" if live else "mock",
            "dashboard": "Running",
            "database": "unavailable",
            "broker": "unavailable" if live else "—",
            "last_sync": None,
            "last_refresh": None,
            "next_refresh": None,
            "worker": "Stopped",
            "snapshot": None,
            "reconciliation": None,
            "freshness": [],
            "refresh_failed": False,
        }


def _clock(value) -> str:
    if not isinstance(value, datetime):
        return "—"
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _esc(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
