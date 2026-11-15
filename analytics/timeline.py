"""Chart annotations from trades, decisions, and stored events.

The chart draws these markers. It does not decide what an event is.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class TimelineMarker:
    instrument_id: str
    timestamp: datetime
    event_type: str
    title: str
    description: str
    price: Decimal | None
    source: str
    source_id: str
    currency: str = ""


def trade_marker(
    *,
    instrument_id: str,
    trade_id: str,
    timestamp: datetime,
    action: str,
    quantity: Decimal,
    price: Decimal,
    currency: str,
    broker: str = "",
) -> TimelineMarker:
    buy = action == "BUY"
    label = "Buy" if buy else "Sell"
    return TimelineMarker(
        instrument_id=instrument_id,
        timestamp=timestamp,
        event_type="TRADE_BUY" if buy else "TRADE_SELL",
        title=f"{label} · {broker}" if broker else label,
        description=f"{broker + ' · ' if broker else ''}Quantity {quantity} at {price} {currency}".strip(),
        price=price,
        source="trade",
        source_id=trade_id,
        currency=currency,
    )


def decision_marker(
    *,
    instrument_id: str,
    decision_id: str,
    timestamp: datetime,
    decision_type: str,
    reasoning: str,
    conviction: Decimal | None,
) -> TimelineMarker:
    parts = []
    if conviction is not None:
        parts.append(f"Conviction {conviction}")
    text = " ".join(reasoning.split())
    if text:
        parts.append(text[:160])
    return TimelineMarker(
        instrument_id=instrument_id,
        timestamp=timestamp,
        event_type="DECISION",
        title=decision_type,
        description=" · ".join(parts),
        price=None,
        source="decision",
        source_id=decision_id,
    )


def stored_event_marker(
    *,
    instrument_id: str,
    event_id: str,
    timestamp: datetime,
    classification: str,
    note: str,
) -> TimelineMarker:
    kind = "COMPANY_EVENT" if classification == "COMPANY" else "MARKET_EVENT"
    title = " ".join(note.split())[:80] or classification or "Stored event"
    return TimelineMarker(
        instrument_id=instrument_id,
        timestamp=timestamp,
        event_type=kind,
        title=title,
        description=classification,
        price=None,
        source="market_event",
        source_id=event_id,
    )


def dedupe_markers(markers: list[TimelineMarker]) -> list[TimelineMarker]:
    """Keep every fill. Collapse repeated titles for the same event on the same day."""
    kept: list[TimelineMarker] = []
    seen: set[tuple] = set()
    for marker in sorted(markers, key=lambda item: item.timestamp):
        if marker.event_type in {"TRADE_BUY", "TRADE_SELL", "DECISION"}:
            identity = (marker.event_type, marker.source, marker.source_id)
        else:
            identity = (
                marker.instrument_id,
                marker.timestamp.date(),
                marker.event_type,
                marker.title.casefold(),
            )
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(marker)
    return kept


def bars_covering(markers: list[TimelineMarker], timestamps: list[datetime], closes: list[Decimal], currency: str) -> list[dict]:
    """Give each marker a y value on this series. A price in another currency is not plotted."""
    if not timestamps:
        return []
    ordered = sorted(zip(timestamps, closes, strict=True), key=lambda item: item[0])
    start = ordered[0][0]
    end = ordered[-1][0]
    placed = []
    for marker in markers:
        if marker.timestamp < start or marker.timestamp > end:
            continue
        price = marker.price
        if price is not None and marker.currency and marker.currency != currency:
            continue
        if price is None:
            price = _close_at(ordered, marker.timestamp)
        if price is None:
            continue
        placed.append(
            {
                "time": marker.timestamp,
                "price": price,
                "event_type": marker.event_type,
                "title": marker.title,
                "description": marker.description,
            }
        )
    return placed


def _close_at(ordered: list[tuple[datetime, Decimal]], moment: datetime) -> Decimal | None:
    chosen = None
    for timestamp, close in ordered:
        if timestamp <= moment:
            chosen = close
        else:
            break
    return chosen if chosen is not None else ordered[0][1]
