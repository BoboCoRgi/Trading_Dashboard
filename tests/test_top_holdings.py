"""Top holdings charts, cache, and indicators. No network and no provider key."""

import inspect
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from analytics.chart_ranges import interval_for, range_spec
from analytics.holdings import CostFigure, CombinedPosition
from analytics.market_stats import MarketBar, daily_relative_volume, simple_moving_average
from analytics.support_resistance import support_resistance_zones
from analytics.timeline import decision_marker, dedupe_markers, stored_event_marker, trade_marker
from app.dashboard import views
from app.dashboard.charts import stock_mini_chart
from app.dashboard.theme import CORAL, SAGE
from app.dashboard.views import stock_detail, today, top_holdings
from connectors.market_data.bars import PriceBar, bar_identity, upsert_bars
from connectors.market_data.twelvedata import MarketDataError, TwelveDataProvider, normalise_time_series
from core.money import D
from services.market_data import MarketDataService, plan_fetch
from services.top_holdings import (
    build_holding_cards,
    card_from_series,
    chart_average_cost,
    daily_rvol,
    moving_average_series,
    range_return,
    select_top_holdings,
    vwap_for_chart,
)

NOW = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)  # Wednesday
SECRET = "super-secret-twelve-key"


def test_top_holdings_are_ranked_by_weight_and_capped_at_five():
    positions = [_position(symbol, D(weight)) for symbol, weight in (
        ("MU", "0.10"),
        ("RKLB", "0.80"),
        ("AAA", "0.02"),
        ("BBB", "0.03"),
        ("CCC", "0.04"),
        ("DDD", "0.05"),
    )]
    chosen = select_top_holdings(positions)
    assert [row.symbol for row in chosen] == ["RKLB", "MU", "DDD", "CCC", "BBB"]
    assert len(chosen) == 5


def test_fewer_than_five_holdings_are_not_padded():
    positions = [_position("RKLB", D("0.5")), _position("MU", D("0.2"), instrument_id="22222222-2222-2222-2222-222222222222")]
    assert [row.symbol for row in select_top_holdings(positions)] == ["RKLB", "MU"]


def test_closed_positions_are_excluded():
    closed = _position("RKLB", D("0.9"))
    closed = CombinedPosition(**{**closed.__dict__, "quantity": D("0")})
    assert select_top_holdings([closed, _position("MU", D("0.1"), instrument_id="22222222-2222-2222-2222-222222222222")])[0].symbol == "MU"


def test_current_symbol_is_used_instead_of_a_historical_alias():
    current = _position("RKLB", D("0.818"))
    chosen = select_top_holdings([current])
    assert chosen[0].symbol == "RKLB"
    assert chosen[0].symbol != "VACQ"


def test_twelve_data_payload_becomes_ohlcv_bars():
    bars = normalise_time_series(
        {
            "meta": {"currency": "USD"},
            "values": [
                {"datetime": "2026-10-01", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "100"},
                {"datetime": "2026-10-02", "open": "11", "high": "13", "low": "10", "close": "12", "volume": "80"},
            ],
        },
        symbol="RKLB",
        interval="1D",
    )
    assert bars[0].close == D("11")
    assert bars[0].volume == D("100")
    assert bars[0].currency == "USD"
    assert bars[0].interval == "1D"
    assert bars[0].source == "twelvedata"
    assert bars[0].is_delayed is True
    assert bars[1].timestamp == datetime(2026, 10, 2, tzinfo=timezone.utc)


def test_provider_maps_range_interval_and_hides_the_key():
    seen = {}

    def transport(path, params):
        seen["path"] = path
        seen["params"] = dict(params)
        return 200, json.dumps({"meta": {"currency": "USD"}, "values": [{"datetime": "2026-10-01", "close": "11"}]})

    provider = TwelveDataProvider(SECRET, transport=transport)
    bars = provider.fetch("RKLB", "1D", NOW - timedelta(days=30), NOW)
    assert seen["params"]["interval"] == "1day"
    assert seen["params"]["apikey"] == SECRET
    assert SECRET not in repr(bars)
    assert bars[0].interval == "1D"


def test_rate_limit_does_not_repeat_the_key():
    provider = TwelveDataProvider(SECRET, transport=lambda path, params: (429, f"apikey={SECRET}"))
    try:
        provider.fetch("RKLB", "1D", NOW - timedelta(days=1), NOW)
    except MarketDataError as exc:
        assert exc.kind == "rate_limit"
        assert SECRET not in str(exc)
    else:
        raise AssertionError("rate limit was not reported")


def test_cached_series_is_not_fetched_again():
    cache = _MemoryCache()
    calls = []

    def fetch(symbol, interval, start, end):
        calls.append((start, end))
        return [_price(start, interval), _price(end, interval)]

    service = MarketDataService(cache, _Provider(fetch), now=NOW)
    kwargs = {"instrument_id": "11111111-1111-1111-1111-111111111111", "symbol": "RKLB", "range_code": "1M", "currency": "USD"}
    first = service.series(**kwargs)
    second = service.series(**kwargs)
    assert first.status == "ready"
    assert len(calls) == 1
    assert second.status == "ready"
    assert len(calls) == 1
    assert len(cache.rows) == 2


