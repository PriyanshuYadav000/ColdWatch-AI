# Hosted preview deployment

ColdWatch can be previewed with two services:

- **FastAPI backend:** Render Web Service, connected to the public GitHub repository.
- **Streamlit frontend:** Streamlit Community Cloud, connected to the same repository.

The Streamlit app must not use `127.0.0.1:8000` for a hosted backend. In Community Cloud, loopback refers to the Streamlit container, not the separate Render service.

This deployment is for a public synthetic-data demo only. Do not connect real warehouse or customer data.

## 1. Deploy the FastAPI backend on Render

The repository root contains a `render.yaml` Blueprint.

1. Push the latest `main` branch to GitHub.
2. Sign in to [Render](https://dashboard.render.com/).
3. Choose **New → Blueprint**.
4. Connect the public repository `PriyanshuYadav000/ColdWatch-AI`.
5. Select the `main` branch and apply the Blueprint.
6. Review the service named `coldwatch-api` and deploy it.

The Blueprint configures:

| Setting | Value |
|---|---|
| Runtime | Python 3.12.11 |
| Plan | Free preview |
| Build command | Install the project in editable mode and create the synthetic starter database |
| Start command | Start FastAPI on `0.0.0.0:$PORT` |
| Health check | `/health` |
| Database | `.runtime/coldwatch-api.duckdb` |
| Dataset | Deterministic `starter` profile |
| Workflow writes | Protected by a generated shared API token |

The project is installed with `pip install -e .` rather than as a regular wheel. The generators resolve configuration and runtime folders (`config/`, `data/`, and `reports/`) relative to the checked-out source tree, so editable installation keeps those paths anchored to the repository on Render.\n\nThe bootstrap script runs the same starter pipeline as a local reviewer: initialize schema, generate master data, generate baseline telemetry, inject scenarios, validate the dataset, and persist risk-engine findings. It runs during the Render build so the API can bind its port immediately at runtime.

Render generates `COLDWATCH_API_WRITE_TOKEN` for the service. Open the service's **Environment** page and copy the generated value. Do not put it in GitHub or in a source file.

When the service is live, copy its public URL, for example:

```text
https://coldwatch-api-<your-suffix>.onrender.com
```

Check the health route in a browser:

```text
https://<your-render-service>.onrender.com/health
```

The response should contain `"status":"ok"`, `"database_readable":true`, and the available table count.

## 2. Configure Streamlit Community Cloud

Deploy the frontend from the same GitHub repository using:

| Field | Value |
|---|---|
| Repository | `PriyanshuYadav000/ColdWatch-AI` |
| Branch | `main` |
| Main file path | `app/streamlit_app.py` |
| Python version | `3.12` |

In **Advanced settings → Secrets**, add the following TOML, replacing both placeholder values:

```toml
COLDWATCH_API_URL = "https://<your-render-service>.onrender.com"
COLDWATCH_API_WRITE_TOKEN = "<paste-the-generated-Render-token>"
```

Save the secrets and reboot/redeploy the Streamlit app. The dashboard reads the API URL and token from `st.secrets`. The token is attached only to workflow mutation requests (POST/PUT/PATCH/DELETE); read-only GET requests do not need it.

If testing locally with a hosted backend, you can set the same values as shell environment variables:

```bash
export COLDWATCH_API_URL="https://<your-render-service>.onrender.com"
export COLDWATCH_API_WRITE_TOKEN="<generated-token>"
streamlit run app/streamlit_app.py
```

For the ordinary local application, leave these variables unset and the dashboard defaults to `http://127.0.0.1:8000`.

## 3. Verify the hosted flow

1. Open the Streamlit Community Cloud URL.
2. Confirm the dashboard shows **API connected**.
3. Check the warehouse/zone summary and alarm list.
4. Open an alarm and review its evidence and inventory impacts.
5. Try a workflow action. It should succeed when the Streamlit secret contains the correct token.
6. Open the Render `/health` route independently if the dashboard cannot connect.

If GET routes work but workflow actions return HTTP 401, check that `COLDWATCH_API_WRITE_TOKEN` in Streamlit Secrets exactly matches the generated Render environment value. After editing secrets, reboot the Streamlit app.

## 4. Free-hosting behavior and limitations

- Render Free Web Services spin down after inactivity and can take time to wake on the next request.
- The free service uses an ephemeral filesystem. The synthetic starter database is included in the build output; workflow changes made at runtime can be lost when the instance is recycled or redeployed.
- The demo uses synthetic starter data, not the one-million-reading full challenge dataset.
- GET endpoints expose synthetic demo data publicly. Workflow mutation endpoints require the shared `X-API-Token`.
- The shared token is a demo safeguard, not user authentication, role-based access control, or production authorization.
- Do not expose real inventory, facility, personnel, or customer data through this preview.
- No production guarantee is made for availability, persistence, throughput, or security.

Render documents free-service idle spin-down and ephemeral filesystem limitations in its [free instance documentation](https://render.com/docs/free) and [web-service documentation](https://render.com/docs/web-services). Streamlit documents deployment Secrets in its [Community Cloud secrets guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).
