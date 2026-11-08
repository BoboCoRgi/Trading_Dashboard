"""Market and benchmark boundaries.

These interfaces do not call a vendor. The stored reader uses rows already in
PostgreSQL. Missing bars stay missing.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from analytics.market_stats import MarketBar
from core.enums import DataOrigin
from db.models import BenchmarkMapping, Instrument, MarketPriceBar, PriceObservation


@dataclass(frozen=True)
class Quote:
    price: Decimal | None
    currency: str
    as_of: datetime | None
    source: str
    delay: str


@dataclass(frozen=True)
class SeriesNote:
    bars: list[MarketBar]
    note: str
    label: str = ""


class MarketDataProvider(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...

    def get_intraday_bars(self, symbol: str) -> SeriesNote: ...

    def get_daily_bars(self, symbol: str) -> SeriesNote: ...

    def get_volume_history(self, symbol: str) -> SeriesNote: ...


class BenchmarkProvider(Protocol):
    def get_market_benchmark(self, symbol: str) -> SeriesNote: ...

    def get_sector_benchmark(self, symbol: str) -> SeriesNote: ...

    def get_peer_group(self, symbol: str) -> dict: ...


class StoredMarketData:
    """Reads stored bars, then stored closes. It does not invent OHLC or volume."""

    def __init__(self, session: Session, origin: DataOrigin) -> None:
        self.session = session
        self.origin = origin

    def get_quote(self, symbol: str) -> Quote:
        return Quote(None, "", None, "not a quote feed", "No separate quote is stored. Use the position snapshot price.")

    def get_intraday_bars(self, symbol: str) -> SeriesNote:
        bars = self._bars(symbol, intraday=True)
        if bars is None:
            return SeriesNote([], "market_price_bar is not in this database yet. Run alembic upgrade head. No intraday bars were estimated.")
        if not bars:
            return SeriesNote([], "Intraday bars are not stored. No market-data provider is connected.")
        return SeriesNote(bars, "Intraday bars stored in market_price_bar. Volume is the volume of each bar.")

    def get_daily_bars(self, symbol: str) -> SeriesNote:
        bars = self._bars(symbol, intraday=False)
        if bars:
            return SeriesNote(bars, "Daily bars stored in market_price_bar. Volume, when present, is that session's volume.")
        closes = self._closes(symbol)
        if bars is None:
            prefix = "market_price_bar is not in this database yet. Run alembic upgrade head. "
            if not closes:
                return SeriesNote([], prefix + "No daily closes are stored for this symbol in this data mode.")
            return SeriesNote(
                closes,
                prefix + "Stored daily closes only. Open, high, low, and volume are not stored, so this is not a candlestick series and not a volume series.",
            )
        if not closes:
            return SeriesNote([], "No daily closes are stored for this symbol in this data mode.")
        return SeriesNote(
            closes,
            "Stored daily closes only. Open, high, low, and volume are not stored, so this is not a candlestick series and not a volume series.",
        )

    def get_volume_history(self, symbol: str) -> SeriesNote:
        daily = self.get_daily_bars(symbol)
        with_volume = [bar for bar in daily.bars if bar.volume is not None and bar.interval == "1D"]
        if not with_volume:
            return SeriesNote([], "Full-day volume is not stored. Closes are not treated as volume.")
        return SeriesNote(with_volume, "Full-day volume from daily bars.")

    def _instrument(self, symbol: str) -> Instrument | None:
        return self.session.scalar(select(Instrument).where(Instrument.symbol == symbol))

    def _bars(self, symbol: str, *, intraday: bool) -> list[MarketBar] | None:
        instrument = self._instrument(symbol)
        if instrument is None:
            return []
        try:
            rows = self.session.scalars(
                select(MarketPriceBar)
                .where(
                    MarketPriceBar.instrument_id == instrument.id,
                    MarketPriceBar.data_origin == self.origin,
                )
                .order_by(MarketPriceBar.bar_start)
            ).all()
        except ProgrammingError:
            self.session.rollback()
            return None
        chosen = []
        for row in rows:
            is_day = row.interval_code == "1D"
            if intraday == is_day:
                continue
            chosen.append(
                MarketBar(
                    start=row.bar_start,
                    interval=row.interval_code,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    currency=row.currency,
                    source=row.source,
                )
            )
        return chosen

    def _closes(self, symbol: str) -> list[MarketBar]:
        instrument = self._instrument(symbol)
        if instrument is None:
            return []
        rows = self.session.scalars(
            select(PriceObservation)
            .where(
                PriceObservation.instrument_id == instrument.id,
                PriceObservation.data_origin == self.origin,
            )
            .order_by(PriceObservation.as_of)
        ).all()
        return [
            MarketBar(
                start=row.as_of,
                interval="1D",
                open=None,
                high=None,
                low=None,
                close=row.price,
                volume=None,
                currency=row.currency,
                source=row.source,
            )
            for row in rows
        ]


class StoredBenchmarks:
    def __init__(self, session: Session, origin: DataOrigin, market_data: StoredMarketData) -> None:
        self.session = session
        self.origin = origin
        self.market_data = market_data

    def get_market_benchmark(self, symbol: str) -> SeriesNote:
        return self._mapped(symbol, "market")

    def get_sector_benchmark(self, symbol: str) -> SeriesNote:
        return self._mapped(symbol, "sector")

    def get_peer_group(self, symbol: str) -> dict:
        return {
            "ready": False,
            "members": [],
            "note": "No peer group is configured. A peer set is not chosen automatically.",
        }

    def _mapped(self, symbol: str, kind: str) -> SeriesNote:
        instrument = self.session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if instrument is None:
            return SeriesNote([], "Symbol is not in the instrument table.")
        try:
            mapping = self.session.scalar(
                select(BenchmarkMapping).where(
                    BenchmarkMapping.instrument_id == instrument.id,
                    BenchmarkMapping.data_origin == self.origin,
                )
            )
        except ProgrammingError:
            self.session.rollback()
            return SeriesNote([], "benchmark_mapping is not in this database yet. Run alembic upgrade head. No benchmark was guessed.")
        if mapping is None:
            return SeriesNote([], "No benchmark mapping is stored for this symbol.")
        benchmark = mapping.market_benchmark_symbol if kind == "market" else mapping.sector_benchmark_symbol
        label = "Market benchmark" if kind == "market" else "Sector benchmark"
        if not benchmark:
            return SeriesNote([], f"{label} is not set in the stored mapping.", label="")
        series = self.market_data.get_daily_bars(benchmark)
        if not series.bars:
            return SeriesNote([], f"{label} {benchmark} is configured, but no prices are stored for it.", label=benchmark)
        return SeriesNote(series.bars, f"{label} {benchmark}. {mapping.definition}".strip(), label=benchmark)
