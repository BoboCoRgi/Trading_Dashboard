# app/dashboard/views

One render function per page. These are registered from `streamlit_app.py`. This folder is not a Streamlit `pages/` directory.

| Module | Page |
| --- | --- |
| `today.py` | What needs attention |
| `holdings.py` | Combined book and stock detail |
| `decisions.py` | Decision ledger |
| `risk_lab.py` | Concentration, drawdown, scenarios |
| `research_lab.py` | Thesis, notes, watchlist |
| `ai_review.py` | Stored analysis runs |

Holdings uses the latest snapshot for the selected data mode. A missing price history, volume series, or benchmark mapping is an empty state.
