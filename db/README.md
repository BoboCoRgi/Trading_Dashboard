# db

PostgreSQL access.

| Path | Role |
| --- | --- |
| `base.py` | SQLAlchemy base |
| `session.py` | Engine, `SessionLocal`, and `session_scope` |
| `models/` | Tables |
| `migrations/` | Alembic revisions |
| `repositories/` | Small read helpers re-exported for services |

The default local URL is `postgresql+psycopg://invest:invest@localhost:5432/investment_intelligence`. That password is the docker-compose default for this machine, not a production secret.

Almost every table carries `data_origin` so `MOCK` and `LIVE` rows stay apart.
