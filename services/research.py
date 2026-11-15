"""Research workspace: one universe, one thesis per instrument, stored events only.

Rows are keyed by instrument id. A ticker is only the label used to find that
instrument. Nothing here invents an earnings date or a price.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from connectors.trading212.client import display_symbol
from core.enums import (
    AssetType,
    CatalystStatus,
    DataOrigin,
    ThesisStatus,
    WatchlistStatus,
)
from core.money import D
from db.models import (
    Catalyst,
    Decision,
    Instrument,
    InstrumentAlias,
    MarketEvent,
    MarketPriceBar,
    Thesis,
    WatchlistItem,
)
from services.holdings import holdings_report

SECTIONS = ("Watchlist", "Theses", "Catalysts & Events", "Deep Dive", "Strategy Lab")
EMPTY_THESIS = "No thesis yet. Add one to track what would make you change your view."
EMPTY_EVENTS = "No stored event for this name yet. Earnings, guidance, and other dates appear here only after they are recorded."
THESIS_STATUSES = ("ACTIVE", "WATCH", "BROKEN", "CLOSED")
WATCH_STATUSES = ("WATCHING", "PASS", "RESEARCH")


def fallback_source(data_mode: str) -> dict:
    """Page source when the portfolio status cannot be read. Live stays live."""
    if str(data_mode).strip().lower() == "live":
        return {"mode": "live", "label": "LIVE · Trading 212", "ready": False, "stale": False}
    return {"mode": "mock", "label": "MOCK DATA", "ready": False, "stale": False}


def open_research_instrument(instrument_id: str) -> dict:
    return {"instrument_id": str(instrument_id), "section": "Deep Dive"}


def apply_open(state: dict, instrument_id: str) -> dict:
    chosen = open_research_instrument(instrument_id)
    state["research_instrument_id"] = chosen["instrument_id"]
    state["research_section_widget"] = chosen["section"]
    return state


def display_status(*, held: bool, watch_status: str | None, referenced: bool) -> str:
    if held:
        return "HOLDING"
    if watch_status == "PASS":
        return "PASS"
    if watch_status == "RESEARCH":
        return "RESEARCH"
    if watch_status in {"ACTIVE", "WATCHING"}:
        return "WATCHING"
    if referenced:
        return "RESEARCH"
    return "WATCHING"


def thesis_context(thesis: Thesis) -> dict:
    """Stored thesis fields the Gemini deep-dive and skeptic prompts can read."""
    reviewed = thesis.last_reviewed_at
    updated = thesis.updated_at
    return {
        "title": thesis.title,
        "status": thesis.status.value if hasattr(thesis.status, "value") else str(thesis.status),
        "summary": thesis.thesis_summary,
        "bull_case": thesis.bull_case,
        "base_case": thesis.base_case,
        "bear_case": thesis.bear_case,
        "assumptions": thesis.assumptions or "",
        "risks": thesis.risks or "",
        "catalysts": thesis.catalysts,
        "invalidation_conditions": thesis.invalidation_conditions,
        "conviction": thesis.conviction,
        "time_horizon": thesis.time_horizon,
        "last_reviewed": reviewed.isoformat() if reviewed else None,
        "updated_at": updated.isoformat() if updated else None,
    }


def returns_from_closes(points: list[tuple[datetime, Decimal, str]]) -> dict:
    """1D is the last two stored closes. 1M is the close on or before 30 days earlier."""
    if not points:
        return {}
    ordered = sorted(points, key=lambda item: _aware(item[0]))
    last_time, last_price, currency = ordered[-1]
    day = _change(last_price, ordered[-2][1]) if len(ordered) >= 2 else None
    cutoff = _aware(last_time) - timedelta(days=30)
    prior = [item for item in ordered[:-1] if _aware(item[0]) <= cutoff]
    month = _change(last_price, prior[-1][1]) if prior else None
    return {
        "price": D(last_price),
        "currency": currency or "",
        "day": day,
        "month": month,
        "price_kind": "eod_close",
        "as_of": _aware(last_time),
    }


def event_timeline(events, catalysts) -> list[dict]:
    """Chronological stored events. An empty input is an empty timeline."""
    rows = []
    for event in events:
        classification = getattr(getattr(event, "event_classification", None), "value", None) or "UNKNOWN"
        title = "Unusual market move" if classification == "UNKNOWN" else classification.replace("_", " ").title()
        detail = (getattr(event, "context_note", None) or "").strip()
        move = getattr(event, "price_change_pct", None)
        if move is not None:
            move_text = f"Price move {(D(move) * 100):+,.2f}%"
            detail = f"{detail} {move_text}".strip() if detail else move_text
        when = getattr(event, "event_date", None)
        rows.append(
            {
                "instrument_id": str(event.instrument_id),
                "kind": "unusual market move",
                "when": when,
                "when_label": _clock(when) if when else "No date stored",
                "title": title,
                "detail": detail,
                "source": "stored",
            }
        )
    for catalyst in catalysts:
        window = (getattr(catalyst, "expected_window", None) or "").strip()
        when_label = "No date stored" if not window or window.upper() == "UNKNOWN" else window
        description = (getattr(catalyst, "description", None) or "").strip() or "Catalyst"
        status = getattr(getattr(catalyst, "status", None), "value", None) or ""
        status = {"UPCOMING": "Upcoming", "PASSED": "Passed"}.get(status, "")
        rows.append(
            {
                "instrument_id": str(catalyst.instrument_id),
                "kind": "catalyst",
                "when": getattr(catalyst, "created_at", None),
                "when_label": when_label,
                "title": description,
                "detail": status,
                "source": "stored",
            }
        )
    rows.sort(key=lambda row: _aware(row["when"]) if row["when"] else datetime.min.replace(tzinfo=timezone.utc))
    return rows


def assemble_universe(
    *,
    holdings: list[dict],
    watches: list[dict],
    references: list[dict],
    theses: dict[str, dict],
    catalysts: dict[str, str],
    prices: dict[str, dict],
) -> list[dict]:
    """One row per instrument. A holding wins over a watchlist status."""
    rows: dict[str, dict] = {}

    def ensure(item: dict) -> dict:
        instrument_id = str(item["instrument_id"])
        row = rows.get(instrument_id)
        if row is None:
            row = {
                "instrument_id": instrument_id,
                "symbol": item.get("symbol") or "—",
                "company": item.get("company") or item.get("symbol") or "—",
                "held": False,
                "watch_status": None,
                "weight": None,
            }
            rows[instrument_id] = row
        if item.get("symbol"):
            row["symbol"] = item["symbol"]
        if item.get("company"):
            row["company"] = item["company"]
        return row

    for holding in holdings:
        row = ensure(holding)
        row["held"] = True
        row["weight"] = holding.get("weight")
    watched = set()
    for watch in watches:
        row = ensure(watch)
        row["watch_status"] = watch.get("status")
        watched.add(row["instrument_id"])
    for reference in references:
        ensure(reference)

    result = []
    for row in rows.values():
        instrument_id = row["instrument_id"]
        quote = prices.get(instrument_id) or {}
        thesis = theses.get(instrument_id) or {}
        result.append(
            {
                "instrument_id": instrument_id,
                "symbol": row["symbol"],
                "company": row["company"],
                "status": display_status(
                    held=row["held"],
                    watch_status=row["watch_status"],
                    referenced=True,
                ),
                "price": quote.get("price"),
                "currency": quote.get("currency") or "",
                "price_kind": quote.get("price_kind") or "",
                "as_of": quote.get("as_of"),
                "day_pct": quote.get("day"),
                "month_pct": quote.get("month"),
                "weight": row["weight"] if row["held"] else None,
                "active_thesis": bool(thesis.get("active")),
                "last_reviewed": thesis.get("reviewed"),
                "next_catalyst": catalysts.get(instrument_id),
                "held": row["held"],
                "removable": (not row["held"]) and instrument_id in watched,
            }
        )
    result.sort(key=lambda item: (0 if item["held"] else 1, -float(item["weight"] or 0), item["symbol"]))
    return result


def load_universe(session: Session, provider) -> list[dict]:
    report = holdings_report(session, provider)
    holdings = []
    if report.get("ready"):
        for position in report["combined"]:
            if position.quantity is None or position.quantity <= 0:
                continue
            holdings.append(
                {
                    "instrument_id": str(position.instrument_id),
                    "symbol": position.symbol,
                    "company": position.company,
                    "weight": position.weight,
                    "price": position.price,
                    "currency": position.price_currency or "",
                    "day": position.daily_change_pct,
                }
            )
    origin = provider.origin
    watch_rows = session.scalars(
        select(WatchlistItem).where(
            WatchlistItem.data_origin == origin,
            WatchlistItem.status != WatchlistStatus.ARCHIVED,
        )
    ).all()
    thesis_rows = session.scalars(select(Thesis).where(Thesis.data_origin == origin)).all()
    decision_ids = session.scalars(select(Decision.instrument_id).where(Decision.data_origin == origin)).all()
    ids = {UUID(row["instrument_id"]) for row in holdings}
    ids.update(row.instrument_id for row in watch_rows)
    ids.update(row.instrument_id for row in thesis_rows)
    ids.update(decision_ids)
    instruments = {}
    if ids:
        instruments = {
            row.id: row
            for row in session.scalars(select(Instrument).where(Instrument.id.in_(ids))).all()
        }
    watches = [_named(instruments.get(row.instrument_id), row.instrument_id, status=row.status.value) for row in watch_rows]
    watches = [row for row in watches if row is not None]
    references = []
    for row in thesis_rows:
        named = _named(instruments.get(row.instrument_id), row.instrument_id)
        if named is not None:
            references.append(named)
    for instrument_id in decision_ids:
        named = _named(instruments.get(instrument_id), instrument_id)
        if named is not None:
            references.append(named)
    latest: dict[UUID, Thesis] = {}
    for row in thesis_rows:
        current = latest.get(row.instrument_id)
        if current is None or _aware(row.updated_at) >= _aware(current.updated_at):
            latest[row.instrument_id] = row
    theses = {
        str(instrument_id): {
            "active": row.status == ThesisStatus.ACTIVE,
            "reviewed": _day(row.last_reviewed_at or row.updated_at),
        }
        for instrument_id, row in latest.items()
    }
    catalysts = _next_catalysts(session, list(ids))
    prices = _cached_prices(session, list(ids), origin)
    for holding in holdings:
        stored = prices.get(holding["instrument_id"])
        if not stored or stored.get("price") is None:
            prices[holding["instrument_id"]] = {
                "price": holding.get("price"),
                "currency": holding.get("currency") or "",
                "day": holding.get("day"),
                "month": None,
                "price_kind": "broker_snapshot",
                "as_of": None,
            }
        elif stored.get("day") is None and holding.get("day") is not None:
            stored["day"] = holding["day"]
    return assemble_universe(
        holdings=holdings,
        watches=watches,
        references=references,
        theses=theses,
        catalysts=catalysts,
        prices=prices,
    )


def resolve_instrument(session: Session, symbol: str, *, origin: DataOrigin) -> Instrument:
    """Find the canonical instrument. Create one only when no symbol, alias, or broker ticker matches."""
    cleaned = _symbol(symbol)
    if not cleaned:
        raise ValueError("Enter a ticker.")
    found = session.scalar(select(Instrument).where(func.upper(Instrument.symbol) == cleaned))
    if found is not None:
        return found
    aliases = session.scalars(select(InstrumentAlias).where(func.upper(InstrumentAlias.symbol) == cleaned)).all()
    if aliases:
        aliases.sort(key=lambda row: 0 if row.alias_type.value == "CURRENT_TICKER" else 1)
        instrument = session.get(Instrument, aliases[0].instrument_id)
        if instrument is not None:
            return instrument
    for instrument in session.scalars(select(Instrument).where(Instrument.trading212_ticker.is_not(None))).all():
        ticker = instrument.trading212_ticker or ""
        if display_symbol(ticker).upper() == cleaned or ticker.upper() == cleaned:
            return instrument
    created = Instrument(
        symbol=cleaned,
        company_name=cleaned,
        asset_type=AssetType.EQUITY,
        exchange="",
        currency="",
        theme="",
        data_origin=origin,
    )
    session.add(created)
    session.flush()
    return created


def add_watch(
    session: Session,
    symbol: str,
    *,
    origin: DataOrigin,
    status: WatchlistStatus = WatchlistStatus.WATCHING,
) -> dict:
    if status not in {WatchlistStatus.WATCHING, WatchlistStatus.PASS, WatchlistStatus.RESEARCH, WatchlistStatus.ACTIVE}:
        raise ValueError("Choose watching, pass, or research.")
    instrument = resolve_instrument(session, symbol, origin=origin)
    row = session.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument.id))
    if row is None:
        row = WatchlistItem(
            instrument_id=instrument.id,
            notes="",
            status=status,
            data_origin=origin,
        )
        session.add(row)
    else:
        row.status = status
        row.data_origin = origin
    session.flush()
    return {"instrument_id": str(instrument.id), "symbol": instrument.symbol}


def remove_watch(session: Session, instrument_id: str, *, held: bool) -> dict:
    if held:
        return {"removed": False, "reason": "A current holding stays on the list."}
    try:
        parsed = UUID(str(instrument_id))
    except ValueError:
        return {"removed": False, "reason": "This name is not a manual watchlist entry."}
    row = session.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == parsed))
    if row is None:
        return {"removed": False, "reason": "This name is not a manual watchlist entry."}
    session.delete(row)
    session.flush()
    return {"removed": True, "instrument_id": str(parsed)}


def save_thesis(session: Session, instrument_id: str, fields: dict, *, origin: DataOrigin | None = None) -> str:
    """Create or update the thesis for this workspace. This does not create a decision."""
    instrument = session.get(Instrument, UUID(str(instrument_id)))
    if instrument is None:
        raise ValueError("unknown instrument")
    chosen = origin or instrument.data_origin
    conviction = fields.get("conviction")
    if conviction in ("", None):
        conviction = None
    else:
        conviction = int(conviction)
        if conviction < 1 or conviction > 5:
            raise ValueError("Conviction is 1 to 5.")
    status_name = str(fields.get("status") or "ACTIVE")
    status = ThesisStatus(status_name)
    row = _latest_thesis(session, instrument.id, chosen)
    now = datetime.now(timezone.utc)
    base_case = row.base_case if row is not None and "base_case" not in fields else (fields.get("base_case") or "")
    title = (fields.get("title") or (row.title if row is not None else "") or f"{instrument.symbol} thesis")[:256]
    payload = {
        "title": title,
        "thesis_summary": fields.get("summary") or "",
        "time_horizon": (fields.get("time_horizon") or "")[:64],
        "bull_case": fields.get("bull_case") or "",
        "base_case": base_case,
        "bear_case": fields.get("bear_case") or "",
        "catalysts": fields.get("catalysts") or "",
        "invalidation_conditions": fields.get("invalidation") or "",
        "assumptions": fields.get("assumptions") or "",
        "risks": fields.get("risks") or "",
        "conviction": conviction,
        "last_reviewed_at": now,
        "status": status,
    }
    if row is None:
        row = Thesis(instrument_id=instrument.id, data_origin=chosen, **payload)
        session.add(row)
    else:
        for key, value in payload.items():
            setattr(row, key, value)
    session.flush()
    return str(row.id)


def thesis_for(session: Session, instrument_id: str, *, origin: DataOrigin | None = None) -> dict | None:
    try:
        parsed = UUID(str(instrument_id))
    except ValueError:
        return None
    row = _latest_thesis(session, parsed, origin)
    if row is None:
        return None
    payload = thesis_context(row)
    payload["id"] = str(row.id)
    payload["instrument_id"] = str(row.instrument_id)
    return payload


def events_for(session: Session, instrument_id: str) -> list[dict]:
    try:
        parsed = UUID(str(instrument_id))
    except ValueError:
        return []
    events = session.scalars(select(MarketEvent).where(MarketEvent.instrument_id == parsed)).all()
    catalysts = session.scalars(select(Catalyst).where(Catalyst.instrument_id == parsed)).all()
    return event_timeline(events, catalysts)


def decisions_for(session: Session, instrument_id: str) -> list[dict]:
    try:
        parsed = UUID(str(instrument_id))
    except ValueError:
        return []
    rows = session.scalars(
        select(Decision).where(Decision.instrument_id == parsed).order_by(Decision.created_at.desc())
    ).all()
    return [
        {
            "id": str(row.id),
            "decision_type": row.decision_type.value,
            "status": row.status.value,
            "proposed_action": row.proposed_action,
            "created_at": _day(row.created_at),
        }
        for row in rows
    ]


def dive_context(session: Session, instrument_id: str, *, origin: DataOrigin | None = None) -> dict:
    return {
        "instrument_id": str(instrument_id),
        "thesis": thesis_for(session, instrument_id, origin=origin),
        "events": events_for(session, instrument_id),
        "decisions": decisions_for(session, instrument_id),
    }


def _latest_thesis(session: Session, instrument_id: UUID, origin: DataOrigin | None) -> Thesis | None:
    statement = select(Thesis).where(Thesis.instrument_id == instrument_id).order_by(Thesis.updated_at.desc())
    if origin is not None:
        statement = statement.where(Thesis.data_origin == origin)
    return session.scalar(statement)


def _named(instrument: Instrument | None, instrument_id, *, status: str | None = None) -> dict | None:
    if instrument is None:
        return None
    payload = {
        "instrument_id": str(instrument_id),
        "symbol": instrument.symbol,
        "company": instrument.company_name,
    }
    if status is not None:
        payload["status"] = status
    return payload


def _next_catalysts(session: Session, instrument_ids: list[UUID]) -> dict[str, str]:
    if not instrument_ids:
        return {}
    rows = session.scalars(
        select(Catalyst).where(
            Catalyst.instrument_id.in_(instrument_ids),
            Catalyst.status == CatalystStatus.UPCOMING,
        )
    ).all()
    chosen: dict[UUID, Catalyst] = {}
    for row in rows:
        current = chosen.get(row.instrument_id)
        if current is None or _aware(row.created_at) >= _aware(current.created_at):
            chosen[row.instrument_id] = row
    labels = {}
    for instrument_id, row in chosen.items():
        text = (row.description or "").strip()
        window = (row.expected_window or "").strip()
        if window and window.upper() != "UNKNOWN":
            text = f"{text} · {window}".strip(" ·")
        if text:
            labels[str(instrument_id)] = text
    return labels


def _cached_prices(session: Session, instrument_ids: list[UUID], origin: DataOrigin) -> dict[str, dict]:
    if not instrument_ids:
        return {}
    bars = session.scalars(
        select(MarketPriceBar).where(
            MarketPriceBar.instrument_id.in_(instrument_ids),
            MarketPriceBar.interval_code == "1D",
        )
    ).all()
    grouped: dict[str, list] = {}
    for bar in bars:
        grouped.setdefault(str(bar.instrument_id), []).append(bar)
    prices = {}
    for instrument_id, group in grouped.items():
        preferred = [bar for bar in group if bar.data_origin == origin] or group
        points = [(bar.bar_start, bar.close, bar.currency) for bar in preferred]
        prices[instrument_id] = returns_from_closes(points)
    return prices


def _symbol(value: str) -> str:
    cleaned = " ".join(str(value).strip().upper().split())
    if len(cleaned) > 32:
        raise ValueError("Enter the ticker, not the company name.")
    return cleaned


def _change(latest, earlier) -> Decimal | None:
    if latest is None or earlier in (None, 0):
        return None
    base = D(earlier)
    if base == 0:
        return None
    return (D(latest) - base) / base


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _clock(value: datetime) -> str:
    moment = _aware(value)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _day(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).astimezone(timezone.utc).date().isoformat()
