# scripts

Command-line jobs. None of them place an order.

| Script | Role |
| --- | --- |
| `seed.py` | Resets and loads mock data. Refuses to run unless `APP_ENV=local`, and refuses if any row is `LIVE` |
| `sync_trading212_history.py` | Archives Trading 212 history as `LIVE` rows |
| `create_t212_live_snapshot.py` | Writes one live portfolio snapshot from the current account |
| `run_t212_snapshot_scheduler.py` | Repeats that snapshot on the configured interval |
| `test_trading212_connection.py` | Checks that the read-only client can reach the account |

Run them with the virtualenv Python from the repository root, for example:

```powershell
.\.venv\Scripts\python.exe scripts/create_t212_live_snapshot.py
```
