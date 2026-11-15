"""Scan cached candles for setups. The page does not call Twelve Data from here.

Detection rules live in analytics.setups. This module only loads stored bars,
drops an unfinished candle, and records lifecycle changes.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.market_stats import NEW_YORK, MarketBar
from analytics.setups import (
    CONFIRMED,
    INVALIDATED,
    WATCHING,
    AdvanceResult,
    SetupRules,
    SetupSignal,
    drop_unfinished,
    quality_allows,
    replay,
)
from connectors.market_data.bars import PriceBar
from core.config import get_settings
from core.enums import DataOrigin
from db.models import TechnicalSetup
from services.market_data import SqlBarCache, bar_status
from services.research import load_universe

_SETUP_NAMESPACE = UUID("6f9c2a1e-7b34-4c1a-9e2d-0a5b8c7d6e11")
_INTRADAY = {"5m", "15m", "1H"}
_LABELS = {
    "BREAKOUT_WATCH": "Breakout watch",
    "CONFIRMED_BREAKOUT": "Confirmed breakout",
    "PULLBACK_WATCH": "Pullback watch",
    "SUPPORT_BOUNCE": "Support bounce",
    "VOLUME_SPIKE": "Volume spike",
    "TREND_WARNING": "Trend warning",
}
_ORDER = {CONFIRMED: 0, WATCHING: 1, INVALIDATED: 2}
_REJECTED_SOURCES = {"", "mock", "seed"}


def rules_from_settings() -> SetupRules:
    settings = get_settings()
    return SetupRules(
        approach_fraction=settings.setup_approach_fraction,
        breakout_rvol=settings.setup_breakout_rvol,
        volume_spike_rvol=settings.setup_volume_spike_rvol,
        pullback_fraction=settings.setup_pullback_fraction,
    )


def refresh_setups(session: Session, provider, *, now: datetime | None = None, timeframe: str = "1D") -> list[dict]:
    """Update setups for holdings and the watchlist from cached bars only."""
    if provider.origin is DataOrigin.MOCK:
        return []
    moment = now or datetime.now(timezone.utc)
    names = _names(session, provider)
    for instrument_id, symbol in names:
        bars = _cached_bars(session, provider.origin, instrument_id, timeframe, moment)
        _sync(session, instrument_id, symbol, timeframe, bars, provider.origin, moment)
    return attention_setups(session, provider.origin)


def observe_displayed_bars(
    *,
    symbol: str,
    timeframe: str,
    bars: list[PriceBar],
    now: datetime | None = None,
) -> list[dict]:
    """Detect setups on the candles already loaded. This does not write."""
    moment = now or datetime.now(timezone.utc)
    detected = _detect(symbol, timeframe, bars, moment)
    if detected is None:
        return []
    quality, result = detected
    shown = [_from_signal(signal, quality, active=True) for signal in result.active]
    shown.extend(_from_signal(signal, quality, active=False) for signal in result.invalidated)
    return shown


def attention_setups(session: Session, origin: DataOrigin, *, limit: int = 5, now: datetime | None = None) -> list[dict]:
    rows = session.scalars(
        select(TechnicalSetup)
        .where(TechnicalSetup.data_origin == origin, TechnicalSetup.current.is_(True), TechnicalSetup.status != INVALIDATED)
        .order_by(TechnicalSetup.candle_time.desc())
    ).all()
    return [_public(row) for row in choose_attention(rows, now or datetime.now(timezone.utc), limit=limit)]


def choose_attention(rows: list, now: datetime, *, limit: int = 5) -> list:
    """Daily warnings stay ahead of intraday notes. Old intraday candles stay off Today."""
    session_day = _session_date(now)
    daily = []
    newest: dict[str, object] = {}
    for row in rows:
        if not getattr(row, "current", False) or row.status == INVALIDATED:
            continue
        if row.timeframe in _INTRADAY:
            if _local_date(row.candle_time) != session_day:
                continue
            kept = newest.get(row.symbol)
            if kept is None or _stamp(row.candle_time) > _stamp(kept.candle_time):
                newest[row.symbol] = row
            continue
        daily.append(row)
    daily.sort(key=lambda row: (_ORDER.get(row.status, 9), row.symbol, -_stamp(row.candle_time)))
    intraday = sorted(newest.values(), key=lambda row: (_ORDER.get(row.status, 9), row.symbol))
    return [*daily, *intraday][:limit]


def setup_id(instrument_id: str, timeframe: str, structure_key: str, candle_time: datetime, origin: DataOrigin) -> UUID:
    """Same candle and structure always get the same id. A later candle is a different row."""
    stamp = candle_time.isoformat() if candle_time.tzinfo else candle_time.replace(tzinfo=timezone.utc).isoformat()
    return uuid5(_SETUP_NAMESPACE, f"{instrument_id}|{timeframe}|{structure_key}|{stamp}|{origin.value}")


def instrument_setups(session: Session, instrument_id: str, origin: DataOrigin, timeframe: str) -> list[dict]:
    rows = session.scalars(
        select(TechnicalSetup)
        .where(
            TechnicalSetup.instrument_id == UUID(str(instrument_id)),
            TechnicalSetup.data_origin == origin,
            TechnicalSetup.timeframe == timeframe,
        )
        .order_by(TechnicalSetup.candle_time.desc())
    ).all()
    return [_public(row) for row in rows]


def _sync(session, instrument_id, symbol, timeframe, bars, origin, now) -> None:
    detected = _detect(symbol, timeframe, bars, now)
    if detected is None:
        return
    quality, result = detected
    _persist(session, instrument_id, timeframe, origin, quality, now, result)


def _detect(symbol: str, timeframe: str, bars: list[PriceBar], now: datetime):
    usable = [bar for bar in bars if _usable_source(bar)]
    usable.sort(key=lambda bar: bar.timestamp)
    market = [_market(bar) for bar in usable]
    completed = drop_unfinished(market, now)
    if len(completed) < len(usable):
        usable = usable[: len(completed)]
    quality = bar_status(usable, _interval(timeframe), now) if usable else "UNKNOWN"
    if not completed or not quality_allows(quality):
        return None
    rules = rules_from_settings()
    if len(completed) < rules.min_bars:
        return None
    return quality, replay(completed, symbol=symbol, timeframe=timeframe, rules=rules)


def _persist(session, instrument_id, timeframe, origin, quality, now, result: AdvanceResult) -> None:
    stored = session.scalars(
        select(TechnicalSetup).where(
            TechnicalSetup.instrument_id == UUID(str(instrument_id)),
            TechnicalSetup.timeframe == timeframe,
            TechnicalSetup.data_origin == origin,
        )
    ).all()
    live = {row.structure_key: row for row in stored if row.status != INVALIDATED}
    previous = {row.structure_key: row for row in stored}
    closed_keys = {signal.structure_key for signal in result.invalidated}
    for signal in result.invalidated:
        row = live.get(signal.structure_key) or previous.get(signal.structure_key)
        if row is None:
            session.add(_row(instrument_id, timeframe, origin, quality, now, signal, current=False))
            continue
        _apply(row, signal, quality, now, current=False)
    active_keys = {signal.structure_key for signal in result.active}
    for key, row in live.items():
        if key not in active_keys and key not in closed_keys:
            row.current = False
    for signal in result.active:
        if signal.structure_key in closed_keys:
            continue
        row = live.get(signal.structure_key) or session.get(
            TechnicalSetup, setup_id(str(instrument_id), timeframe, signal.structure_key, signal.candle_time, origin)
        )
        if row is None:
            session.add(_row(instrument_id, timeframe, origin, quality, now, signal, current=True))
            continue
        _apply(row, signal, quality, now, current=True)


def _apply(row: TechnicalSetup, signal: SetupSignal, quality: str, now: datetime, *, current: bool) -> None:
    row.setup_type = signal.setup_type
    row.status = signal.status
    row.candle_time = signal.candle_time
    row.level_low = signal.level_low
    row.level_high = signal.level_high
    row.close_price = signal.close
    row.rvol = signal.rvol
    row.ema20 = signal.ema20
    row.evidence = signal.evidence
    row.invalidation = signal.invalidation
    row.data_status = quality
    row.current = current
    row.invalidated_at = now if signal.status == INVALIDATED else None


def _row(instrument_id, timeframe, origin, quality, now, signal: SetupSignal, *, current: bool) -> TechnicalSetup:
    return TechnicalSetup(
        id=setup_id(str(instrument_id), timeframe, signal.structure_key, signal.candle_time, origin),
        instrument_id=UUID(str(instrument_id)),
        symbol=signal.symbol,
        timeframe=timeframe,
        setup_type=signal.setup_type,
        status=signal.status,
        detected_at=now,
        candle_time=signal.candle_time,
        level_low=signal.level_low,
        level_high=signal.level_high,
        close_price=signal.close,
        rvol=signal.rvol,
        ema20=signal.ema20,
        evidence=signal.evidence,
        invalidation=signal.invalidation,
        structure_key=signal.structure_key,
        data_status=quality,
        current=current,
        invalidated_at=now if signal.status == INVALIDATED else None,
        data_origin=origin,
    )


def _public(row: TechnicalSetup) -> dict:
    level = row.level_high if row.level_high is not None else row.level_low
    return {
        "id": str(row.id),
        "instrument_id": str(row.instrument_id),
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "setup_type": row.setup_type,
        "label": _LABELS.get(row.setup_type, row.setup_type),
        "status": row.status,
        "candle_time": row.candle_time,
        "level": None if level is None else str(level),
        "evidence": row.evidence,
        "invalidation": row.invalidation,
        "data_status": row.data_status,
        "active": bool(row.current) and row.status != INVALIDATED,
    }


def _from_signal(signal: SetupSignal, quality: str, *, active: bool) -> dict:
    level = signal.level_high if signal.level_high is not None else signal.level_low
    return {
        "id": "",
        "instrument_id": "",
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "setup_type": signal.setup_type,
        "label": _LABELS.get(signal.setup_type, signal.setup_type),
        "status": signal.status,
        "candle_time": signal.candle_time,
        "level": None if level is None else str(level),
        "evidence": signal.evidence,
        "invalidation": signal.invalidation,
        "data_status": quality,
        "active": active and signal.status != INVALIDATED,
    }


def _session_date(now: datetime):
    local = (now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)).astimezone(NEW_YORK)
    day = local.date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _local_date(value: datetime):
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(NEW_YORK).date()


def _stamp(value: datetime) -> float:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _names(session, provider) -> list[tuple[str, str]]:
    try:
        rows = load_universe(session, provider)
    except Exception:
        return []
    seen: set[str] = set()
    names = []
    for row in rows:
        instrument_id = str(row.get("instrument_id") or "")
        symbol = row.get("symbol") or ""
        if not instrument_id or not symbol or instrument_id in seen:
            continue
        seen.add(instrument_id)
        names.append((instrument_id, symbol))
    return names


def _cached_bars(session, origin, instrument_id, timeframe, now) -> list[PriceBar]:
    interval = _interval(timeframe)
    cache = SqlBarCache(session, origin)
    return cache.load(instrument_id, interval, now - timedelta(days=420), now)


def _interval(timeframe: str) -> str:
    return {"5m": "5min", "15m": "15min", "1H": "1h", "1D": "1D", "1W": "1W"}.get(timeframe, timeframe)


def _usable_source(bar: PriceBar) -> bool:
    return (bar.source or "").strip().lower() not in _REJECTED_SOURCES


def _market(bar: PriceBar) -> MarketBar:
    return MarketBar(
        start=bar.timestamp,
        interval=bar.interval,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        currency=bar.currency,
        source=bar.source,
    )
