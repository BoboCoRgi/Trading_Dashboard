from connectors.base import BrokerReadConnector
from connectors.ibkr import IBKRReadConnector
from connectors.market_data.series import END, PEAK, START, price_on
from connectors.trading212 import Trading212ReadConnector
from core.enums import TradeAction
from core.money import D


def test_mock_prices_are_anchored():
    assert price_on("RKLB", START) == D("16.40")
    assert price_on("RKLB", PEAK) == D("64.20")
    assert price_on("RKLB", END) == D("58.10")
    assert price_on("RKLB", PEAK) > price_on("RKLB", END) > price_on("RKLB", START)


def test_t212_and_ibkr_normalise_into_the_same_trade_shape():
    t212 = Trading212ReadConnector()
    ibkr = IBKRReadConnector()
    t212_fill = next(event for event in t212.fetch_events() if event.event_type == "FILL" and "RKLB" in event.broker_event_id and "BUY" in event.broker_event_id)
    ibkr_fill = next(event for event in ibkr.fetch_events() if event.event_type == "EXECUTION")
    t212_trade = t212.normalise(t212_fill).trade
    ibkr_trade = ibkr.normalise(ibkr_fill).trade
    assert t212_trade is not None and ibkr_trade is not None
    assert t212_trade.symbol == "RKLB"
    assert t212_trade.action is TradeAction.BUY
    assert ibkr_trade.action is TradeAction.BUY
    assert t212_trade.fx_rate_to_gbp == ibkr_trade.fx_rate_to_gbp
    assert "ticker" in t212_fill.raw_payload
    assert "conid" in ibkr_fill.raw_payload


def test_opening_cash_is_normalised_without_a_trade():
    event = next(event for event in Trading212ReadConnector().fetch_events() if event.event_type == "OPENING_BALANCE")
    effect = Trading212ReadConnector().normalise(event)
    assert effect.trade is None
    assert effect.cash is not None
    assert effect.cash.amount_gbp == D(35000)


def test_each_account_keeps_cash_non_negative():
    from analytics.portfolio import Fill, Mark, replay
    from connectors.market_data.series import END, price_on
    from connectors.mock_book import INSTRUMENTS, OPENING_CASH
    from core.money import D

    marks = {
        spec["symbol"]: Mark(spec["symbol"], price_on(spec["symbol"], END), D("0.78"))
        for spec in INSTRUMENTS
    }
    for key, connector_cls in (("t212", Trading212ReadConnector), ("ibkr", IBKRReadConnector)):
        fills = []
        for event in connector_cls().fetch_events():
            trade = connector_cls().normalise(event).trade
            if trade is None:
                continue
            fills.append(
                Fill(
                    "account",
                    trade.symbol,
                    trade.action.value,
                    trade.quantity,
                    trade.fill_price,
                    trade.fx_rate_to_gbp,
                    trade.fees_native,
                    trade.fx_fee_native,
                    trade.executed_at,
                )
            )
        state = replay(fills, marks, OPENING_CASH[key])
        assert state.cash_gbp > 0
    merged = {}
    for connector_cls in (Trading212ReadConnector, IBKRReadConnector):
        for event in connector_cls().fetch_events():
            trade = connector_cls().normalise(event).trade
            if trade is None or trade.symbol != "RKLB" or trade.action.value != "BUY":
                continue
            merged["qty"] = merged.get("qty", D(0)) + trade.quantity
    assert merged["qty"] > 900


def test_connectors_are_read_only():
    forbidden = {"place_order", "submit_order", "create_order", "cancel_order", "modify_order", "execute_trade"}
    for connector in (Trading212ReadConnector, IBKRReadConnector, BrokerReadConnector):
        assert forbidden.isdisjoint(set(dir(connector)))
