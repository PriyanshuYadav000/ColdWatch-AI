# ColdWatch-AI
### Warehouse Environmental Monitoring & Risk Intelligence Platform

ColdWatch-AI is an end-to-end warehouse monitoring and incident-management prototype. It models warehouse infrastructure, sensors, environmental telemetry, inventory lots, and storage requirements; detects environmental and operational risks; identifies inventory exposed during the risk window; and gives operators a traceable workflow for investigation, inspection, remediation, verification, and closure.

The central design principle is **evidence before action**. A sensor reading or door event is not treated as an isolated alert: the system evaluates it against configured requirements and effective-dated warehouse relationships, links the finding to its supporting source records, and resolves potentially affected inventory using both zone and time overlap. Product disposition remains a human decision.

The project is designed to be reproducible and reviewable. It uses deterministic synthetic data, a separate ground-truth evaluation dataset, a transparent Python rule engine, a DuckDB relational store, a FastAPI backend, and a Streamlit operations dashboard. No external LLM or hosted AI service is required to run the core application.

---

## Contents

1. [At a glance](#at-a-glance)
2. [What the system does](#what-the-system-does)
3. [Architecture and data flow](#architecture-and-data-flow)
4. [Technology stack](#technology-stack)
5. [Run the project locally](#run-the-project-locally)
6. [Test and validate the project](#test-and-validate-the-project)
7. [Hosted preview deployment (optional)](#hosted-preview-deployment-optional)
8. [Reviewer walkthrough: how to use it](#reviewer-walkthrough-how-to-use-it)
9. [Risk scenarios and detection rules](#risk-scenarios-and-detection-rules)
10. [Operational workflow and state transitions](#operational-workflow-and-state-transitions)
11. [Data model](#data-model)
12. [API reference](#api-reference)
13. [Generated files and exports](#generated-files-and-exports)
14. [Configuration and reproducibility](#configuration-and-reproducibility)
15. [Run the full challenge-scale profile](#run-the-full-challenge-scale-profile)
16. [Repository structure](#repository-structure)
17. [Troubleshooting](#troubleshooting)
18. [Security, assumptions, and limitations](#security-assumptions-and-limitations)
19. [Verification record](#verification-record)
20. [Project documentation](#project-documentation)

---

## At a glance

| Area | Implementation |
|---|---|
| Application style | Modular Python application with REST API and web dashboard |
| Primary database | DuckDB |
| Analytical file format | Parquet |
| Data processing | Python, Pandas, NumPy, PyArrow |
| Backend | FastAPI and Pydantic |
| Frontend | Streamlit |
| Risk detection | Deterministic, explainable Python rules |
| Evaluation | Separate ground truth and independent precision/recall/F1 evaluator |
| Testing | Pytest |
| Runtime | Python 3.12 |

### Challenge-scale dataset

The full synthetic profile has been generated and locally validated with the following volumes:

| Entity / artifact | Full profile |
|---|---:|
| Warehouses | 10 |
| Zones | 100 |
| Sensors | 500 |
| Sensor readings | 1,000,000 |
| Inventory lots | 10,000 |
| Baseline door-event records | 2,800 |
| Controlled risk scenarios | 9 |

The default starter profile is intentionally smaller so a reviewer can run the complete pipeline on a laptop before generating the full dataset.

---

## What the system does

ColdWatch-AI covers the path from synthetic source data to an operator action:

1. **Model the warehouse.** Create warehouses, zones, sensors, storage requirements, inventory lots, and effective-dated sensor/lot-to-zone assignments.
2. **Generate baseline data.** Create normal sensor telemetry and paired door-open/door-close events using a fixed random seed. Sensor readings are written in bounded batches and stored as Parquet as well as loaded into DuckDB.
3. **Inject controlled exceptions.** Add nine deterministic anomaly scenarios to the operational dataset. Evaluation labels are written separately and are not detector input.
4. **Validate data integrity.** Check generated artifacts, counts, identifiers, relationships, timestamps, cadence, assignment overlaps, anomaly evidence, and audit records.
5. **Detect risks.** Run transparent rules over the operational records and configured storage requirements.
6. **Resolve inventory exposure.** Match a finding's zone and event interval against effective-dated lot placements. Temperature and humidity findings are linked to lots whose applicable requirements were breached; other zone risks consider all lots overlapping the relevant zone/time interval.
7. **Persist evidence and alarms.** Store the finding, source evidence, inventory-impact links, and stable identifiers transactionally.
8. **Manage the incident.** Operators acknowledge and investigate alarms, create inspections, record findings and product disposition, create remediation actions, and record independent verification.
9. **Review the operation.** Use the Streamlit dashboard or REST API to inspect KPIs, alarms, telemetry, evidence, impacted lots, workflow status, and history.
10. **Evaluate independently.** Compare the rule engine's output with the isolated ground-truth labels after detection is complete.

The risk engine does not read the ground-truth directory, anomaly manifest, or evaluation CSV. This separation prevents expected labels from leaking into operational detection.

---

## Architecture and data flow

### High-level architecture

~~~mermaid
flowchart TD
    A[Configuration and fixed seed] --> B[Master-data generator]
    B --> C[Telemetry generator]
    C --> D[Baseline DuckDB and Parquet]
    D --> E[Anomaly injector]
    E --> F[Operational source records]
    E --> G[Separate ground-truth labels]
    F --> H[Dataset validator]
    F --> I[Deterministic risk engine]
    I --> J[Findings and source evidence]
    J --> K[Zone and time inventory attribution]
    K --> L[DuckDB alarms and impact records]
    L --> M[FastAPI]
    M --> N[Streamlit operations dashboard]
    L --> O[Independent evaluation]
    G --> O
~~~

### Runtime request flow

~~~text
Reviewer / Operator
       |
       v
Streamlit dashboard (localhost:8501)
       |
       | HTTP requests
       v
FastAPI backend (127.0.0.1:8000)
       |
       +---- Read-only operational queries
       |
       +---- Transactional workflow services
       |
       v
DuckDB database
       |
       +---- Warehouse and zone master data
       +---- Sensor readings and door events
       +---- Inventory and effective-dated assignments
       +---- Alarms, evidence, and inventory impacts
       +---- Inspection, remediation, and history records
~~~

The dashboard communicates through the API; it does not open DuckDB directly. API read routes use read-only database connections. Workflow mutations go through service functions that validate state transitions and append history records transactionally.

### Key engineering decisions

- **Deterministic rules for operational decisions:** anomaly decisions are explicit and inspectable. No LLM is involved in the core risk engine.
- **DuckDB for local analytical workloads:** the application can run without provisioning a database server.
- **Parquet for telemetry artifacts:** baseline readings are generated in bounded batches and stored in a columnar format.
- **Effective-dated assignments:** historical sensor mappings and lot placements can be resolved at the time of an event instead of assuming current placement was always true.
- **Ground-truth isolation:** expected labels are only used by the evaluator after the risk engine has produced its findings.
- **Human-controlled disposition:** an alarm does not automatically declare goods damaged, released, or rejected.
- **Append-only workflow history:** status changes and verification actions remain traceable.

---

## Technology stack

| Technology | Purpose |
|---|---|
| Python 3.12 | Application and generator runtime |
| DuckDB | Relational storage, analytical joins, and operational state |
| NumPy | Seeded numerical signal generation |
| Pandas | Data transformation and reporting |
| PyArrow | Bounded Parquet generation and processing |
| Parquet / CSV / JSON | Data artifacts, labels, manifests, and reports |
| FastAPI | REST API |
| Pydantic | Request validation and typed API models |
| Uvicorn | ASGI server |
| Streamlit | Operations dashboard |
| Requests | Dashboard-to-API communication |
| Pytest / HTTPX | Automated testing and API tests |

---

## Run the project locally

This is the recommended first-run path for a reviewer. It creates a small, complete starter dataset, detects the injected risks, and runs the API and dashboard against **one database file**.

### 1. Prerequisites

Install the following:

- Git
- Python **3.12.x** (the project requires Python 3.12 and currently targets versions below 3.13)
- A terminal
- macOS, Linux, or Windows

On macOS with Homebrew:

~~~bash
brew install python@3.12
~~~

Check the installed version:

~~~bash
python3.12 --version
~~~

It should report Python 3.12.x.

### 2. Clone the repository

~~~bash
git clone https://github.com/PriyanshuYadav000/ColdWatch-AI.git
cd ColdWatch-AI
~~~

### 3. Create and activate a virtual environment

macOS / Linux:

~~~bash
python3.12 -m venv .venv
source .venv/bin/activate
~~~

Windows PowerShell:

~~~powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
~~~

Use the virtual environment for all remaining commands. Your terminal prompt should show the active environment.

### 4. Install dependencies

~~~bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
~~~

The editable installation makes the ColdWatch Python package importable while you work in the repository. The optional development dependencies include Pytest and HTTPX.

Check the environment:

~~~bash
python scripts/check_environment.py
~~~

### 5. Create a fresh database

For this walkthrough, use a dedicated database named **data/coldwatch-reviewer.duckdb**. The commands below pass this same path to every stage.

~~~bash
mkdir -p data
export DB_PATH="data/coldwatch-reviewer.duckdb"

python scripts/init_db.py --database "$DB_PATH"
~~~

On Windows PowerShell, set the variable with:

~~~powershell
$env:DB_PATH = "data/coldwatch-reviewer.duckdb"
python scripts/init_db.py --database $env:DB_PATH
~~~

The initializer creates the DuckDB tables from **schemas/schema.sql**. It does not generate data.

### 6. Generate warehouse and inventory master data

~~~bash
python scripts/generate_master_data.py \
  --profile starter \
  --database "$DB_PATH"
~~~

This creates the warehouse, zone, sensor, storage-requirement, inventory-lot, and effective-dated assignment records for the starter profile.

### 7. Generate baseline sensor readings and door events

~~~bash
python scripts/generate_telemetry.py \
  --profile starter \
  --database "$DB_PATH" \
  --output-root data/generated
~~~

The generator writes the baseline telemetry artifacts under **data/generated/starter/telemetry/** and loads the generated records into DuckDB. The normal baseline is generated before any controlled anomaly is injected.

### 8. Inject the nine controlled scenarios

~~~bash
python scripts/inject_anomalies.py \
  --profile starter \
  --database "$DB_PATH" \
  --ground-truth-root data/ground_truth \
  --report-root reports
~~~

This step adds controlled anomaly evidence to the operational database and writes the expected scenario labels separately under **data/ground_truth/starter/**. Do not run this step a second time against the same database. For a clean repeat, initialize a new database path and run the pipeline from the beginning.

### 9. Validate the completed dataset

~~~bash
python scripts/validate_dataset.py \
  --profile starter \
  --database "$DB_PATH" \
  --generated-root data/generated \
  --ground-truth-root data/ground_truth \
  --report-root reports
~~~

The validator writes a JSON report under **reports/** and returns a non-zero exit code if required validation checks fail. Continue only after the report status is PASS.

### 10. Preview risk findings, then persist alarms

First run a dry-run preview. This writes a JSON report but does not persist alarms or evidence:

~~~bash
python scripts/run_risk_engine.py \
  --profile starter \
  --database "$DB_PATH" \
  --report-root reports/risk-preview \
  --dry-run
~~~

After reviewing the findings, run the engine without the dry-run flag to persist alarms, evidence, and inventory impacts:

~~~bash
python scripts/run_risk_engine.py \
  --profile starter \
  --database "$DB_PATH" \
  --report-root reports/risk-engine
~~~

The risk engine uses the operational records only. The API and dashboard must use this same database path to display the persisted findings.

### 11. Start the FastAPI backend

Open **Terminal 1** in the repository, activate the virtual environment, and run:

~~~bash
source .venv/bin/activate
python scripts/run_api.py \
  --database data/coldwatch-reviewer.duckdb \
  --host 127.0.0.1 \
  --port 8000
~~~

The API is now available at:

- API base URL: http://127.0.0.1:8000
- Interactive Swagger documentation: http://127.0.0.1:8000/docs
- OpenAPI schema: http://127.0.0.1:8000/openapi.json
- Health check: http://127.0.0.1:8000/health

In a third terminal, check the health route:

~~~bash
curl http://127.0.0.1:8000/health
~~~

The response should report a healthy status, a readable database, and the available table count.

### 12. Start the Streamlit dashboard

Keep the API running. Open **Terminal 2**, activate the same virtual environment, and run:

~~~bash
source .venv/bin/activate
streamlit run app/streamlit_app.py
~~~

Open the local URL printed in the terminal, normally:

http://localhost:8501

The dashboard's default API address is http://127.0.0.1:8000. Keep the API running while using the dashboard.

### Local startup summary

After the data pipeline has completed, the normal day-to-day startup is just two commands in two terminals:

**Terminal 1 — backend**

~~~bash
source .venv/bin/activate
python scripts/run_api.py --database data/coldwatch-reviewer.duckdb
~~~

**Terminal 2 — dashboard**

~~~bash
source .venv/bin/activate
streamlit run app/streamlit_app.py
~~~

You do not need to regenerate data every time you open the dashboard. Reuse the database created during the initial setup.

---

## Test and validate the project

### Run the automated test suite

~~~bash
pytest -q
~~~

Run a specific test module:

~~~bash
pytest -q tests/test_schema.py
pytest -q tests/test_risk_engine.py
pytest -q tests/test_api.py
~~~

The tests cover schema constraints, deterministic generation, data validation, risk detection, affected-inventory attribution, API behavior, workflow transitions, and seed export/restore behavior.

### Evaluate risk findings against ground truth

The evaluator is independent of the risk engine. It reads an already-generated risk report and then compares it with the separate label CSV.

~~~bash
python scripts/evaluate_risk_engine.py \
  --profile starter \
  --risk-report reports/risk-preview/risk_engine_starter.json \
  --ground-truth data/ground_truth/starter/ground_truth.csv \
  --output reports/risk-preview/evaluation_starter.json
~~~

The report includes:

- Matched scenarios, unmatched findings, and missed scenarios
- True-positive, false-positive, and false-negative counts
- Precision, recall, and F1
- Severity agreement
- Affected-lot association metrics
- Per-issue-type evaluation details

The labelled synthetic set does not define a true-negative population. For that reason, the evaluator does not claim accuracy or specificity.

---

## Hosted preview deployment (optional)

The dashboard and API run in separate environments when hosted. The Streamlit Community Cloud frontend cannot reach a FastAPI process running at `127.0.0.1` on a developer's laptop.

For an optional public synthetic-data preview, deploy:
- FastAPI backend as a Render Web Service using the repository's `render.yaml` Blueprint.
- Streamlit frontend on Streamlit Community Cloud using `app/streamlit_app.py`.

The hosted dashboard reads `COLDWATCH_API_URL` from Streamlit Secrets and sends `COLDWATCH_API_WRITE_TOKEN` for workflow mutation requests. Render generates the shared write token. Read routes serve synthetic data; mutation routes require the token when it is configured.

**Deployment guide:** [docs/deployment.md](docs/deployment.md). This preview uses the starter dataset and an ephemeral filesystem; workflow changes can reset when the free service is recycled. It is not production-hardened and must not be used with real warehouse/customer data.

---

## Reviewer walkthrough: how to use it

Once the API and dashboard are running, this is the recommended path through the application.

### 1. Start with the overview

Open the dashboard and review the overview KPIs and distributions. The dashboard summarizes warehouse/zone coverage, sensors and readings, alarm severities and statuses, and inventory-impact status.

### 2. Open the alarm center

Navigate to the alarm view and filter by status, severity, or alarm type. Open an alarm to inspect:

- The detected issue and severity
- The affected warehouse and zone
- Detection and event timestamps
- Supporting source evidence
- Impacted inventory lots
- Current status and status history

An alarm is a finding that needs investigation. It is not, by itself, proof that a product has been damaged.

### 3. Review the evidence and impacted inventory

Use the alarm detail and impact views to inspect the linked sensor readings or door events and the lots whose assignments overlapped the relevant zone and event window.

The inventory resolver uses effective-dated lot placement. A lot that is currently in a zone is not automatically assumed to have been there at the historical time of an earlier alarm.

### 4. Acknowledge and investigate an alarm

Use the alarm actions to assign an owner and move the alarm through its permitted lifecycle. Add an operator name and a reason for each transition. The API validates transitions and appends a history record.

### 5. Create and complete an inspection

Create an inspection from an alarm. Start the inspection, record its findings, and select an explicit product disposition where applicable (for example, QUARANTINED, RELEASED, REJECTED, or RETEST_REQUIRED).

The system records the decision; it does not infer the disposition from a sensor event.

### 6. Create and verify a remediation

Create a remediation action from a completed inspection. Move the action through its lifecycle, then record an independent verification result. The verification history is stored separately from the remediation status history.

### 7. Close the alarm

Resolve and close the alarm after the required impact assessment and workflow work have been completed. An alarm cannot be resolved while its inventory-impact assessment is incomplete.

### 8. Inspect warehouse telemetry

Use the warehouse monitor to browse zones and view temperature, humidity, and occupancy readings over time. The dashboard supports manual refresh and short-lived read caching.

### 9. Review workflow history

Use the inspection and remediation views to see outstanding tasks, completed actions, and verification outcomes. The dashboard is intended to make the incident lifecycle reviewable, not to replace physical warehouse procedures.

---

## Risk scenarios and detection rules

The current controlled dataset covers nine environmental, operational, sensor-health, mapping, and inventory-integrity cases.

| Scenario | What the engine checks |
|---|---|
| Temperature excursion | Temperature readings against the applicable lot-specific storage minimum and maximum |
| Humidity excursion | Humidity readings against the applicable lot-specific storage minimum and maximum |
| Occupancy limit | Occupancy percentage against the zone's configured warning threshold |
| Door open too long | Duration between an OPEN event and the next CLOSE event |
| Sensor outage | Explicit missing-reading gap/tombstone records and configured consecutive missing intervals |
| Sensor drift | A temperature sensor's readings relative to peer sensors in the same zone over the configured observation window |
| Incompatible lot placement | Whether an inventory lot is assigned to a zone type that is not allowed by its storage requirement |
| Invalid overlapping sensor mapping | Whether a sensor has unapproved or unexplained overlapping assignments to different zones |
| Incomplete inventory-impact assessment | Whether an alarm is missing affected-lot links despite overlapping inventory in its risk zone/time window |

### Inventory attribution rules

- Findings with a clear zone and time interval are matched to effective-dated lot placements using half-open interval overlap.
- Temperature and humidity findings link lots whose applicable storage requirements were breached.
- Occupancy, door, and outage findings link candidate lots overlapping the affected zone and risk interval.
- If sensor-to-zone mapping is ambiguous because of an unapproved overlap, the engine does not guess which zone's inventory should be attributed. It reports the mapping issue separately.
- The negative-control incomplete-impact alarm is detected but is not automatically repaired.

Rule thresholds are configurable and illustrative. They are not regulatory limits or universal food, pharmaceutical, or cold-chain requirements.

---

## Operational workflow and state transitions

ColdWatch separates detection from operator workflow. The risk engine creates findings and alarms; inspections and remediations are explicit human-triggered records.

### Alarm lifecycle

~~~text
OPEN
  |
  v
ACKNOWLEDGED
  |
  v
INVESTIGATING
  |
  v
RESOLVED
  |
  v
CLOSED
~~~

The service layer also supports valid transitions back to investigation where defined by the state machine. Every transition records who changed the status, when it changed, and the reason.

### Inspection lifecycle

~~~text
PENDING --> IN_PROGRESS --> COMPLETED
     \                       ^
      \--> CANCELLED         |
~~~

Inspection completion records findings and an explicit product disposition where relevant.

### Remediation lifecycle

~~~text
PLANNED --> IN_PROGRESS --> COMPLETED
    \                         |
     \--> CANCELLED           v
                      Independent verification
                      PENDING / PASSED / FAILED
~~~

A remediation is created from a completed inspection. Verification is recorded separately so the action being marked complete is not confused with proof that the result was independently checked.

### Workflow integrity

- Invalid state transitions are rejected by the service layer.
- Each mutation is performed transactionally.
- Status history is append-only.
- An alarm cannot be resolved while the inventory-impact assessment is incomplete.
- Inspection completion requires findings and an explicit disposition.
- Remediation is linked to a completed inspection.
- Verification is persisted in its own history table.

---

## Data model

The relational schema includes all ten core challenge entities plus supporting bridge, evidence, audit, and history tables.

| Core entity | Purpose |
|---|---|
| Warehouse | Physical warehouse/site and its location metadata |
| Zone | A storage or operational area within a warehouse |
| Sensor | Temperature, humidity, or occupancy sensor metadata |
| SensorReading | Timestamped sensor measurement with value, unit, and quality metadata |
| DoorEvent | Door OPEN/CLOSE event records |
| InventoryLot | Product lot, quantity, and product metadata |
| StorageRequirement | Applicable storage constraints for a product or lot |
| Alarm | A detected operational risk and its current state |
| Inspection | An operator-created inspection linked to an alarm |
| Remediation | A corrective action linked to a completed inspection |

### Supporting relationships and audit tables

Supporting tables implement:

- Sensor-to-zone assignments with effective start/end timestamps
- Lot-to-zone assignments with effective storage start/end timestamps
- Storage-requirement-to-allowed-zone-type relationships
- Sensor health events and explicit reading-gap records
- Alarm source evidence
- Alarm-to-inventory impact links
- Reading revision and gap audit records
- Alarm status history
- Inspection status history
- Remediation status history
- Remediation verification history

See **docs/erd.md**, **docs/data_dictionary.md**, and **schemas/schema.sql** for the complete relationship diagram, field definitions, constraints, and SQL schema.

---

## API reference

The FastAPI backend exposes operational data and workflow actions. The live Swagger UI is the most convenient way to inspect request and response schemas:

http://127.0.0.1:8000/docs

### Main routes

| Area | Method | Route | Purpose |
|---|---|---|---|
| System | GET | /health | Database connectivity and table availability |
| System | GET | /api/v1/summary | Dashboard counts and status distributions |
| Locations | GET | /api/v1/warehouses | Warehouse list and zone/sensor counts |
| Locations | GET | /api/v1/zones | Zone list and optional filters |
| Telemetry | GET | /api/v1/zones/{zone_id}/readings | Zone readings with sensor/time filters |
| Alarms | GET | /api/v1/alarms | Paginated and filterable alarm list |
| Alarms | GET | /api/v1/alarms/{alarm_id} | Alarm, evidence, impacts, and status history |
| Alarms | GET | /api/v1/alarms/{alarm_id}/impacts | Impacted inventory lots |
| Alarms | POST | /api/v1/alarms/{alarm_id}/transitions | Change alarm state and optionally assign an owner |
| Inspections | GET | /api/v1/inspections | List and filter inspections |
| Inspections | GET | /api/v1/inspections/{inspection_id} | Inspection details and status history |
| Inspections | POST | /api/v1/alarms/{alarm_id}/inspections | Create an inspection from an alarm |
| Inspections | POST | /api/v1/inspections/{inspection_id}/transitions | Start, complete, or cancel an inspection |
| Remediations | GET | /api/v1/remediations | List and filter remediations |
| Remediations | GET | /api/v1/remediations/{remediation_id} | Remediation, status, and verification history |
| Remediations | POST | /api/v1/inspections/{inspection_id}/remediations | Create a corrective action from a completed inspection |
| Remediations | POST | /api/v1/remediations/{remediation_id}/transitions | Start, complete, or cancel remediation |
| Remediations | POST | /api/v1/remediations/{remediation_id}/verification | Record independent verification |

### Example API requests

Check application health:

~~~bash
curl http://127.0.0.1:8000/health
~~~

Get the summary:

~~~bash
curl http://127.0.0.1:8000/api/v1/summary
~~~

List open high-severity alarms:

~~~bash
curl "http://127.0.0.1:8000/api/v1/alarms?status=OPEN&severity=HIGH"
~~~

Get a specific alarm after copying its ID from the alarm list:

~~~bash
curl "http://127.0.0.1:8000/api/v1/alarms/ALARM_ID"
~~~

Workflow request bodies and additional examples are documented in **docs/api.md**.

---

## Generated files and exports

The main generated artifacts are local outputs and are excluded from Git to keep the source repository lightweight.

| Path | Contents |
|---|---|
| data/*.duckdb | Local DuckDB databases |
| data/generated/<profile>/telemetry/ | Baseline Parquet telemetry and telemetry summary |
| data/ground_truth/<profile>/ | Separate evaluation labels |
| reports/ | Dataset validation, anomaly, risk, and evaluation JSON reports |
| exports/ | Optional portable seed bundles |

### Portable CSV and SQL seed bundle

Export master/reference tables from a generated database:

~~~bash
python scripts/export_seed_bundle.py \
  --database data/coldwatch-reviewer.duckdb \
  --output-dir exports/coldwatch-master
~~~

The default bundle contains master-data CSV files, a SQL schema, a replayable SQL loader, and a SHA-256 JSON manifest.

To include all operational tables (including telemetry, alarms, evidence, impacts, and workflow history), use the all-tables option:

~~~bash
python scripts/export_seed_bundle.py \
  --database data/coldwatch-reviewer.duckdb \
  --output-dir exports/coldwatch-all \
  --all
~~~

The all-tables export can be large. Ground-truth labels are deliberately excluded from operational seed exports.

To restore a bundle, create an empty DuckDB database, execute the exported **schema.sql**, change the working directory to the bundle directory, and execute **seed_data.sql** against the new database. Review the generated manifest for exported table names, row counts, and hashes.

---

## Configuration and reproducibility

Primary settings are in **config/settings.toml**.

| Setting | Purpose |
|---|---|
| project.random_seed | Seed used for deterministic generation |
| generation.profile | Default profile: starter or full |
| generation.reading_interval_minutes | Baseline sensor cadence |
| generation.start_timestamp | Start of the synthetic simulation window |
| generation.batch_size | Bounded telemetry generation batch size |
| profiles.starter | Laptop-friendly starter volumes |
| profiles.full | Challenge-scale volumes |
| rules | Illustrative default thresholds and rule behavior |

The product catalogue and allowed zone types are defined in **config/products.toml**.

To override the configured seed for a generation run, pass **--seed** to the applicable generator CLI. Generate into a fresh database when repeating the full pipeline. The telemetry generator requires empty telemetry tables and will refuse accidental duplicate insertion. The anomaly injector must not be run twice against the same generated database.

---

## Run the full challenge-scale profile

The starter profile is the recommended first run. To generate the full challenge-scale dataset, use a new database path and replace **starter** with **full** in each generation, injection, and validation command.

~~~bash
export DB_PATH="data/coldwatch-full-reviewer.duckdb"

python scripts/init_db.py --database "$DB_PATH"

python scripts/generate_master_data.py \
  --profile full \
  --database "$DB_PATH"

python scripts/generate_telemetry.py \
  --profile full \
  --database "$DB_PATH" \
  --output-root data/generated

python scripts/inject_anomalies.py \
  --profile full \
  --database "$DB_PATH" \
  --ground-truth-root data/ground_truth \
  --report-root reports/anomalies

python scripts/validate_dataset.py \
  --profile full \
  --database "$DB_PATH" \
  --generated-root data/generated \
  --ground-truth-root data/ground_truth \
  --report-root reports/validation

python scripts/run_risk_engine.py \
  --profile full \
  --database "$DB_PATH" \
  --report-root reports/risk-engine
~~~

The full profile generates one million sensor readings and a larger set of Parquet and DuckDB artifacts. Runtime and memory usage depend on the reviewer's machine. Allow additional disk space for the database, telemetry output, and optional CSV exports.

---

## Repository structure

~~~text
ColdWatch-AI/
├── app/
│   └── streamlit_app.py       # Streamlit operations dashboard
├── config/
│   ├── products.toml          # Synthetic product/storage catalogue
│   └── settings.toml          # Seed, profiles, cadence, and rule settings
├── data/
│   ├── generated/             # Local baseline Parquet artifacts
│   └── ground_truth/          # Local evaluation labels; not committed
├── docs/
│   ├── api.md
│   ├── architecture.md
│   ├── assumptions.md
│   ├── challenge_compliance.md
│   ├── dashboard.md
│   ├── data_dictionary.md
│   ├── data_generation.md
│   ├── erd.md
│   ├── prompt_history.md
│   ├── risk_engine.md
│   ├── validation.md
│   └── workflows.md
├── reports/                   # Local generated JSON reports
├── schemas/
│   └── schema.sql             # Canonical DuckDB schema
├── scripts/
│   ├── check_environment.py
│   ├── evaluate_risk_engine.py
│   ├── export_seed_bundle.py
│   ├── generate_master_data.py
│   ├── generate_telemetry.py
│   ├── init_db.py
│   ├── inject_anomalies.py
│   ├── run_api.py
│   ├── run_risk_engine.py
│   └── validate_dataset.py
├── src/coldwatch/
│   ├── engine/                # Deterministic risk rules
│   ├── evaluation/             # Independent evaluation metrics
│   ├── exports/                # CSV/SQL seed bundle export
│   ├── generators/             # Master, telemetry, and anomaly generators
│   ├── inventory/              # Zone/time inventory attribution
│   ├── validation/             # Dataset and integrity validation
│   ├── workflows/              # Alarm, inspection, remediation services
│   └── api/                    # FastAPI application and routes
├── tests/                      # Automated test suite
├── pyproject.toml
└── README.md
~~~

---

## Troubleshooting

### Python version is not 3.12

Create the environment with the Python 3.12 executable explicitly. The package metadata currently requires Python 3.12 and excludes Python 3.13.

~~~bash
python3.12 -m venv .venv
source .venv/bin/activate
python --version
~~~

### Module import error: coldwatch

Activate the virtual environment and install the project in editable mode:

~~~bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
~~~

### Dashboard says it cannot connect to the API

- Confirm the API is running in Terminal 1.
- Confirm it is using port 8000.
- Open http://127.0.0.1:8000/health.
- Start Streamlit only after the API health check succeeds.

### Port 8000 or 8501 is already in use

Stop the process currently using the port, or start the API on another port. The dashboard's current default API URL is 127.0.0.1:8000, so if you change the API port you must also update the dashboard API URL in **app/streamlit_app.py** before starting Streamlit.

### No alarms appear in the dashboard

The risk engine must be run without **--dry-run** to persist the alarms and impacts. Also confirm the API is connected to the same DuckDB path passed to the generation and risk-engine commands.

### A generator reports that records already exist

The data pipeline is designed for a fresh database. Do not rerun telemetry generation or anomaly injection against the same populated database. Initialize a new database filename and repeat the pipeline, or intentionally remove only the disposable generated database after confirming it contains no work you need.

### DuckDB reports that the database is locked

Stop other processes using the same DuckDB file, including another API instance or notebook. DuckDB file access should be coordinated; do not run multiple independent writers against the same local database.

### Validation fails

Read the generated validation JSON report and inspect the failed check names and details. Do not proceed to risk evaluation or API startup until the dataset validation status is PASS.

---

## Security, assumptions, and limitations

This repository is a local challenge prototype and an engineering demonstration. It is not a production-ready warehouse control system.

- Hosted workflow mutation routes support an optional shared `X-API-Token` check when `COLDWATCH_API_WRITE_TOKEN` is configured. This is not per-user authentication or role-based authorization; read-only routes remain public for the synthetic demo.
- The local API defaults to binding on 127.0.0.1. Do not expose the local development server to an untrusted network.
- Rate limiting and production identity/access management are not implemented. Render provides HTTPS for its public service URL; this prototype does not implement its own TLS termination.
- DuckDB is used as a local embedded database; production multi-user deployment would require an appropriately designed database and concurrency model.
- All generated warehouse, sensor, inventory, and anomaly data is synthetic.
- Thresholds and baseline signal values are illustrative. They are not regulatory guidance or universal product storage limits.
- Sensor drift is a peer-relative heuristic, not a calibrated metrology assessment.
- Outage detection consumes explicit missing-reading gap records. A production ingestion service would need to create and monitor these records from expected sensor cadence.
- A detected risk does not prove that inventory is damaged. Product disposition remains a human decision.
- Inspection and remediation records are human-triggered workflow actions; the detector does not automatically create an inspection for every finding.
- The demonstrated remediation and inventory disposition are simulated workflow records. No physical warehouse movement or equipment control is performed.
- The controlled evaluation dataset is intentionally small and synthetic. Its results must not be presented as production performance.

For these reasons, the supported deployment in this README is **local development/reviewer execution**. A public cloud deployment should only be made after adding authentication/authorization, network protections, persistent storage, appropriate database concurrency handling, and operational monitoring.

---

## Verification record

The following verification results were recorded locally on 1 November 2026.

| Check | Result |
|---|---|
| Starter dataset validation | 62/62 checks passed |
| Full dataset validation | 62/62 checks passed |
| Full-profile master and telemetry scale | 10 warehouses, 100 zones, 500 sensors, 1,000,000 readings, 10,000 inventory lots |
| Full-profile baseline door events | 2,800 records |
| Full-profile risk preview | 9 findings across 9 controlled scenarios |
| Independent event evaluation | 9/9 scenarios matched; no unmatched findings |
| Event precision / recall / F1 | 1.0000 / 1.0000 / 1.0000 on the labelled synthetic set |
| Affected-lot associations | 104 expected associations matched; no false positives or false negatives |
| Affected-lot precision / recall / F1 | 1.0000 / 1.0000 / 1.0000 on the labelled synthetic set |
| Automated test suite | 41 passed; one Starlette TestClient/httpx deprecation warning |
| Full-profile FastAPI health and operational endpoints | Manually exercised successfully |
| End-to-end workflow | Alarm investigation, inspection, simulated remediation, verification, and closure exercised |
| Negative control | Incomplete-impact alarm remained unchanged and was not automatically repaired |

**Important evaluation qualification:** the precision, recall, and F1 values above measure agreement with nine controlled synthetic scenarios and 104 expected lot associations. They are not estimates of real-world detection performance. The labelled set does not define a true-negative population, so accuracy and specificity are not reported.

See **docs/challenge_compliance.md** for the requirement-to-evidence matrix and outstanding evidence notes.

---

## Project documentation

| Document | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | Components, dependencies, and end-to-end data flow |
| [Challenge compliance](docs/challenge_compliance.md) | Requirement-to-implementation and evidence mapping |
| [Data dictionary](docs/data_dictionary.md) | Entity and field definitions |
| [ERD](docs/erd.md) | Core entities and relationships |
| [Data generation](docs/data_generation.md) | Synthetic baseline, cadence, and assumptions |
| [Risk engine](docs/risk_engine.md) | Rule logic, attribution, and detector limitations |
| [Validation](docs/validation.md) | Dataset and artifact validation checks |
| [API](docs/api.md) | Routes, payloads, and response behavior |
| [Dashboard](docs/dashboard.md) | Dashboard layout and API integration |
| [Workflows](docs/workflows.md) | Alarm, inspection, remediation, and verification lifecycles |
| [Assumptions](docs/assumptions.md) | Domain and simulation assumptions |
| [Deployment](docs/deployment.md) | Optional Render API and Streamlit Community Cloud setup |

---

**Repository:** https://github.com/PriyanshuYadav000/ColdWatch-AI  
**Primary branch:** main
