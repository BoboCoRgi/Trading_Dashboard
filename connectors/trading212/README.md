# connectors/trading212

Read-only Trading 212 client.

| Module | Role |
| --- | --- |
| `client.py` | GET client. It cannot place, change, or cancel an order |
| `connector.py` | Account and position reads |
| `history.py` | History pages used by the sync script |
| `normalise.py` | Maps Trading 212 payloads into canonical rows |

The dashboard does not call this package while rendering. Ingestion scripts write `LIVE` rows, and the page reads those rows from PostgreSQL.
