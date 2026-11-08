# core

Shared types used by connectors, analytics, services, and the database. This package does not import those layers.

| Module | Role |
| --- | --- |
| `enums.py` | Brokers, data origin, trade actions, thesis and decision states |
| `money.py` | `Decimal` helpers. Floats are rejected |
| `config.py` | Settings from the environment and `.env` |
| `contracts.py` | Broker-agnostic drafts |
| `logging.py` | Logging setup. Secrets are not logged |

`DATA_MODE` is `mock` or `live`. Any other value does not fall back to the mock book.
