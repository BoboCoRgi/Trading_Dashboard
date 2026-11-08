import base64
from decimal import Decimal

import httpx
import pytest

from connectors.trading212.client import (
    Trading212AuthError,
    Trading212Client,
    Trading212ConfigError,
    Trading212TransientError,
    sanitised_connection_summary,
)
from core.config import Settings


def _settings() -> Settings:
    return Settings(
        t212_enabled=True,
        t212_base_url="https://live.trading212.com/api/v0",
        t212_api_key="test-key",
        t212_api_secret="test-secret",
        t212_read_only=True,
    )


def _client(handler, sleeper=None) -> Trading212Client:
    return Trading212Client(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleeper=sleeper or (lambda _seconds: None),
    )


def test_account_summary_and_positions_use_basic_auth():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        decoded = base64.b64decode(request.headers["Authorization"].removeprefix("Basic ")).decode()
        assert decoded == "test-key:test-secret"
        if request.url.path.endswith("/equity/account/summary"):
            return httpx.Response(200, json={"currency": "GBP", "id": 424242, "totalValue": 1000.5})
        return httpx.Response(200, json=[{"ticker": "MU_US_EQ"}, {"ticker": "RKLB_US_EQ"}])

    client = _client(handler)
    summary = client.get_account_summary()
    positions = client.get_positions()
    text = sanitised_connection_summary(summary, positions)
    assert seen == ["/api/v0/equity/account/summary", "/api/v0/equity/positions"]
    assert "Trading 212 connection: OK" in text
    assert "Currency: GBP" in text
    assert "£1,000.50" in text
    assert "Open positions: 2" in text
    assert "Symbols: MU, RKLB" in text
    assert "424242" not in text
    assert "test-secret" not in text


def test_live_position_shape_reads_the_nested_ticker():
    text = sanitised_connection_summary(
        {"currency": "GBP", "id": 7, "totalValue": Decimal("10")},
        [{"instrument": {"ticker": "VRT_US_EQ", "isin": "XX0000000000"}, "quantity": 1}],
    )
    assert "Symbols: VRT" in text
    assert "XX0000000000" not in text
    assert "7" not in text


def test_authentication_errors_are_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"error": "bad"})

    sleeps = []
    client = _client(handler, sleeper=sleeps.append)
    with pytest.raises(Trading212AuthError):
        client.get_account_summary()
    assert calls["count"] == 1
    assert sleeps == []


def test_transient_status_is_retried_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "9"})
        if calls["count"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"currency": "GBP", "totalValue": 1})

    sleeps = []
    client = _client(handler, sleeper=sleeps.append)
    summary = client.get_account_summary()
    assert summary["totalValue"] == 1
    assert calls["count"] == 3
    assert sleeps[0] == 2.0
    assert sleeps[1] == 1.0


def test_persistent_server_error_stops_after_the_attempt_limit():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500)

    client = _client(handler)
    with pytest.raises(Trading212TransientError):
        client.get_positions()
    assert calls["count"] == 3


def test_missing_credentials_do_not_call_the_network():
    settings = Settings(t212_api_key="", t212_api_secret="")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network was called")

    client = Trading212Client(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(Trading212ConfigError):
        client.get_positions()


def test_write_requests_are_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network was called")

    client = _client(handler)
    with pytest.raises(Trading212ConfigError):
        client._request("POST", "/equity/orders")


def test_logs_do_not_include_the_secret(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with caplog.at_level("INFO", logger="connectors.trading212"):
        _client(handler).get_positions()
    assert "test-secret" not in caplog.text
    assert "/equity/positions" in caplog.text


def test_default_live_base_url_is_the_documented_host():
    settings = Settings(_env_file=None)
    assert settings.t212_base_url == "https://live.trading212.com/api/v0"
    assert settings.data_mode == "mock"
    assert settings.t212_read_only is True
    assert settings.t212_api_key == ""
    assert settings.t212_api_secret == ""
