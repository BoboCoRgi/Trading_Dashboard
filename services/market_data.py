"""Local price-bar cache and the only path the dashboard uses to load charts.

A page refresh reads PostgreSQL first. Twelve Data is called only for a missing
or stale edge of the requested window. Live mode never fills a gap with mock prices.
"""

from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from analytics.chart_ranges import history_window, interval_for, timeframe_interval, window_for
from analytics.resample import resample_bars, resample_source
from connectors.market_data.bars import PriceBar, bar_identity, upsert_bars
from connectors.market_data.twelvedata import MarketDataError, TwelveDataProvider
from core.config import get_settings
from core.enums import DataOrigin
from db.models import MarketPriceBar

_STEP = {
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1D": timedelta(days=1),
    "1W": timedelta(days=7),
}
_GRACE = {
    "5min": timedelta(minutes=15),
    "15min": timedelta(minutes=30),
    "1h": timedelta(hours=2),
    "1W": timedelta(days=8),
}
_MESSAGES = {
    "missing_key": "Price history is unavailable until a market-data key is configured.",
    "timeout": "Price history temporarily unavailable.",
    "rate_limit": "Price history temporarily unavailable.",
    "unsupported": "Price history temporarily unavailable.",
    "invalid": "Price history temporarily unavailable.",
    "unavailable": "Price history temporarily unavailable.",
    "empty": "Price history temporarily unavailable.",
}


class SeriesResult:
    def __init__(
        self,
        *,
        bars: list[PriceBar],
        status: str,
        message: str,
        source: str,
        data_type: str,
        updated_at: datetime | None,
        interval: str,
        aggregated_from: str | None = None,
    ) -> None:
        self.bars = bars
        self.status = status
        self.message = message
        self.source = source
        self.data_type = data_type
        self.updated_at = updated_at
        self.interval = interval
        self.aggregated_from = aggregated_from


def plan_fetch(
    cached: list[PriceBar],
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    interval: str,
) -> list[tuple[datetime, datetime]]:
    """Windows that are not already covered by a fresh cache. Empty means do not call the provider."""
    ordered = sorted((bar for bar in cached if bar.interval == interval), key=lambda bar: bar.timestamp)
    if not ordered:
        return [(start, end)]
    windows: list[tuple[datetime, datetime]] = []
    first = ordered[0].timestamp
    last = ordered[-1].timestamp
    step = _STEP[interval]
    if first > start + step:
        windows.append((start, first))
    if not _tail_is_fresh(last, now, interval):
        windows.append((last, end))
    return windows


def bar_status(bars: list[PriceBar], interval: str, now: datetime) -> str:
    """LIVE, DELAYED, EOD, STALE, or UNKNOWN. Stale means a newer bar was expected."""
    if not bars:
        return "UNKNOWN"
    last = bars[-1].timestamp
    if last.tzinfo is not None and now.tzinfo is not None and not _tail_is_fresh(last, now, interval):
        return "STALE"
    return freshness_label(bars, interval)


def freshness_label(bars: list[PriceBar], interval: str) -> str:
    if not bars:
        return "UNKNOWN"
    if interval == "1D":
        return "EOD"
    if bars and all(bar.is_delayed is False for bar in bars):
        return "LIVE"
    if any(bar.is_delayed for bar in bars):
        return "DELAYED"
    return "UNKNOWN"


def source_label(source: str) -> str:
    if source == "twelvedata":
        return "Twelve Data"
    return source or "Stored prices"


