# tests

Pytest suite. Database tests skip when PostgreSQL is not accepting connections, so the maths tests can still run.

| Module | Covers |
| --- | --- |
| `test_analytics.py` | Portfolio maths |
| `test_connectors.py` | Read-only connector normalisation |
| `test_trading212_client.py` | Trading 212 client behaviour |
| `test_t212_history.py`, `test_t212_phase3.py` | Live history sync and snapshots |
| `test_data_mode.py`, `test_database_flow.py` | Mock and live separation |
| `test_events.py` | Market-event classification |
| `test_holdings_phase1.py` | Combined cost, relative volume, VWAP, charts |
| `test_ai_safety.py` | Prompt context does not carry secrets or order tools |

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Tests that insert rows roll them back. They do not leave a fixture book in the local database.
