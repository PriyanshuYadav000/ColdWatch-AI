# Application entry points

## FastAPI backend

The FastAPI application is implemented in \`src/coldwatch/api/app.py\`.

Run it against the isolated challenge database from the repository root:

\`\`\`bash
python scripts/run_api.py --database data/coldwatch-risk-engine.duckdb
\`\`\`

Open \`http://127.0.0.1:8000/docs\` for interactive endpoint documentation.

## Streamlit dashboard

The Streamlit operations dashboard is implemented in \`app/streamlit_app.py\`. It uses the FastAPI API and does not connect directly to DuckDB.

Start the dashboard in a second terminal:

\`\`\`bash
streamlit run app/streamlit_app.py
\`\`\`

Open the local URL printed by Streamlit (normally \`http://localhost:8501\`). The default backend URL is \`http://127.0.0.1:8000\`; it can be changed in the dashboard sidebar.

See [../docs/dashboard.md](../docs/dashboard.md) for feature descriptions and operating notes, and [../docs/api.md](../docs/api.md) for backend routes and request contracts.
