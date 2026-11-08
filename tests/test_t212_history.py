from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from connectors.trading212.client import Trading212AuthError, Trading212Client, Trading212HTTPError
from connectors.trading212.history import (
    classify_order,
    order_event_id,
    reconcile_positions,
    reconstruct_quantities,
)
from core.config import Settings
from core.enums import TradeAction
from core.money import D


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        t212_base_url="https://live.trading212.com/api/v0",
        t212_api_key="test-key",
        t212_api_secret="test-secret",
        t212_read_only=True,
    )


def _order(fill_id, side="BUY", status="FILLED", quantity="1.5", price="10", fill_type="TRADE", ticker="MU_US_EQ"):
    return {
        "fill": {
            "id": fill_id,
            "filledAt": "2026-09-28T18:14:47.000Z",
            "price": price,
            "quantity": quantity,
            "type": fill_type,
            "walletImpact": {"currency": "GBP", "fxRate": "1.25", "taxes": []},
        },
        "order": {
            "id": 1000 + int(fill_id),
            "side": side,
            "status": status,
            "createdAt": "2026-09-28T18:14:46.000Z",
            "currency": "GBP",
            "ticker": ticker,
            "instrument": {"ticker": ticker, "currency": "USD", "name": "Example"},
        },
    }


def test_pagination_follows_next_page_path_and_stops():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path + ("?" + request.url.query.decode() if request.url.query else ""))
        if b"cursor=2" in request.url.query:
            return httpx.Response(200, json={"items": [{"id": 2}], "nextPagePath": None})
        return httpx.Response(
            200,
            json={"items": [{"id": 1}], "nextPagePath": "/api/v0/equity/history/orders?limit=1&cursor=2"},
        )

    client = Trading212Client(_settings(), transport=httpx.MockTransport(handler), sleeper=lambda _s: None, history_interval=0)
    rows = client.get_historical_orders(limit=1)
    assert [row["id"] for row in rows] == [1, 2]
    assert seen[1].startswith("/api/v0/equity/history/orders")
    assert "cursor=2" in seen[1]


def test_pagination_rejects_a_repeated_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": 1}], "nextPagePath": "/equity/history/orders?limit=1"},
        )

    client = Trading212Client(_settings(), transport=httpx.MockTransport(handler), sleeper=lambda _s: None, history_interval=0)
    with pytest.raises(Trading212HTTPError):
        client.get_historical_orders(limit=1)


def test_cancelled_orders_are_not_trades_and_fills_keep_their_own_ids():
    assert classify_order(_order(1, status="CANCELLED")) == "ignore"
    assert classify_order(_order(2, status="REJECTED")) == "ignore"
    assert classify_order(_order(3, fill_type="STOCK_SPLIT", quantity="-4")) == "corporate"
    first = _order(11, quantity="0.25")
    second = _order(12, quantity="0.75")
    second["order"]["id"] = first["order"]["id"]
    assert order_event_id(first) != order_event_id(second)
    assert classify_order(first) == "trade"


def test_buy_sell_and_fractional_reconstruction():
    totals = reconstruct_quantities(
        [
            ("MU_US_EQ", TradeAction.BUY, D("0.5")),
            ("MU_US_EQ", TradeAction.BUY, D("0.25")),
            ("MU_US_EQ", TradeAction.SELL, D("0.25")),
            ("NOK_US_EQ", TradeAction.BUY, D("2")),
            ("NOK_US_EQ", TradeAction.SELL, D("2")),
        ]
    )
    assert totals["MU_US_EQ"] == Decimal("0.50")
    assert "NOK_US_EQ" not in totals


def test_reconciliation_pass_and_fail():
    passed = reconcile_positions(
        [{"instrument": {"ticker": "MU_US_EQ"}, "quantity": "10"}],
        {"MU_US_EQ": D("10")},
    )
    assert passed["overall"] == "PASS"
    failed = reconcile_positions(
        [{"instrument": {"ticker": "MU_US_EQ"}, "quantity": "10"}],
        {"MU_US_EQ": D("9")},
    )
    assert failed["overall"] == "FAIL"
    assert failed["quantity_mismatches"] == 1


def test_missing_instrument_is_a_failure():
    report = reconcile_positions([], {}, unmapped=1)
    assert report["overall"] == "FAIL"
    assert report["unmapped_instruments"] == 1


def test_event_id_fingerprint_is_stable_when_the_broker_id_is_missing():
    item = {"order": {"ticker": "MU_US_EQ", "side": "BUY", "createdAt": "2026-01-01T00:00:00Z"}, "fill": {}}
    assert order_event_id(item) == order_event_id(dict(item))
    assert order_event_id(item).startswith("order-fp:")


def test_authentication_errors_are_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401)

    client = Trading212Client(_settings(), transport=httpx.MockTransport(handler), sleeper=lambda _s: (_ for _ in ()).throw(AssertionError("slept")))
    with pytest.raises(Trading212AuthError):
        client.get_historical_orders()
    assert calls["count"] == 1


def test_transient_retry_stays_bounded():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(429)

    client = Trading212Client(_settings(), transport=httpx.MockTransport(handler), sleeper=lambda _s: None, history_interval=0)
    with pytest.raises(Exception):
        client.get_transactions()
    assert calls["count"] == 3


def test_sync_rerun_does_not_duplicate_rows():
    pytest.importorskip("psycopg")
    from db.session import SessionLocal
    from services.t212_sync import sync_trading212_history

    session = SessionLocal()
    try:
        session.execute(text_ready())
    except Exception as exc:
        session.close()
        pytest.skip(f"database unavailable: {exc.__class__.__name__}")

    class FakeClient:
        def get_account_summary(self):
            return {"currency": "GBP"}

        def get_instruments(self):
            return [{"ticker": "ZZ_US_EQ", "name": "Zed", "currencyCode": "USD", "isin": "US0000000001"}]

        def get_historical_orders(self):
            return [
                _order(501, quantity="1.25", ticker="ZZ_US_EQ"),
                _order(502, status="CANCELLED", quantity="9", ticker="ZZ_US_EQ"),
            ]

        def get_transactions(self):
            return [{"reference": "tx-1", "type": "DEPOSIT", "amount": "25", "currency": "GBP", "dateTime": "2026-01-02T00:00:00Z"}]

        def get_dividends(self):
            return [{
                "reference": "div-1",
                "amount": "1.5",
                "currency": "GBP",
                "paidOn": "2026-08-11T13:58:59.000+03:00",
                "ticker": "ZZ_US_EQ",
                "type": "ORDINARY",
                "instrument": {"ticker": "ZZ_US_EQ", "currency": "USD", "name": "Zed"},
            }]

        def get_positions(self):
            return [{"instrument": {"ticker": "ZZ_US_EQ"}, "quantity": "1.25"}]

    try:
        first = sync_trading212_history(session, FakeClient())
        second = sync_trading212_history(session, FakeClient())
        assert first.trades_created == 1
        assert first.dividends_created == 1
        assert first.cash_created == 2
        assert second.trades_created == 0
        assert second.raw_inserted == 0
        assert second.raw_known >= 4
    finally:
        session.rollback()
        session.close()


def text_ready():
    from sqlalchemy import text

    return text("SELECT 1")
