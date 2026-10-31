# ColdWatch REST API

## Overview

The FastAPI application exposes warehouse/zone telemetry, alarms, inventory-impact links, inspections, and remediation workflows. Read endpoints open DuckDB in read-only mode. Mutation endpoints delegate to the transactional workflow services; they do not implement an alternate state machine.

The API does not expose or read the separate ground-truth CSV. It serves operational records only.

## Run locally

Install the project and development/test dependencies:

```bash
python -m pip install -e ".[dev]"
```

Launch the API against the isolated risk-engine database:

```bash
python scripts/run_api.py --database data/coldwatch-risk-engine.duckdb
```

The default address is `http://127.0.0.1:8000`. Interactive OpenAPI documentation is available at `/docs`; the raw OpenAPI schema is at `/openapi.json`.

Custom host/port and auto-reload are supported:

```bash
python scripts/run_api.py \
  --database data/coldwatch-risk-engine.duckdb \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

The database can also be selected using `COLDWATCH_DB_PATH` when importing `coldwatch.api.app:app` with Uvicorn. The application factory `create_app(database_path)` supports isolated test databases.

## Routes

System:
- `GET /health` — database reachability and table count.
- `GET /api/v1/summary` — master-data, telemetry, alarm, inspection, and remediation counts; alarm severity/status and impact-status distributions.

Locations and telemetry:
- `GET /api/v1/warehouses` — warehouses with zone and active sensor counts.
- `GET /api/v1/zones` — zones with optional `warehouse_id` and `status` filters, active sensor count, and current lot count.
- `GET /api/v1/zones/{zone_id}/readings` — effective-dated sensor readings by zone, optionally filtered by `sensor_type`, `from_at`, `to_at`, and `limit`.

Alarms and impacts:
- `GET /api/v1/alarms` — paginated alarm list; optional `status`, `severity`, and `alarm_type` filters.
- `GET /api/v1/alarms/{alarm_id}` — alarm, evidence, inventory impacts, and status history.
- `GET /api/v1/alarms/{alarm_id}/impacts` — impacted lots with product and zone context.
- `POST /api/v1/alarms/{alarm_id}/transitions` — change alarm status and optionally assign an owner.

Inspections:
- `GET /api/v1/inspections` — paginated inspection list; optional `status` and `alarm_id` filters.
- `GET /api/v1/inspections/{inspection_id}` — inspection detail and status history.
- `POST /api/v1/alarms/{alarm_id}/inspections` — create an inspection.
- `POST /api/v1/inspections/{inspection_id}/transitions` — start, complete, or cancel an inspection.

Remediations:
- `GET /api/v1/remediations` — paginated remediation list; optional `status` and `inspection_id` filters.
- `GET /api/v1/remediations/{remediation_id}` — remediation details, status history, and verification history.
- `POST /api/v1/inspections/{inspection_id}/remediations` — create a corrective action from a completed inspection.
- `POST /api/v1/remediations/{remediation_id}/transitions` — start, complete, or cancel remediation.
- `POST /api/v1/remediations/{remediation_id}/verification` — record independent verification.

## Example requests

List high-severity open alarms:

```bash
curl "http://127.0.0.1:8000/api/v1/alarms?status=OPEN&severity=HIGH"
```

Get alarm evidence and impact details:

```bash
curl "http://127.0.0.1:8000/api/v1/alarms/ALARM_ID"
curl "http://127.0.0.1:8000/api/v1/alarms/ALARM_ID/impacts"
```

Acknowledge an alarm:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/alarms/ALARM_ID/transitions" \
  -H "Content-Type: application/json" \
  -d '{
    "new_status": "ACKNOWLEDGED",
    "changed_by": "warehouse.operator",
    "reason": "Reviewed the alert and assigned an owner.",
    "assigned_to": "quality.lead"
  }'
```

Create an inspection:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/alarms/ALARM_ID/inspections" \
  -H "Content-Type: application/json" \
  -d '{
    "inspection_type": "PRODUCT_QUALITY",
    "requested_by": "quality.coordinator",
    "lot_id": "LOT_ID",
    "inspector": "quality.inspector"
  }'
```

## Responses and errors

- Request bodies use Pydantic models; unknown fields are rejected and enum values are validated.
- List endpoints return `items`, `total`, `limit`, and `offset` where pagination applies.
- Invalid workflow transitions return HTTP 409.
- Missing operational records return HTTP 404.
- Invalid request bodies or query parameters return HTTP 422.
- Missing/unreadable database returns HTTP 503.
- All workflow changes call the service layer and append status/verification history transactionally.

## Scope and limitations

This is a local challenge prototype, not a production-hardened public API. It currently has no authentication/authorization, role-based access, rate limiting, or TLS termination. Do not expose it to an untrusted network or use it with real warehouse/customer data without adding those controls. The API is synchronous and uses a local DuckDB file. Product disposition remains a human decision; the API records an explicit decision rather than inferring damage from an alarm.
