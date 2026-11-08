# connectors/market_data

Market data boundary.

| Module | Role |
| --- | --- |
| `provider.py` | `MarketDataProvider` and `BenchmarkProvider`. The stored reader uses `market_price_bar`, `price_observation`, and `benchmark_mapping` |
| `series.py` | Mock weekday closes used by seed data |

`get_quote`, `get_intraday_bars`, `get_daily_bars`, and `get_volume_history` return only stored rows. Open, high, low, and volume are not copied from a close. A benchmark is used only when `benchmark_mapping` names it.
