from datetime import datetime

from core.contracts import CanonicalTradeDraft, CashMovementDraft, NormalisedEvent, RawEventDraft
from core.enums import Broker, CashMovementType, TradeAction
from core.money import D
from connectors.market_data.series import FX_TO_GBP
from connectors.mock_book import OPENING, OPENING_CASH, TRADE_PLAN, executed_at, fees_for, trade_fill_price


def _opening_event() -> RawEventDraft:
    occurred = datetime(OPENING.year, OPENING.month, OPENING.day, 8, 0, tzinfo=executed_at({"day": OPENING}).tzinfo)
    amount = OPENING_CASH["t212"]
    return RawEventDraft(
        broker=Broker.TRADING212,
        broker_event_id="MOCK-T212-OPENING",
        event_type="OPENING_BALANCE",
        occurred_at=occurred,
        raw_payload={
            "type": "OPENING_BALANCE",
            "id": "MOCK-T212-OPENING",
            "amountGbp": str(amount),
            "currency": "GBP",
            "asOf": occurred.isoformat(),
            "dataLabel": "MOCK DATA",
        },
    )


def _fill_event(row: dict) -> RawEventDraft:
    price = trade_fill_price(row)
    commission, fx_fee = fees_for(row["broker"], row["quantity"], price)
    occurred = executed_at(row)
    event_id = f"MOCK-T212-{row['symbol']}-{row['action'].value}-{row['day'].isoformat()}"
    ticker = f"{row['symbol']}_US_EQ"
    return RawEventDraft(
        broker=Broker.TRADING212,
        broker_event_id=event_id,
        event_type="FILL",
        occurred_at=occurred,
        raw_payload={
            "type": "FILL",
            "id": event_id,
            "ticker": ticker,
            "side": row["action"].value,
            "filledQuantity": str(row["quantity"]),
            "fillPrice": str(price),
            "currency": "USD",
            "fxRateToGbp": str(FX_TO_GBP),
            "stampDuty": str(commission),
            "fxFee": str(fx_fee),
            "filledAt": occurred.isoformat(),
            "dataLabel": "MOCK DATA",
        },
    )


def trading212_events() -> list[RawEventDraft]:
    events = [_opening_event()]
    events.extend(_fill_event(row) for row in TRADE_PLAN if row["broker"] is Broker.TRADING212)
    return events


def symbol_from_ticker(ticker: str) -> str:
    suffix = "_US_EQ"
    if ticker.endswith(suffix):
        return ticker[: -len(suffix)]
    return ticker


def normalise_trading212(event: RawEventDraft) -> NormalisedEvent:
    payload = event.raw_payload
    if payload.get("type") == "OPENING_BALANCE":
        return NormalisedEvent(
            cash=CashMovementDraft(
                movement_type=CashMovementType.OPENING,
                amount_gbp=D(str(payload["amountGbp"])),
                occurred_at=event.occurred_at,
                note="MOCK DATA opening cash",
            )
        )
    if payload.get("type") != "FILL":
        return NormalisedEvent()
    return NormalisedEvent(
        trade=CanonicalTradeDraft(
            broker_trade_id=str(payload["id"]),
            action=TradeAction(payload["side"]),
            quantity=D(str(payload["filledQuantity"])),
            fill_price=D(str(payload["fillPrice"])),
            trade_currency=str(payload["currency"]),
            fx_rate_to_gbp=D(str(payload["fxRateToGbp"])),
            fees_native=D(str(payload.get("stampDuty", "0"))),
            fx_fee_native=D(str(payload.get("fxFee", "0"))),
            executed_at=event.occurred_at,
            symbol=symbol_from_ticker(str(payload["ticker"])),
        )
    )
