"""Scan cached candles for setups. The page does not call Twelve Data from here.

Detection rules live in analytics.setups. This module only loads stored bars,
drops an unfinished candle, and records lifecycle changes.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.market_stats import MarketBar
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


def sync_displayed_bars(
    session: Session,
    *,
    instrument_id: str,
    symbol: str,
    timeframe: str,
    bars: list[PriceBar],
    origin: DataOrigin,
    now: datetime | None = None,
) -> list[dict]:
    if origin is DataOrigin.MOCK:
        return []
    moment = now or datetime.now(timezone.utc)
    _sync(session, instrument_id, symbol, timeframe, bars, origin, moment)
    return instrument_setups(session, instrument_id, origin, timeframe)


def attention_setups(session: Session, origin: DataOrigin, *, limit: int = 5) -> list[dict]:
    rows = session.scalars(
        select(TechnicalSetup)
        .where(TechnicalSetup.data_origin == origin, TechnicalSetup.current.is_(True), TechnicalSetup.status != INVALIDATED)
        .order_by(TechnicalSetup.candle_time.desc())
    ).all()
    ranked = sorted(rows, key=lambda row: (_ORDER.get(row.status, 9), row.symbol))
    return [_public(row) for row in ranked[:limit]]


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
    usable = [bar for bar in bars if _usable_source(bar)]
    usable.sort(key=lambda bar: bar.timestamp)
    market = [_market(bar) for bar in usable]
    completed = drop_unfinished(market, now)
    if len(completed) < len(usable):
        usable = usable[: len(completed)]
    quality = bar_status(usable, _interval(timeframe), now) if usable else "UNKNOWN"
    if not completed or not quality_allows(quality):
        return
    rules = rules_from_settings()
    if len(completed) < rules.min_bars:
        return
    result = replay(completed, symbol=symbol, timeframe=timeframe, rules=rules)
    _persist(session, instrument_id, timeframe, origin, quality, now, result)


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
        row = live.get(signal.structure_key)
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
        id=uuid4(),
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
