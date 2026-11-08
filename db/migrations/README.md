# db/migrations

Alembic environment. `env.py` uses the application metadata and `DATABASE_URL`.

Apply the chain from the repository root:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`0001_initial` creates the model schema. Later revisions add columns and tables for databases that already exist. A fresh database and an upgraded database are meant to end on the same shape.
