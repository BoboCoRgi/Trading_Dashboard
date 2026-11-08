from datetime import datetime

from core.contracts import CanonicalTradeDraft, CashMovementDraft, NormalisedEvent, RawEventDraft
from core.enums import Broker, CashMovementType, TradeAction
from core.money import D
from connectors.market_data.series import FX_TO_GBP
from connectors.mock_book import INSTRUMENTS, OPENING, OPENING_CASH, TRADE_PLAN, executed_at, fees_for, trade_fill_price

_CONID = {row["symbol"]: row["conid"] for row in INSTRUMENTS}
_SIDE = {TradeAction.BUY: "BOT", TradeAction.SELL: "SLD"}


def _opening_event() -> RawEventDraft:
    occurred = datetime(OPENING.year, OPENING.month, OPENING.day, 8, 5, tzinfo=executed_at({"day": OPENING}).tzinfo)
    amount = OPENING_CASH["ibkr"]
    return RawEventDraft(
        broker=Broker.IBKR,
        broker_event_id="MOCK-IBKR-OPENING",
        event_type="CASH_BALANCE",
        occurred_at=occurred,
        raw_payload={
            "msgType": "CASH_BALANCE",
            "eventId": "MOCK-IBKR-OPENING",
            "cash": str(amount),
            "currency": "GBP",
            "asOf": occurred.isoformat(),
            "dataLabel": "MOCK DATA",
        },
    )


def _fill_event(row: dict) -> RawEventDraft:
    price = trade_fill_price(row)
    commission, fx_fee = fees_for(row["broker"], row["quantity"], price)
    occurred = executed_at(row)
    event_id = f"MOCK-IBKR-{row['symbol']}-{row['action'].value}-{row['day'].isoformat()}"
    return RawEventDraft(
        broker=Broker.IBKR,
        broker_event_id=event_id,
        event_type="EXECUTION",
        occurred_at=occurred,
        raw_payload={
            "msgType": "EXECUTION",
            "execId": event_id,
            "conid": _CONID[row["symbol"]],
            "symbol": row["symbol"],
            "side": _SIDE[row["action"]],
            "shares": str(row["quantity"]),
            "price": str(price),
            "currency": "USD",
            "fxRateToGbp": str(FX_TO_GBP),
            "commission": str(commission),
            "fxFee": str(fx_fee),
            "time": occurred.isoformat(),
            "dataLabel": "MOCK DATA",
        },
    )


def ibkr_events() -> list[RawEventDraft]:
    events = [_opening_event()]
    events.extend(_fill_event(row) for row in TRADE_PLAN if row["broker"] is Broker.IBKR)
    return events


def normalise_ibkr(event: RawEventDraft) -> NormalisedEvent:
    payload = event.raw_payload
    if payload.get("msgType") == "CASH_BALANCE":
        return NormalisedEvent(
            cash=CashMovementDraft(
                movement_type=CashMovementType.OPENING,
                amount_gbp=D(str(payload["cash"])),
                occurred_at=event.occurred_at,
                note="MOCK DATA opening cash",
            )
        )
    if payload.get("msgType") != "EXECUTION":
        return NormalisedEvent()
    side = {"BOT": TradeAction.BUY, "SLD": TradeAction.SELL}[payload["side"]]
    return NormalisedEvent(
        trade=CanonicalTradeDraft(
            broker_trade_id=str(payload["execId"]),
            action=side,
            quantity=D(str(payload["shares"])),
            fill_price=D(str(payload["price"])),
            trade_currency=str(payload["currency"]),
            fx_rate_to_gbp=D(str(payload["fxRateToGbp"])),
            fees_native=D(str(payload.get("commission", "0"))),
            fx_fee_native=D(str(payload.get("fxFee", "0"))),
            executed_at=event.occurred_at,
            symbol=str(payload["symbol"]),
        )
    )
