# Architecture

## Architectural style

Modular monolith with independently testable Python modules. The generator, validation, risk engine, inventory impact resolver, workflow services, API, and dashboard share the same application package and local DuckDB store.

## Planned data flow

1. Load deterministic generation settings and fixed seed.
2. Create master data: warehouses, zones, sensors, and storage requirements.
3. Create inventory lots and effective-dated lot-to-zone assignments.
4. Generate baseline sensor readings in bounded batches at each sensor's configured cadence; write Parquet and bulk-load DuckDB.
5. Generate paired baseline door OPEN/CLOSE events for each zone; preserve event times and source identifiers.
6. Inject labelled anomalies into an otherwise valid baseline.
7. Persist source records and separate ground-truth labels.
8. Validate primary keys, foreign keys, enums, date ordering, lifecycle transitions, and reconciliation.
9. Evaluate environmental and data-quality rules.
10. Resolve affected lots by zone and time overlap.
11. Persist alarms and evidence; resolve affected inventory lots using zone/time overlap.
12. Advance alarm, inspection, and remediation workflows with transactional updates and append-only history.
13. Expose operational views and actions through FastAPI and Streamlit; compare decisions with isolated ground truth through an independent evaluator.

## Core decision approach

The primary decision engine is deterministic Python business logic. DuckDB performs relational joins and analytical queries. Sensor drift checks may use documented statistical comparisons. The core safety decisions do not depend on an LLM or an ML model.

## Data and evaluation separation

Ground-truth labels are stored separately from operational input tables. The risk engine must not read ground truth. An independent evaluator compares predicted decisions and inventory impacts with expected labels after processing.

## Traceability

State transitions are represented as append-only history records. Alarm evidence links decisions to source readings or door events. Inventory impact links alarms to affected lots. Effective-dated assignments preserve historical locations and sensor mappings.