def test_missing_tail_does_not_request_the_whole_history():
    start = NOW - timedelta(days=31)
    cached = [_price(start, "1D"), _price(NOW - timedelta(days=2), "1D")]
    windows = plan_fetch(cached, start=start, end=NOW, now=NOW, interval="1D")
    assert windows == [(cached[-1].timestamp, NOW)]


def test_a_completed_daily_bar_is_fresh_over_the_weekend():
    friday = datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    start = saturday - timedelta(days=31)
    windows = plan_fetch([_price(start, "1D"), _price(friday, "1D")], start=start, end=saturday, now=saturday, interval="1D")
    assert windows == []


def test_upsert_replaces_the_same_bar():
    first = _price(NOW, "1D")
    second = PriceBar(**{**first.__dict__, "close": D("20")})
    once = upsert_bars({}, [first])
    twice = upsert_bars(once, [second])
    assert len(twice) == 1
    assert twice[bar_identity(first)].close == D("20")


def test_one_month_selection_drops_older_bars():
    cache = _MemoryCache([
        _price(NOW - timedelta(days=100), "1D"),
        _price(NOW - timedelta(days=10), "1D"),
    ])
    result = MarketDataService(cache, None, now=NOW).series(
        instrument_id="11111111-1111-1111-1111-111111111111",
        symbol="RKLB",
        range_code="1M",
        currency="USD",
    )
    assert [bar.timestamp for bar in result.bars] == [NOW - timedelta(days=10)]
    assert interval_for("1M") == "1D"


def test_trade_markers_keep_the_execution_timestamp():
    moment = datetime(2026, 10, 3, 14, 35, tzinfo=timezone.utc)
    buy = trade_marker(
        instrument_id="11111111-1111-1111-1111-111111111111",
        trade_id="buy-1",
        timestamp=moment,
        action="BUY",
        quantity=D("4"),
        price=D("50"),
        currency="USD",
    )
    sell = trade_marker(
        instrument_id="11111111-1111-1111-1111-111111111111",
        trade_id="sell-1",
        timestamp=moment + timedelta(hours=1),
        action="SELL",
        quantity=D("1"),
        price=D("55"),
        currency="USD",
    )
    assert buy.event_type == "TRADE_BUY"
    assert sell.event_type == "TRADE_SELL"
    assert buy.timestamp == moment
    assert sell.price == D("55")
    assert "4" in buy.description


def test_repeated_event_titles_collapse_to_one_marker():
    moment = datetime(2026, 10, 3, tzinfo=timezone.utc)
    copies = [
        stored_event_marker(
            instrument_id="1",
            event_id=str(index),
            timestamp=moment,
            classification="COMPANY",
            note="Q3 Earnings",
        )
        for index in range(15)
    ]
    decision = decision_marker(
        instrument_id="1",
        decision_id="d1",
        timestamp=moment,
        decision_type="HOLD",
        reasoning="Thesis still holds",
        conviction=D("0.7"),
    )
    kept = dedupe_markers([*copies, decision])
    assert len([marker for marker in kept if marker.title == "Q3 Earnings"]) == 1
    assert any(marker.event_type == "DECISION" and "Conviction" in marker.description for marker in kept)


def test_range_intervals_stay_out_of_the_views():
    assert interval_for("1D") == "5min"
    assert interval_for("5D") == "1h"
    assert interval_for("3M") == "1D"
    assert interval_for("1Y") == "1D"
    assert range_spec("1M").intraday is False
    assert range_spec("1D").intraday is True
    for module in (today, top_holdings, stock_detail):
        assert "1day" not in inspect.getsource(module)


def test_moving_averages_and_daily_rvol_and_vwap():
    closes = [D("10")] * 200
    averages = {window: simple_moving_average(closes, window) for window in (20, 50, 200)}
    assert averages[20][18] is None
    assert averages[20][19] == D("10")
    assert averages[50][49] == D("10")
    assert averages[200][199] == D("10")
    bars = [_price(NOW - timedelta(days=offset), "1D", close=D("10"), volume=D("10")) for offset in range(20, -1, -1)]
    bars[-1] = _price(bars[-1].timestamp, "1D", close=D("10"), volume=D("20"))
    assert daily_rvol(bars) == daily_relative_volume(session_volume=D("20"), prior_session_volumes=[D("10")] * 20)
    assert daily_rvol(bars[:10]) is None
    packed = moving_average_series(bars)
    assert packed[20][-1] == D("10")
    intraday = [
        _price(NOW, "5min", close=D("13"), volume=D("100"), high=D("13"), low=D("10"), open_=D("10")),
        _price(NOW + timedelta(minutes=5), "5min", close=D("20"), volume=D("100"), high=D("22"), low=D("14"), open_=D("20")),
    ]
    series = vwap_for_chart(intraday)
    assert series[0] == D("12")
    assert series[1] is not None
    assert vwap_for_chart([_price(NOW, "1D")]) == [None]


