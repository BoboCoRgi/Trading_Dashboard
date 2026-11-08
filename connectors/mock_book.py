from datetime import date, datetime, timezone
from decimal import Decimal

from core.enums import Broker, TradeAction
from core.money import D
from connectors.market_data.series import FX_TO_GBP, price_on

OPENING = date(2026, 1, 5)


def _at(day: date, hour: int = 15) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 30, tzinfo=timezone.utc)


# account_key is internal to the mock book. It is not a broker account number.
TRADE_PLAN: list[dict] = [
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "MRVL", "action": TradeAction.BUY, "quantity": D("200"), "day": date(2026, 1, 20)},
    {"broker": Broker.IBKR, "account_key": "ibkr", "symbol": "META", "action": TradeAction.BUY, "quantity": D("25"), "day": date(2026, 2, 2)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "RKLB", "action": TradeAction.BUY, "quantity": D("500"), "day": date(2026, 2, 10)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "NOK", "action": TradeAction.BUY, "quantity": D("1200"), "day": date(2026, 3, 1)},
    {"broker": Broker.IBKR, "account_key": "ibkr", "symbol": "MU", "action": TradeAction.BUY, "quantity": D("80"), "day": date(2026, 3, 12)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "RKLB", "action": TradeAction.BUY, "quantity": D("250"), "day": date(2026, 4, 3)},
    {"broker": Broker.IBKR, "account_key": "ibkr", "symbol": "VRT", "action": TradeAction.BUY, "quantity": D("80"), "day": date(2026, 4, 28)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "MRVL", "action": TradeAction.SELL, "quantity": D("50"), "day": date(2026, 5, 22)},
    {"broker": Broker.IBKR, "account_key": "ibkr", "symbol": "META", "action": TradeAction.BUY, "quantity": D("15"), "day": date(2026, 6, 2)},
    {"broker": Broker.IBKR, "account_key": "ibkr", "symbol": "RKLB", "action": TradeAction.BUY, "quantity": D("200"), "day": date(2026, 6, 18)},
    {"broker": Broker.IBKR, "account_key": "ibkr", "symbol": "MU", "action": TradeAction.BUY, "quantity": D("40"), "day": date(2026, 7, 1)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "RKLB", "action": TradeAction.SELL, "quantity": D("50"), "day": date(2026, 8, 5)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "NOK", "action": TradeAction.SELL, "quantity": D("400"), "day": date(2026, 8, 12)},
    {"broker": Broker.TRADING212, "account_key": "t212", "symbol": "RKLB", "action": TradeAction.BUY, "quantity": D("25"), "day": date(2026, 9, 25)},
]

OPENING_CASH = {
    "t212": D("35000"),
    "ibkr": D("52000"),
}

INSTRUMENTS = [
    {"symbol": "RKLB", "company_name": "Rocket Lab", "exchange": "NASDAQ", "theme": "Space", "conid": "900001", "t212": "RKLB_US_EQ", "isin": "MOCK-ISIN-RKLB"},
    {"symbol": "MU", "company_name": "Micron", "exchange": "NASDAQ", "theme": "Semiconductors", "conid": "900002", "t212": "MU_US_EQ", "isin": "MOCK-ISIN-MU"},
    {"symbol": "MRVL", "company_name": "Marvell", "exchange": "NASDAQ", "theme": "Semiconductors", "conid": "900003", "t212": "MRVL_US_EQ", "isin": "MOCK-ISIN-MRVL"},
    {"symbol": "VRT", "company_name": "Vertiv", "exchange": "NYSE", "theme": "Data centre power", "conid": "900004", "t212": "VRT_US_EQ", "isin": "MOCK-ISIN-VRT"},
    {"symbol": "META", "company_name": "Meta Platforms", "exchange": "NASDAQ", "theme": "Social platforms", "conid": "900005", "t212": "META_US_EQ", "isin": "MOCK-ISIN-META"},
    {"symbol": "NOK", "company_name": "Nokia", "exchange": "NYSE", "theme": "Telecom", "conid": "900006", "t212": "NOK_US_EQ", "isin": "MOCK-ISIN-NOK"},
]

ACCOUNTS = [
    {
        "key": "t212",
        "broker": Broker.TRADING212,
        "account_type": "STOCKS_ISA",
        "display_name": "Trading 212 Stocks ISA",
        "external_account_ref": "MOCK-T212-ISA-001",
    },
    {
        "key": "ibkr",
        "broker": Broker.IBKR,
        "account_type": "STOCKS_ISA",
        "display_name": "IBKR Stocks ISA",
        "external_account_ref": "MOCK-IBKR-ISA-001",
    },
]


def fees_for(broker: Broker, quantity: Decimal, fill_price: Decimal) -> tuple[Decimal, Decimal]:
    notional = quantity * fill_price
    if broker is Broker.TRADING212:
        fx_fee = (notional * D("0.0015")).quantize(Decimal("0.01"))
        return D("0"), fx_fee
    return D("1.25"), D("0")


def trade_fill_price(row: dict) -> Decimal:
    return price_on(row["symbol"], row["day"])


def executed_at(row: dict) -> datetime:
    return _at(row["day"])
