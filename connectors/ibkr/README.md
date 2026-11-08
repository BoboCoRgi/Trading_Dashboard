# connectors/ibkr

Read-only IBKR adapter.

`connector.py` implements the same read contract as Trading 212. `normalise.py` maps an IBKR payload into the canonical account, position, and fill shape.

No IBKR positions are fabricated. Rows appear in Holdings only after a real IBKR snapshot is stored with its own account.
