# Streamlit operations dashboard

## Architecture

The Streamlit frontend in \`app/streamlit_app.py\` consumes the FastAPI REST API. It does not read DuckDB directly. Alarm, inspection, remediation, and verification changes stay inside the transactional workflow services.

\`\`\`text
Streamlit dashboard
        | HTTP GET / POST
        v
FastAPI application
        +---- read-only DuckDB queries
        +---- transactional workflow services
                         |
                         v
                    DuckDB database
\`\`\`

## Run

Start the API in one terminal:

\`\`\`bash
python scripts/run_api.py --database data/coldwatch-risk-engine.duckdb
\`\`\`

Start the dashboard in another terminal:

\`\`\`bash
streamlit run app/streamlit_app.py
\`\`\`

Open the local URL printed by Streamlit (normally \`http://localhost:8501\`). The default API URL is \`http://127.0.0.1:8000\`.

## Features

- KPI overview and alarm severity, status, and inventory-impact distributions.
- Alarm filtering, evidence and impacted-lot review, state transitions, and inspection creation.
- Warehouse/zone browsing and recent temperature, humidity, and occupancy charts.
- Inspection/remediation lifecycle actions and independent verification.
- Fifteen-second read caching, manual refresh, and cache invalidation after successful mutations.

## Boundaries

The dashboard uses operational API records only and never reads the separate ground-truth evaluation CSV. It does not infer product damage; inspection disposition remains a human decision. The backend validates every state transition and records append-only history.

The API and dashboard are local challenge prototypes. Authentication/authorization, role-based access, rate limiting, and TLS are not implemented. Do not expose them to an untrusted network or connect real operational/customer data.
