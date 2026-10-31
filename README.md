# ColdWatch AI

**Warehouse Environmental Monitoring & Risk Intelligence Platform**

ColdWatch AI is a locally runnable prototype for monitoring warehouse zones, detecting environmental and operational risks, identifying potentially affected inventory lots, and maintaining traceable alarm, inspection, and remediation workflows.

## Project goals

- Model warehouses, zones, sensors, readings, door events, inventory lots, storage requirements, alarms, inspections, and remediation.
- Generate deterministic synthetic data using a fixed seed and configurable starter/full profiles.
- Inject labelled edge cases only after generating a valid baseline.
- Detect temperature, humidity, occupancy, door, sensor-health, and data-integrity risks.
- Identify inventory exposure using zone and time overlap.
- Keep ground-truth labels separate from operational input data.
- Validate keys, relationships, enums, timestamps, lifecycle transitions, and reproducibility.

## Technology

Python 3.12, NumPy, Pandas, PyArrow, DuckDB, Parquet/CSV/JSON, FastAPI, Pydantic, Streamlit, and Pytest.

The primary risk decisions are made by an explainable Python rule engine. SQL is used for relational joins and inventory-impact queries. No external AI model or LLM is required for the core workflow.

## Requirements

- macOS (Apple Silicon supported) or another Python 3.12 environment
- Python 3.12
- Git

## Setup on macOS

If Python 3.12 is not installed and you use Homebrew:

```bash
brew install python@3.12
```

Clone the repository, then enter the project directory:

```bash
git clone https://github.com/PriyanshuYadav000/ColdWatch-AI.git
cd ColdWatch-AI
```

Create and activate a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Upgrade pip and install the project with development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verify the environment:

```bash
python scripts/check_environment.py
```

Initialize the local DuckDB schema:

```bash
python scripts/init_db.py
```

This creates `data/coldwatch.duckdb` and the empty relational tables defined in `schemas/schema.sql`. The database file is local and excluded from Git.

Run the test suite:

```bash
pytest
```

The schema-specific tests can also be run independently:

```bash
pytest -q tests/test_schema.py
```

## Generate master and inventory data

The synthetic product catalogue and allowed storage-zone types are configured in `config/products.toml`. Generate the starter master dataset after initializing a fresh DuckDB database:

```bash
python scripts/generate_master_data.py --profile starter
```

Use `--profile full` for the challenge-scale master data (10 warehouses, 100 zones, 500 sensors, and 10,000 inventory lots). This step generates the master/business entities and their effective-dated assignments.

Generate normal baseline sensor readings and door events next:

```bash
python scripts/generate_telemetry.py --profile starter
```

Use `--profile full` to generate exactly 1,000,000 sensor readings for the full profile. Readings are generated in bounded batches and written to Parquet; door events are generated as paired OPEN/CLOSE records. The default five-minute interval is preserved for each sensor. Starter and full profiles have a simulated time window derived from their reading counts so the baseline cadence is explicit.

Telemetry Parquet files and `telemetry_summary.json` are written under `data/generated/<profile>/telemetry/`. The summary includes row counts, coverage, canonical-content SHA-256 hashes, and Parquet file hashes. The telemetry command requires empty `sensor_reading` and `door_event` tables and refuses accidental duplicate insertion. Use a fresh initialized database for a new run.

Both master-data and telemetry generation are deterministic for the same seed and configuration.

## Anomaly injection and validation

After confirming the baseline telemetry, inject the nine labelled synthetic scenarios:

```bash
python scripts/inject_anomalies.py --profile starter
```

The injector writes operational audit evidence to DuckDB and a separate evaluation label file to `data/ground_truth/<profile>/ground_truth.csv`. The original baseline Parquet files are not modified. Do not run the injector twice against the same database; use a fresh database generated from the schema and baseline pipeline for a new run.

Validate the completed dataset and audit trails:

```bash
python scripts/validate_dataset.py --profile starter
```

The command writes `reports/validation_<profile>.json`. It exits successfully only when artifact checksums, telemetry integrity, audit counts, all nine scenario evidence records, and key inventory/mapping constraints pass. See [docs/validation.md](docs/validation.md) for the detailed checks.

## FastAPI backend

Before launching the API, persist detected alarms (the previous dry run did not write them):

```bash
python scripts/run_risk_engine.py \
  --profile starter \
  --database data/coldwatch-risk-engine.duckdb \
  --report-root reports/risk-engine
```

Then install the API/test dependencies and run the server:

```bash
python -m pip install -e ".[dev]"
python scripts/run_api.py --database data/coldwatch-risk-engine.duckdb
```

Open `http://127.0.0.1:8000/docs` for interactive OpenAPI documentation. The API exposes warehouse/zone and sensor telemetry reads, alarm/evidence/impact reads, and alarm/inspection/remediation workflow actions. Read routes use a read-only DuckDB connection; mutation routes call the transactional workflow services. See [docs/api.md](docs/api.md) for endpoints, request examples, and limitations.

The prototype has no authentication/authorization or rate limiting and should remain bound to localhost during the challenge.

## Streamlit operations dashboard

With FastAPI running in Terminal 1, open a second terminal and run:

