# connectors

Read-only boundaries to brokers and to market data that is not in the database yet.

| Path | Role |
| --- | --- |
| `base.py` | Read connector contract. It has no order methods |
| `mock_book.py` | Fixture book used by mock mode |
| `trading212/` | Trading 212 read client, history, and normalisation |
| `ibkr/` | IBKR read connector. It does not invent positions |
| `market_data/` | Quote, bar, and benchmark interfaces |
| `research_data/` | News, fundamentals, and ownership interfaces |

Domain code depends on these interfaces. It does not call one vendor's URL directly. API keys stay in the environment and are not sent to the frontend or to a model.
