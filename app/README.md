# Application entry points

The FastAPI application is implemented in `src/coldwatch/api/app.py`.

Run it against the isolated challenge database from the repository root:

```bash
python scripts/run_api.py --database data/coldwatch-risk-engine.duckdb
```

Open `http://127.0.0.1:8000/docs` for interactive endpoint documentation.

The Streamlit dashboard entry point will be added in the next application milestone. See [docs/api.md](../docs/api.md) for available routes and example requests.