\`\`\`bash
source .venv/bin/activate
streamlit run app/streamlit_app.py
\`\`\`

Open the local Streamlit URL printed by the command (normally \`http://localhost:8501\`). The dashboard includes KPI overview, alarm filtering and investigation, evidence and impacted-lot review, alarm transitions, inspection creation, warehouse/zone telemetry charts, inspection/remediation management, and independent verification.

The dashboard communicates through FastAPI and does not query DuckDB directly. See [docs/dashboard.md](docs/dashboard.md) for architecture and operating instructions.

## Operational workflows

The Python workflow services manage alarm, inspection, and remediation lifecycles with transactional state changes and append-only status history. An alarm cannot be resolved while its inventory-impact assessment is incomplete. Inspection completion requires findings and an explicit disposition. Remediation must be linked to a completed inspection, and independent verification is stored in its own history table.

See [docs/workflows.md](docs/workflows.md) for lifecycle transitions and Python service examples. After pulling schema updates, initialize your selected database to create any new history tables without deleting existing data:

```bash
python scripts/init_db.py --database data/coldwatch-risk-engine.duckdb
```

## Risk detection engine

Preview the deterministic operational risk rules without writing alarms:

```bash
python scripts/run_risk_engine.py --profile starter --dry-run
```

After reviewing the JSON report, persist traceable alarms, evidence, and zone/time-overlapping inventory impacts:

```bash
python scripts/run_risk_engine.py --profile starter
```

The first rule set detects temperature and humidity requirement excursions, occupancy threshold breaches, prolonged door-open events, explicit sensor outages, peer-relative temperature sensor drift, incompatible lot placement, unapproved overlapping sensor mappings, and alarms with incomplete inventory-impact assessments. The engine never reads the separate ground-truth CSV. See [docs/risk_engine.md](docs/risk_engine.md) for thresholds, attribution rules, limitations, and run behavior.

## Configuration

The generator configuration is in `config/settings.toml` (read with Python's built-in TOML parser).

- `project.random_seed` controls deterministic generation.
- `generation.profile` selects `starter` or `full`.
- `profiles.starter` and `profiles.full` define the corresponding record volumes.
- Risk-rule values are configurable and illustrative; product-specific limits belong in `StorageRequirement` records.

## Repository layout

```text
app/                 API and dashboard entry points
config/              Runtime and synthetic product catalogue configuration
schemas/             SQL schema and data dictionary
src/coldwatch/       Reusable application logic
  generators/        Synthetic master, business, and event data
  engine/            Environmental and data-quality decision rules
  inventory/         Inventory exposure calculations
  validation/        Data-integrity and reproducibility checks
  workflows/         Alarm, inspection, and remediation services
data/
  generated/         Generated synthetic input data (not committed at scale)
  ground_truth/      Separate expected labels for evaluation
docs/                Architecture, assumptions, and AI prompt history
reports/             Generated validation and evaluation reports
scripts/             Developer and generation utilities
tests/               Automated tests
```

## Data and provenance

The project uses synthetic data created by this repository. No personal, confidential, or proprietary warehouse data is required. Any future external reference dataset must have its source URL, version, retrieval date, licence/terms, attribution, transformations, and discarded fields documented.

## AI-use transparency

All prompts submitted to AI tools during this project must be recorded in `docs/prompt_history.md`. AI-generated suggestions must be reviewed and tested by the candidate. The candidate is responsible for understanding and validating the implementation.

## Current implementation status

### Implemented
- Python 3.12 project environment and configuration loader.
- DuckDB relational schema for the 10 challenge entities and supporting relationship/history tables.
- Entity relationship design and field-level data dictionary.
- DuckDB schema initialization script.
- Deterministic starter/full master-data generator for warehouses, zones, sensors, storage requirements, inventory lots, and effective-dated assignments.
- In-memory validation for master-data IDs, parent references, and compatible baseline lot placement.
- Deterministic baseline sensor-reading generator with bounded Parquet batches and DuckDB bulk loading.
- Deterministic paired baseline door OPEN/CLOSE event generator.
- Telemetry coverage summaries, canonical content hashes, and Parquet file checksums.
- Documented cadence, coverage, baseline signal, and door-event assumptions in `docs/data_generation.md`.
- Deterministic injection of nine labelled environmental, operational, sensor-health, inventory-placement, mapping, and alarm-impact scenarios.
- Separate ground-truth CSV with SHA-256 manifest and auditable reading revision / gap records.
- Post-injection validator for artifact integrity, telemetry cadence, inventory allocations, mapping overlaps, revision lineage, and scenario evidence.
- Deterministic risk engine for environmental excursions, occupancy, door, outage, drift, inventory compatibility, mapping integrity, and incomplete alarm-impact detection.
- Transactional alarm persistence with stable source keys, evidence links, and inventory exposure joins; supports read-only dry run.
- Transactional alarm, inspection, and remediation workflow services with append-only status and verification history.
- FastAPI REST endpoints for warehouse/zone telemetry, alarms, evidence, inventory impact, inspections, and remediations.

### Verification status
- Starter-profile dataset validation completed locally: 62/62 validation checks passed (2026-10-31).
- Earlier core test run: 33 tests passed before API test additions; rerun the expanded suite locally.
- Full one-million-reading performance and output verification remains pending.
- Local starter-profile risk-engine dry-run and command verification completed by the candidate.
- FastAPI startup and interactive docs/OpenAPI returned HTTP 200 in the local smoke check (2026-10-31).
- Streamlit dashboard and end-to-end API workflow smoke checks remain to be run locally.

### Remaining work
- Run expanded pytest suite and dashboard/API smoke test.
- Complete full-profile performance and output verification.
- Add automated fresh-run reproducibility comparison across independent databases.

Do not treat items under "Not yet implemented" as completed functionality.
