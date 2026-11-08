# analytics

Deterministic portfolio and market maths. These functions do not call a broker, a market vendor, or a language model.

Money and quantities are `Decimal`. Weights and returns are fractions. Drawdown is zero or negative.

| Module | Calculates |
| --- | --- |
| `portfolio.py`, `performance.py` | Value and return |
| `drawdown.py`, `concentration.py`, `exposure.py` | Risk aggregates |
| `cost_basis.py` | Weighted-average cost from fills |
| `holdings.py` | Same-symbol lots across accounts |
| `market_stats.py` | Daily relative volume, intraday relative volume, VWAP |
| `events.py`, `screens.py`, `alerts.py` | Move context, screens, alert thresholds |
| `scenarios.py`, `outcomes.py`, `changes.py` | What-if impact and later outcome windows |
| `snapshot_integrity.py` | Snapshot consistency checks |

Relative volume needs twenty prior sessions. VWAP uses intraday high, low, close, and bar volume. A missing input stays missing.
