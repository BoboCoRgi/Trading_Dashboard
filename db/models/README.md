# db/models

SQLAlchemy models in `tables.py`. `db/models/__init__.py` exports them.

The book is `broker_account`, `instrument`, `trade`, `cash_movement`, `portfolio_snapshot`, and `position_snapshot`. Research memory is `thesis`, `catalyst`, `decision`, `journal_entry`, and `decision_outcome`. Market context is `price_observation`, `market_price_bar`, `benchmark_mapping`, market events, and opportunity candidates. AI audit is `ai_run` and `ai_claim`.

`market_price_bar.volume` is the volume of that bar. It is not a daily total and not a cumulative total. `benchmark_mapping` is an explicit symbol pair. An empty mapping is not guessed.

Corporate actions are stored separately from investor `BUY` and `SELL` trades.
