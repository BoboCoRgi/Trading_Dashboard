"""Current instrument identity.

The stable broker ticker can keep a historical code. The display symbol follows
current listing metadata. Raw broker payloads are not modified.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from connectors.trading212.client import display_symbol, explicit_current_symbol
from core.enums import AliasType
from db.models import Instrument, InstrumentAlias

_SOURCE = "TRADING212"


def apply_current_identity(session: Session, instrument: Instrument, ticker: str, meta: dict) -> str | None:
    """Point the instrument at the current ticker. Return the previous symbol when it changes.

    Metadata without ``shortName`` cannot replace a symbol. That is what stops
    an old order payload from writing the historical ticker back.
    """
    if not isinstance(meta, dict):
        meta = {}
    _fill_identity(instrument, meta)
    current = explicit_current_symbol(meta)
    if not current:
        _remember(session, instrument, instrument.symbol, AliasType.CURRENT_TICKER)
        return None
    previous = instrument.symbol
    if current != previous:
        clash = session.scalar(
            select(Instrument).where(Instrument.symbol == current, Instrument.id != instrument.id)
        )
        if clash is not None:
            return None
        instrument.symbol = current
        _remember(session, instrument, previous, AliasType.HISTORICAL_TICKER)
    prefix = display_symbol(ticker)
    if prefix and prefix != instrument.symbol:
        _remember(session, instrument, prefix, AliasType.HISTORICAL_TICKER)
    _remember(session, instrument, instrument.symbol, AliasType.CURRENT_TICKER)
    if meta.get("name"):
        instrument.company_name = str(meta["name"])
    return None if previous == instrument.symbol else previous


def refresh_instrument_identity(session: Session, catalogue: dict) -> list[tuple[str, str, str]]:
    """Update stored instruments from the current catalogue. One row per broker ticker."""
    changes = []
    rows = session.scalars(select(Instrument).where(Instrument.trading212_ticker.is_not(None))).all()
    for instrument in rows:
        ticker = instrument.trading212_ticker or ""
        meta = catalogue.get(ticker)
        if not isinstance(meta, dict):
            continue
        before = instrument.symbol
        apply_current_identity(session, instrument, ticker, meta)
        if instrument.symbol != before:
            changes.append((before, instrument.symbol, ticker))
    return changes


def _fill_identity(instrument: Instrument, meta: dict) -> None:
    if not instrument.isin and meta.get("isin"):
        instrument.isin = str(meta["isin"])
    currency = meta.get("currency") or meta.get("currencyCode")
    if not instrument.currency and currency:
        instrument.currency = str(currency)


def _remember(session: Session, instrument: Instrument, symbol: str, alias_type: AliasType) -> None:
    if not symbol:
        return
    existing = session.scalar(
        select(InstrumentAlias).where(
            InstrumentAlias.instrument_id == instrument.id,
            InstrumentAlias.symbol == symbol,
            InstrumentAlias.alias_type == alias_type,
        )
    )
    if existing is not None:
        return
    session.add(
        InstrumentAlias(
            instrument_id=instrument.id,
            symbol=symbol,
            valid_from=None,
            valid_to=None,
            alias_type=alias_type,
            source=_SOURCE,
        )
    )
    session.flush()
