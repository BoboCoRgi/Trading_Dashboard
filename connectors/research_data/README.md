# connectors/research_data

Interfaces for sources that are not connected yet.

`provider.py` defines `NewsProvider`, `FundamentalsProvider`, and `OwnershipProvider`. The `Unavailable*` classes return `ready: false` and an empty row list.

Callers must show that empty state. They must not fill missing news, filings, or ownership with a guess.