def test_support_and_resistance_need_repeated_touches():
    bars = []
    for index in range(30):
        low, high, close = "49", "51", "50"
        if index in {5, 12, 20}:
            low, high, close = "46.2", "47.0", "46.6"
        if index in {8, 15, 24}:
            low, high, close = "55.4", "56.4", "56.0"
        bars.append(_market(index, low, high, close))
    zones = support_resistance_zones(bars)
    assert [zone.kind for zone in zones] == ["support", "resistance"]
    assert zones[0].touches >= 2
    assert zones == support_resistance_zones(bars)
    rising = [_market(index, str(10 + index), str(11 + index), str(10 + index)) for index in range(30)]
    assert support_resistance_zones(rising) == []


def test_average_cost_must_match_the_chart_currency():
    position = _position("RKLB", D("0.5"))
    assert chart_average_cost(position, "USD") == D("11")
    gbp_only = CombinedPosition(**{**position.__dict__, "native_cost": CostFigure(None, "", "missing")})
    assert chart_average_cost(gbp_only, "USD") is None
    assert chart_average_cost(gbp_only, "GBP") == D("9")


def test_one_failed_chart_does_not_drop_the_others_or_leak_errors():
    good = _position("RKLB", D("0.8"))
    bad = _position("MU", D("0.1"), instrument_id="22222222-2222-2222-2222-222222222222")
    series = MarketDataService(_MemoryCache([_price(NOW - timedelta(days=10), "1D"), _price(NOW, "1D")]), None, now=NOW).series(
        instrument_id=good.instrument_id,
        symbol="RKLB",
        range_code="1M",
        currency="USD",
    )

    def load(position):
        if position.symbol == "MU":
            raise RuntimeError(SECRET)
        return card_from_series(position, series, [])

    cards = build_holding_cards([good, bad], load)
    assert [card.symbol for card in cards] == ["RKLB", "MU"]
    assert cards[0].message is None
    assert cards[0].latest_price == D("11")
    assert cards[1].message == "Price history temporarily unavailable."
    assert SECRET not in cards[1].message
    assert "Top holdings — price & events" in inspect.getsource(top_holdings.render_top_holdings)
    assert "except Exception" in inspect.getsource(top_holdings.render_top_holdings)
    assert "What's driving my P&L today?" in inspect.getsource(today.render)


def test_missing_provider_is_a_clean_empty_state():
    result = MarketDataService(_MemoryCache(), None, now=NOW).series(
        instrument_id="11111111-1111-1111-1111-111111111111",
        symbol="RKLB",
        range_code="1M",
        currency="USD",
    )
    assert result.status == "missing_key"
    assert result.bars == []
    assert SECRET not in result.message
    assert "configured" in result.message


def test_mini_chart_uses_the_sign_of_the_range_and_not_a_zero_baseline():
    bars = [{"time": "2026-10-01", "close": 10.0}, {"time": "2026-10-02", "close": 12.0}]
    chart = stock_mini_chart(bars, [], positive=True)
    assert chart.data[0].line.color == SAGE
    assert stock_mini_chart(bars, [], positive=False).data[0].line.color == CORAL
    assert chart.layout.yaxis.rangemode == "normal"
    assert range_return([_price(NOW - timedelta(days=1), "1D", close=D("10")), _price(NOW, "1D", close=D("11"))]) == D("0.1")


def test_views_package_still_imports():
    assert views is not None


class _Provider:
    def __init__(self, fetch) -> None:
        self.fetch = fetch


class _MemoryCache:
    def __init__(self, rows: list[PriceBar] | None = None) -> None:
        self.rows = list(rows or [])

    def load(self, instrument_id, interval, start, end):
        return [
            bar for bar in self.rows
            if bar.instrument_id == instrument_id and bar.interval == interval and start <= bar.timestamp <= end
        ]

    def save(self, bars):
        self.rows = list(upsert_bars({bar_identity(bar): bar for bar in self.rows}, bars).values())


def _position(symbol: str, weight: Decimal, instrument_id: str = "11111111-1111-1111-1111-111111111111") -> CombinedPosition:
    return CombinedPosition(
        symbol=symbol,
        company=f"{symbol} Co",
        instrument_id=instrument_id,
        quantity=D("2"),
        price=D("10"),
        price_currency="USD",
        price_note="",
        market_value_gbp=D("16"),
        weight=weight,
        unrealised_gbp=D("1"),
        unrealised_pct=D("0.1"),
        daily_change_pct=None,
        daily_pnl_gbp=None,
        gbp_cost=CostFigure(D("9"), "GBP", "gbp"),
        native_cost=CostFigure(D("11"), "USD", "native"),
        lots=(),
    )


def _price(moment, interval, close=D("11"), volume=D("100"), high=None, low=None, open_=None) -> PriceBar:
    return PriceBar(
        timestamp=moment,
        interval=interval,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        currency="USD",
        source="twelvedata",
        is_delayed=True,
        symbol="RKLB",
        instrument_id="11111111-1111-1111-1111-111111111111",
        fetched_at=moment,
    )


def _market(index: int, low: str, high: str, close: str) -> MarketBar:
    return MarketBar(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
        interval="1D",
        open=D(close),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D("1000"),
        currency="USD",
        source="test",
    )
