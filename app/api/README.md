# app/api

FastAPI surface in `main.py`.

It exposes health, the today and risk views, decisions, research, and a manual analysis trigger. Request bodies do not accept broker credentials. Analysis of an external model still requires `allow_external`.

Run it with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload
```
