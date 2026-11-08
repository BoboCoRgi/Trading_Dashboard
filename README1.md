# Investment Intelligence

A personal decision-intelligence system for one investor. It records what was known when a decision was made, calculates portfolio risk in Python, and keeps AI analysis auditable.

Broker connections are read-only. The application cannot place, change, or cancel an order. V1 runs entirely on labeled mock data.

## Design choices

The repository root is the application. A nested `investment-intelligence/` folder would only add a directory.

A small `core` package holds enums, money helpers, and broker-agnostic drafts. Connectors, analytics, and the database all depend on it, and they do not depend on each other.

Money and quantities are `Decimal`. Weights and returns are fractions, not percents. Drawdown is zero or negative: `(value - high) / high`.

Buy fees are included in cost basis. Sell fees reduce proceeds. Sells use average cost. The same instrument in two accounts stays two positions, and concentration adds those weights together.

These tables were added because the requested flow needs them: `cash_movement`, `price_observation`, `journal_entry`, `decision_outcome`, `catalyst`, `watchlist_item`, `research_note`. Outcome rows are close-to-close instrument returns at 1, 5, 20, and 60 trading days. That is a marker, not a performance study.

AI context is built from calculated figures. The model is told not to recompute them. Claims are typed `FACT`, `INFERENCE`, `OPINION`, or `UNKNOWN`. If a figure was not supplied, the mock analyst says so. Mock runs still estimate token cost from a published rate table and label that estimate.

`AI_PROVIDER` defaults to `mock`, including when API keys exist. A checkbox is required before a run is sent to Anthropic or OpenAI.

Only three analysis triggers run in V1: `MANUAL_DEEP_DIVE`, `PRE_DECISION_REVIEW`, and `DAILY_CLOSE_REVIEW`. The others are named and return `disabled` or `not_implemented`.

The scheduler writes a snapshot only when a newer price exists. It is not started by Streamlit. It cannot trade.

FastAPI and Streamlit both call `services`. Replacing Streamlit later should not require rewriting the ledger or the risk engine.

## Layout

```
app/api/                FastAPI
app/dashboard/views/      Streamlit page functions (not a Streamlit `pages/` folder)
connectors/             Read-only Trading 212 and IBKR adapters
core/                   Shared enums, money, config
db/models/              SQLAlchemy models
db/migrations/          Alembic
db/repositories/        Read helpers used by services
analytics/              Deterministic calculations
ai/                     Providers, prompts, four roles
journal/                Journal assembly
research/               Notes and library
services/               Ingestion, snapshots, decisions, AI audit
scripts/seed.py         Reset and load MOCK DATA
tests/
```

## Setup