class SqlBarCache:
    def __init__(self, session: Session, origin: DataOrigin) -> None:
        self.session = session
        self.origin = origin

    def load(self, instrument_id: str, interval: str, start: datetime, end: datetime) -> list[PriceBar]:
        rows = self.session.scalars(
            select(MarketPriceBar).where(
                MarketPriceBar.instrument_id == UUID(instrument_id),
                MarketPriceBar.interval_code == interval,
                MarketPriceBar.data_origin == self.origin,
                MarketPriceBar.bar_start >= start,
                MarketPriceBar.bar_start <= end,
            )
        ).all()
        return [_row_to_bar(row) for row in rows]

    def save(self, bars: list[PriceBar]) -> None:
        if not bars:
            return
        fetched = datetime.now(timezone.utc)
        values = []
        for bar in bars:
            if not bar.instrument_id:
                continue
            values.append(
                {
                    "id": uuid4(),
                    "instrument_id": UUID(bar.instrument_id),
                    "bar_start": bar.timestamp,
                    "interval_code": bar.interval,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "currency": bar.currency,
                    "source": bar.source,
                    "is_delayed": bar.is_delayed,
                    "fetched_at": bar.fetched_at or fetched,
                    "data_origin": self.origin,
                }
            )
        if not values:
            return
        statement = pg_insert(MarketPriceBar).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_market_price_bar",
            set_={
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "currency": statement.excluded.currency,
                "is_delayed": statement.excluded.is_delayed,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
        self.session.execute(statement)


class MarketDataService:
    def __init__(self, cache, provider, *, now) -> None:
        self.cache = cache
        self.provider = provider
        self.now = now

    def series(self, *, instrument_id: str, symbol: str, range_code: str, currency: str) -> SeriesResult:
        interval = interval_for(range_code)
        start, end = window_for(range_code, self.now)
        try:
            cached = self.cache.load(instrument_id, interval, start, end)
        except Exception:
            return _unavailable(interval)
        windows = plan_fetch(cached, start=start, end=end, now=self.now, interval=interval)
        if windows and self.provider is None:
            if cached:
                return _ready(cached, interval, currency)
            return _result([], "missing_key", _MESSAGES["missing_key"], "", "UNKNOWN", None, interval)
        if windows and self.provider is not None:
            try:
                fetched: list[PriceBar] = []
                for window_start, window_end in windows:
                    fetched.extend(self.provider.fetch(symbol, interval, window_start, window_end))
                stamped = [_stamp_bar(bar, instrument_id, currency) for bar in fetched]
                if stamped:
                    self.cache.save(stamped)
                    cached = list(upsert_bars({bar_identity(bar): bar for bar in cached}, stamped).values())
            except MarketDataError as exc:
                if cached:
                    return _ready(cached, interval, currency)
                return _result([], exc.kind, _MESSAGES.get(exc.kind, _MESSAGES["unavailable"]), "", "UNKNOWN", None, interval)
            except Exception:
                if cached:
                    return _ready(cached, interval, currency)
                return _unavailable(interval)
        visible = [bar for bar in cached if start <= bar.timestamp <= end and bar.interval == interval]
        visible.sort(key=lambda bar: bar.timestamp)
        if not visible:
            return _result([], "empty", _MESSAGES["empty"], "", "UNKNOWN", None, interval)
        return _ready(visible, interval, currency)

    def chart_series(
        self,
        *,
        instrument_id: str,
        symbol: str,
        range_code: str,
        timeframe: str,
        currency: str,
    ) -> SeriesResult:
        """Bars for one holding chart. Resample a stored lower timeframe before calling the provider."""
        interval = timeframe_interval(timeframe)
        start, end = history_window(range_code, timeframe, self.now)
        try:
            cached = self.cache.load(instrument_id, interval, start, end)
        except Exception:
            return _unavailable(interval)
        if not plan_fetch(cached, start=start, end=end, now=self.now, interval=interval):
            return self._visible(cached, start, end, interval, currency, aggregated_from=None)
        lower = resample_source(interval)
        if lower:
            try:
                source_bars = self.cache.load(instrument_id, lower, start, end)
            except Exception:
                source_bars = []
            resampled = resample_bars(source_bars, interval)
            if resampled and not plan_fetch(resampled, start=start, end=end, now=self.now, interval=interval):
                return self._visible(resampled, start, end, interval, currency, aggregated_from=lower)
        if plan_fetch(cached, start=start, end=end, now=self.now, interval=interval) and self.provider is None:
            if cached:
                return self._visible(cached, start, end, interval, currency, aggregated_from=None)
            return _result([], "missing_key", _MESSAGES["missing_key"], "", "UNKNOWN", None, interval)
        if self.provider is not None:
            try:
                fetched: list[PriceBar] = []
                for window_start, window_end in plan_fetch(cached, start=start, end=end, now=self.now, interval=interval):
                    fetched.extend(self.provider.fetch(symbol, interval, window_start, window_end))
                stamped = [_stamp_bar(bar, instrument_id, currency) for bar in fetched]
                if stamped:
                    self.cache.save(stamped)
                    cached = list(upsert_bars({bar_identity(bar): bar for bar in cached}, stamped).values())
            except MarketDataError as exc:
                if cached:
                    return self._visible(cached, start, end, interval, currency, aggregated_from=None)
                return _result([], exc.kind, _MESSAGES.get(exc.kind, _MESSAGES["unavailable"]), "", "UNKNOWN", None, interval)
            except Exception:
                if cached:
                    return self._visible(cached, start, end, interval, currency, aggregated_from=None)
                return _unavailable(interval)
        return self._visible(cached, start, end, interval, currency, aggregated_from=None)

    def _visible(
        self,
        cached: list[PriceBar],
        start: datetime,
        end: datetime,
        interval: str,
        currency: str,
        *,
        aggregated_from: str | None,
    ) -> SeriesResult:
        visible = [bar for bar in cached if start <= bar.timestamp <= end and bar.interval == interval]
        visible.sort(key=lambda bar: bar.timestamp)
        if not visible:
            return _result([], "empty", _MESSAGES["empty"], "", "UNKNOWN", None, interval)
        ready = _ready(visible, interval, currency)
        ready.aggregated_from = aggregated_from
        return ready


def build_market_data_service(session: Session, origin: DataOrigin, *, now: datetime | None = None) -> MarketDataService:
    settings = get_settings()
    provider = None
    if settings.has_twelve_data:
        provider = TwelveDataProvider(settings.twelve_data_api_key)
    elif settings.market_data_provider.strip().lower() not in {"", "twelvedata"}:
        provider = None
    return MarketDataService(SqlBarCache(session, origin), provider, now=now or datetime.now(timezone.utc))


def _stamp_bar(bar: PriceBar, instrument_id: str, currency: str) -> PriceBar:
    return PriceBar(
        timestamp=bar.timestamp,
        interval=bar.interval,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        currency=bar.currency or currency,
        source=bar.source,
        is_delayed=bar.is_delayed,
        symbol=bar.symbol,
        instrument_id=instrument_id,
        fetched_at=bar.fetched_at or datetime.now(timezone.utc),
    )


def _ready(bars: list[PriceBar], interval: str, currency: str) -> SeriesResult:
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    updated = max((bar.fetched_at or bar.timestamp for bar in ordered), default=None)
    source = ordered[-1].source if ordered else ""
    filled = [bar if bar.currency else _with_currency(bar, currency) for bar in ordered]
    return _result(filled, "ready", "", source_label(source), freshness_label(filled, interval), updated, interval)


def _with_currency(bar: PriceBar, currency: str) -> PriceBar:
    return PriceBar(
        timestamp=bar.timestamp,
        interval=bar.interval,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        currency=currency,
        source=bar.source,
        is_delayed=bar.is_delayed,
        symbol=bar.symbol,
        instrument_id=bar.instrument_id,
        fetched_at=bar.fetched_at,
    )


def _unavailable(interval: str) -> SeriesResult:
    return _result([], "unavailable", _MESSAGES["unavailable"], "", "UNKNOWN", None, interval)


def _result(bars, status, message, source, data_type, updated_at, interval) -> SeriesResult:
    return SeriesResult(
        bars=bars,
        status=status,
        message=message,
        source=source,
        data_type=data_type,
        updated_at=updated_at,
        interval=interval,
    )


def _row_to_bar(row: MarketPriceBar) -> PriceBar:
    return PriceBar(
        timestamp=row.bar_start,
        interval=row.interval_code,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        currency=row.currency,
        source=row.source,
        is_delayed=row.is_delayed,
        instrument_id=str(row.instrument_id),
        fetched_at=row.fetched_at,
    )


def _tail_is_fresh(last: datetime, now: datetime, interval: str) -> bool:
    if last.tzinfo is None or now.tzinfo is None:
        return False
    last_utc = last.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    expected = _expected_daily_session(now_utc)
    if interval == "1D":
        return last_utc.date() >= expected
    if interval == "1W":
        return now_utc - last_utc <= _GRACE["1W"]
    if now_utc - last_utc <= _GRACE[interval]:
        return True
    return (not _cash_session_open(now_utc)) and last_utc.date() >= expected


def _expected_daily_session(now: datetime) -> datetime.date:
    moment = now.astimezone(timezone.utc)
    day = moment.date()
    if moment.weekday() >= 5:
        return _previous_weekday(day)
    if moment.hour >= 14:
        return day
    return _previous_weekday(day)


def _previous_weekday(day) -> datetime.date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _cash_session_open(now: datetime) -> bool:
    moment = now.astimezone(timezone.utc)
    if moment.weekday() >= 5:
        return False
    opened = time(14, 30)
    closed = time(21, 0)
    current = moment.timetz().replace(tzinfo=None)
    return opened <= current < closed
