# Challenge Acceptance and Compliance Matrix

This matrix maps the submitted challenge brief to the implementation and records known limitations honestly. It is intended as a reviewer navigation guide, not a claim that every optional production feature exists.

## Business objective

| Brief requirement | Implementation | Status / evidence |
|---|---|---|
| Monitor temperature, humidity, occupancy and door events by zone | Deterministic risk rules over sensor readings, effective-dated mappings, zone configuration and door events | Implemented; see src/coldwatch/engine/risk_engine.py and docs/risk_engine.md. |
| Alert on risk conditions | Stable source keys and transactional alarm/evidence/impact persistence | Implemented; API and persistence tests. |
| Expose key decisions and exceptions | Finding JSON report, alarm detail, evidence and inventory-impact API, Streamlit investigation views | Implemented. |
| Preserve traceability | Source evidence, effective-dated assignments and append-only histories | Implemented and manually exercised end-to-end on the full-profile database. |
| Produce measurable outputs | Dataset validation, telemetry hashes, independent precision/recall/F1 evaluator | Implemented; run the evaluation CLI after the risk engine. |

## Workflow and data model

| Brief step | Implementation | Status / notes |
|---|---|---|
| 1. Master data first | Warehouse, Zone, Sensor, StorageRequirement, InventoryLot and assignment bridges | Implemented in master-data generator. |
| 2. Dependent transaction/event records | SensorReading, DoorEvent, LotZoneAssignment, sensor health and audit records | Implemented with FK and temporal validation. |
| 3. Domain logic, alerts and inspections | Risk engine generates findings/alarms. Operators create inspections through the API/dashboard, then record completion and disposition. | Implemented as a human-in-the-loop workflow; inspections are not auto-created by the detector. |
| 4. Persist state as explicit records | Alarm, inspection and remediation rows plus append-only history tables | Implemented transactionally. |
| 5. Deterministic outcomes and ground truth | Fixed seed, scenario generator, scenario manifest and separate CSV labels | Implemented. Ground truth is not a detector input. |

## Entity and relationship acceptance

All ten named challenge entities are present: Warehouse, Zone, Sensor, SensorReading, DoorEvent, InventoryLot, StorageRequirement, Alarm, Inspection and Remediation.

Supporting tables implement sensor/zone and lot/zone many-to-many relationships over time, requirement/allowed-zone bridge, sensor health events, evidence, alarm/lot impact, reading revisions/gaps and lifecycle histories. See docs/erd.md, docs/data_dictionary.md and schemas/schema.sql.

## Synthetic data and edge-case acceptance

| Brief criterion | Implementation / evidence |
|---|---|
| Full profile: 10 warehouses | Full profile generated locally. |
| Full profile: 100 zones | Full profile generated locally. |
| Full profile: 500 sensors | Full profile generated locally. |
| Full profile: 1,000,000 readings | Full profile generated locally; Parquet output and DuckDB load verified. |
| Full profile: 10,000 inventory lots | Full profile generated locally. |
| Fixed seed and parameterized scale | config/settings.toml, starter/full profiles, seeded generators. |
| Incompatible lot-zone | Controlled labelled scenario and explicit assignment history. |
| Sensor mapped to multiple zones without approval | Controlled overlapping assignment and detector finding. |
| Missing reading not treated as normal | Explicit sensor_reading_gap tombstones and outage detector. |
| Alarm not linked to affected lots | Negative-control alarm ALM-ANOM-000001; detector reports it but never auto-repairs it. |
| Derived values from source records | Counts, telemetry cadence, exposure intervals, dwell and status transitions are computed/validated from source records. |
| Ground truth separate from features | CSV plus checksum manifest; risk engine reports ground_truth_used=false. |
| Primary/foreign keys, enums, timestamps, quantity and audit validation | Dataset validator; full-profile run passed 62/62 checks (2026-10-31). |
| Reproducibility across independent full-profile runs | Deterministic generation and existing starter reproducibility coverage; a second full-scale independent regeneration comparison remains to be recorded. |

## Export and deliverable acceptance

| Brief criterion | Implementation | Status / limitation |
|---|---|---|
| README and run instructions | Root README plus generator/API/dashboard docs | Present; keep updated when new commands are added. |
| Data dictionary | Field-level dictionary for all ten core entities and supporting tables | docs/data_dictionary.md. |
| SQL schema | schemas/schema.sql | Present; schema is executable SQL. |
| CSV/JSON outputs | Ground-truth CSV, manifests, telemetry summaries, risk and validation reports in JSON | Present. |
| CSV + SQL seed bundle | scripts/export_seed_bundle.py exports operational tables to CSV with a replayable SQL loader and JSON SHA-256 manifest | Implemented; run locally and verify restore before submission. |
| Source/provenance manifest | Baseline telemetry summary and anomaly manifest contain profile, seed, counts, hashes and scenario records | Present. |
| Validation summary | scripts/validate_dataset.py JSON report | Full profile: 62/62 passed locally (2026-10-31). |

## Local verification record

The candidate reported the following local checks on 2026-11-01:

- pytest -q tests/test_api.py: 3 passed.
- pytest -q: 36 passed, one Starlette TestClient deprecation warning.
- Full-profile dataset validation: 62/62 passed.
- Full-profile FastAPI health and summary endpoints: HTTP 200.
- Full-profile operational workflow manually exercised through closure.
- Negative control remains OPEN, impact assessment FAILED, with no impact links and only its original history row.

New evaluation, export and CI additions in this branch require a fresh local run after pulling. Do not represent those tests or the first CI run as passing until verified.

## Known limitations

- The prototype is local-only and has no authentication, role-based authorization, rate limiting or TLS.
- The rule thresholds and all generated warehouse data are synthetic and illustrative, not regulatory limits or real-world operating recommendations.
- Inspection and remediation are human-triggered workflow records. The challenge prototype does not create an inspection automatically from every finding.
- The demonstrated relocation and verification are simulated workflow events; no physical inventory operation occurred.
- An independent full-profile reproducibility comparison is still to be documented.
- Prompt history must be reconciled against all AI tools used before final submission.