Python 3.11 or newer. On Windows, `python` may be older than `py -3.14`.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
docker compose up -d
alembic upgrade head
python -m scripts.seed
python -m pytest
streamlit run app/dashboard/streamlit_app.py
```

Optional API:

```powershell
uvicorn app.api.main:app --reload
```

Optional snapshot loop (does not trade):

```powershell
python -m services.scheduler
```

`scripts.seed` deletes the local database and reloads mock data. It refuses to run unless `APP_ENV=local`, and it refuses if any row is marked `LIVE`.

Open the dashboard at http://localhost:8501. The Streamlit config binds it to localhost.

## Schema

| Table | Role |
| --- | --- |
| broker_account | Internal account. `external_account_ref` is never sent to a model |
| instrument | One symbol, with mock Trading 212 and IBKR identifiers |
| raw_broker_event | Original payload, kept for reprocessing |
| trade | Canonical fill, native currency and GBP |
| cash_movement | Opening cash and the cash effect of each fill |
| price_observation | Mock weekday closes |
| portfolio_snapshot | Value, cash, drawdown, concentration |
| position_snapshot | Per account and instrument at that close |
| thesis | Bull, base, bear, invalidation, status |
| catalyst | Placeholder windows, not a live calendar |
| decision | BUY, SELL, HOLD, WATCH, PASS, with before/after snapshots |
| journal_entry | What you thought, expected, and what would prove you wrong |
| decision_outcome | 1D / 5D / 20D / 60D price-return marker |
| ai_run | Provider, model, prompt version, tokens, estimated cost, redacted context, response |
| ai_claim | Claim text, type, confidence, sources, counter-evidence |
| watchlist_item | Names being watched |
| research_note | Notes attached to an instrument |

## Real and mocked

| Piece | V1 |
| --- | --- |
| Portfolio maths, drawdown, concentration, scenarios, alerts | Real, deterministic |
| Normalisation of two different broker payload shapes | Real |
| Trading 212 and IBKR networks | Mock fixtures still feed the dashboard. A separate read-only history sync can archive a live Trading 212 account |
| Market prices and FX | Mock. FX is a constant 0.78. Weekdays, not exchange holidays |
| Claude and OpenAI adapters | Implemented. Called only when a key is set and the provider is selected |
| Default analysis | Mock. It copies calculated figures and does not invent news |
| Order entry | Not implemented |

## Trading 212 history sync

History sync stores live rows with `data_origin = LIVE`. Those rows stay out of mock snapshot rebuilds. The dashboard reads them only when `DATA_MODE=live`.

```text
.venv\Scripts\python.exe scripts/sync_trading212_history.py
```

The client is GET-only. It reads `/equity/history/orders`, `/equity/history/dividends`, `/equity/history/transactions`, and `/equity/metadata/instruments`, and follows `nextPagePath` until it is empty. A repeated or malformed next page stops the run.

Each broker row is archived in `raw_broker_event` before it is normalised. The idempotency key is `broker + broker_event_id`. Ids are `fill:{id}`, `dividend:{reference}`, and `transaction:{reference}`. If the broker does not send an id, the key is a SHA-256 of the stable source fields, prefixed `order-fp:`, `dividend-fp:`, or `transaction-fp:`. Rerunning the script does not insert that row again.

Executed `TRADE` fills become canonical `trade` rows. A `STOCK_SPLIT` fill becomes a `corporate_action` with the signed quantity delta. It is not an investor buy or sell, and it is excluded from trade counts and from cost-basis buys and sells. The split ratio is left empty when the broker does not send one. Cancelled and rejected orders stay in the raw archive. Dividends and cash movements become `cash_movement` rows. Dividends are not trades.

GBP cash is stored with `amount_gbp` set and `fx_conversion_status = NOT_REQUIRED`, including a genuine £0. A non-GBP cash amount keeps `amount_native` and `currency`. When no GBP rate was provided, `amount_gbp` is NULL and `fx_conversion_status` is `MISSING`. Historical FX rates are not guessed.

Open quantity is the sum of executed buys and sells plus corporate-action quantity deltas. Reconciliation compares that with `GET /equity/positions`. `PASS` means every open quantity matches and there are no integrity errors. `WARN` means quantities match but a non-position record failed normalisation. `FAIL` means a quantity mismatch, a missing or extra position, an unmapped instrument, or a duplicate execution. The script exits with status 2 on `FAIL`.

## Live snapshot

`DATA_MODE=mock` keeps Today, Positions, and Risk on the seeded book. `DATA_MODE=live` reads the latest `LIVE` `portfolio_snapshot` and its `position_snapshot` rows. It does not fall back to mock data. Restart Streamlit after changing `DATA_MODE`. Create or refresh the snapshot with:

```text
.venv\Scripts\python.exe scripts/create_t212_live_snapshot.py
```

That command checks configuration, fetches the account summary and positions, maps instruments, writes one `portfolio_snapshot` and its `position_snapshot` rows, and prints position reconciliation and cost-basis reconciliation. It is safe to rerun. The timestamp is the start of the current interval (`SNAPSHOT_INTERVAL_MINUTES`, default 15). A second run in that same interval does not insert another row.

Scheduled snapshots use the same rule. This project has no market-hours calendar, so the process runs on the clock while it is open, including outside exchange hours. The dashboard does not start it.

```text
.venv\Scripts\python.exe scripts/run_t212_snapshot_scheduler.py
```

No historical portfolio values are backfilled from fills. In live mode, charts use stored `LIVE` snapshots only, so history starts with the snapshots stored from now on. Mock mode still uses the seeded snapshot history.

Account total, cash, and the sum of position market values are stored as the broker sent them. A difference of up to £0.10 is `PASS` (rounding). Up to £1 is `WARN`. More than that is `FAIL`. Nothing is adjusted to force the figures to match.

Reconstructed cost basis is weighted average cost in the trade currency: buys add price times quantity plus fees, sells remove the current average times the quantity sold, and a corporate action changes quantity without changing remaining cost. That figure is not treated as Trading 212's `averagePricePaid`. The report labels them reconstructed cost basis and broker cost basis. A missing broker average is `UNAVAILABLE`. GBP cost is omitted when a fill has no FX rate.

The client remains GET-only. The snapshot scripts refuse to run when `T212_READ_ONLY` is not true.

## Limits

- The dashboard still uses mock prices, mock snapshots, and mock broker history.
- No news, filings, or a historical price feed.
- No correlation, factor model, VaR, or expected shortfall.
- The Nasdaq scenario assumes beta of 1 and says so.
- Decision edits append a journal note and overwrite the current row. They are not a full revision history.
- Seed data is a fixture. It is not a statement of what is owned.
- Estimated AI cost is not an invoice.

## Next step

The dashboard is still on the mock book. A later switch can read `current_live_snapshot` after the live snapshot and both reconciliations have been reviewed. Do not add order methods.
