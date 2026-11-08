# db/migrations/versions

Ordered schema revisions.

| Revision | Adds |
| --- | --- |
| `0001_initial` | Core schema from the SQLAlchemy models |
| `0002_events_radar` | Market events and opportunity radar tables |
| `0003_cash_movement_native` | Native-currency cash fields |
| `0004_live_snapshot_cleanup` | Live snapshot constraints and cleanup |
| `0005_market_bars` | `market_price_bar` and `benchmark_mapping` |

`0005` skips a table that `0001` already created. Run `alembic upgrade head` on an existing database before Holdings queries those tables.
