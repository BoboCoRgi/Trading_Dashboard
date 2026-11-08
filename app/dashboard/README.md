# app/dashboard

Streamlit research terminal. `streamlit_app.py` registers the pages. Styling lives in `theme.py` and `ui.py`.

| Module | Role |
| --- | --- |
| `views/` | One module per page |
| `charts.py` | Plotly figures, including the price and volume chart |
| `components.py` | Headers, tables, empty states, source banner |
| `series.py` | Snapshot history for the existing charts |
| `present.py` | Display formatting |
| `event_panel.py` | Market-event presentation |

The pages read stored snapshots through `services`. They do not call Trading 212 during a page render. `DATA_MODE` chooses mock or live rows, and the banner says which one is showing.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/dashboard/streamlit_app.py --server.port 8501 --server.address 127.0.0.1
```
