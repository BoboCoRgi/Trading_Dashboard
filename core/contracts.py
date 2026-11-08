"""Broker-agnostic drafts. Connectors produce these. The database stores them."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.enums import Broker, CashMovementType, TradeAction


@dataclass(frozen=True)
class RawEventDraft:
    broker: Broker
    broker_event_id: str
    event_type: str
    occurred_at: datetime
    raw_payload: dict


@dataclass(frozen=True)
class CanonicalTradeDraft:
    broker_trade_id: str
    action: TradeAction
    quantity: Decimal
    fill_price: Decimal
    trade_currency: str
    fx_rate_to_gbp: Decimal
    fees_native: Decimal
    fx_fee_native: Decimal
    executed_at: datetime
    symbol: str


@dataclass(frozen=True)
class CashMovementDraft:
    movement_type: CashMovementType
    amount_gbp: Decimal
    occurred_at: datetime
    note: str


@dataclass(frozen=True)
class NormalisedEvent:
    trade: CanonicalTradeDraft | None = None
    cash: CashMovementDraft | None = None
