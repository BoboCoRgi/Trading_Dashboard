# services

Application workflows shared by FastAPI and Streamlit.

| Module | Role |
| --- | --- |
| `portfolio_source.py` | Mock or live snapshot provider from `DATA_MODE` |
| `ledger.py` | Latest snapshot and the one before it, same origin |
| `holdings.py` | Combined book and stock detail |
| `queries.py` | Today, risk, decision, and research view models |
| `decisions.py` | Decision ledger writes |
| `t212_sync.py`, `t212_snapshot.py` | Live ingestion. Pages do not call these |
| `scan.py` | Market-event and radar refresh for mock scans |
| `orchestrator.py`, `ai_store.py`, `triggers.py` | Analysis runs and their stored audit |
| `scheduler.py` | Snapshot loop. It is not started by Streamlit |
| `seed_demo.py` | Mock book used by `scripts/seed.py` |

Live mode reads the latest `LIVE` snapshot. It does not mix in `MOCK` rows and it does not call the broker during a page render.
