# AI Prompt History

YottaFlex's candidate-project FAQ requires the prompts submitted to every AI tool used for this project. This log records the project-related prompts sent in the ChatGPT conversation. Responses are not required by the FAQ. If another AI tool is used, add its prompts here verbatim as well.

> Do not add credentials, passwords, personal identity documents, or confidential data to this file.

## Prompt log

### 2026-10-30/31 — ChatGPT — Prompt 001: Original challenge brief

```text
72 Hours Code Challenge
Associate AI Forward Deployed Engineers · AI Forward Deployed Engineer

Problem statement
Read it carefully. Your build should cover every point below.

Business Context / Problem
Monitor temperature, humidity, occupancy and door events by warehouse zone and alert on risk conditions.

Objective and Scope
Build a working solution for this end-to-end workflow: Monitor temperature, humidity, occupancy and door events by warehouse zone and alert on risk conditions. The solution should expose key decisions and exceptions, preserve traceability, and produce measurable outputs. Medium complexity means multiple related entities, conditional rules, at least one exception path and meaningful evaluation metrics.

Primary Actors / Systems
Plant operator, maintenance/quality engineer, production planner and OT/IT administrator

Business Workflow and Rules
1. Generate/reference master data first: Warehouse, Zone, Sensor, SensorReading, DoorEvent.
2. Generate dependent transaction/event records using valid parent IDs.
3. Apply business logic: Create warehouse/zone hierarchy; place sensors in zones; define requirements per stored product/lot; generate readings and door events; link lots to storage zones and dwell times; inject localized excursions and sensor outages; generate alerts and inspections.
4. Persist state changes as explicit records/events where relevant; do not overwrite history.
5. Generate outcomes and ground-truth labels from documented rules, not independent random assignment.

Entities and Relationships
Core entities: Warehouse, Zone, Sensor, SensorReading, DoorEvent, InventoryLot, StorageRequirement, Alarm, Inspection, Remediation. Define a primary key for every entity and foreign keys for every relationship. Document cardinality (1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states. Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins. For deliberate anomaly cases, keep an issue_type/ground_truth label rather than silently corrupting unrelated rows.

ID / Nomenclature Conventions
WH-001; ZONE-<warehouse-id>-001; SNS-<zone-id>-TEMP-01; RDG-<sensor-id>-<timestamp>; LOT-2026-000001.

Synthetic Data Generation Instructions (Granular)
Step 1 — Write a data dictionary for: Warehouse, Zone, Sensor, SensorReading, DoorEvent, InventoryLot, StorageRequirement, Alarm, Inspection, Remediation. For each field specify type, required/optional, allowed values, units, uniqueness and validation rule.
Step 2 — Create parent/master tables first, then child and event tables. Apply this domain-specific generation logic: Create warehouse/zone hierarchy; place sensors in zones; define requirements per stored product/lot; generate readings and door events; link lots to storage zones and dwell times; inject localized excursions and sensor outages; generate alerts and inspections.
Step 3 — Apply deterministic IDs and naming: WH-001; ZONE-<warehouse-id>-001; SNS-<zone-id>-TEMP-01; RDG-<sensor-id>-<timestamp>; LOT-2026-000001. Keep counters zero-padded, IDs immutable, and referenced IDs exactly consistent across files.
Step 4 — Generate the baseline volume: Generate 10 warehouses, 100 zones, 500 sensors, 1 million readings and 10,000 inventory lots. Include cold/hot zones, door-open events and sensor drift. Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full datasets use the same generator.
Step 5 — Generate valid baseline records first, then inject labelled edge cases: Lot assigned to incompatible zone; sensor mapped to multiple zones without rationale; reading gap treated as normal; alarm not linked to affected lots.
Step 6 — Calculate derived values (totals, balances, scores, statuses and dates) from source records rather than generating them independently.
Step 7 — Produce a separate ground_truth.csv/json for known anomalies, expected decisions or labels; never leak it into input features.
Step 8 — Run validation: primary-key uniqueness, foreign-key integrity, allowed enums, date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks.
Step 9 — Export CSV/JSON/SQL seed data plus README, schema/data dictionary, generator command, fixed seed, source manifest and validation summary.
Step 10 — Regenerate twice with the same seed and compare row counts/checksums to demonstrate reproducibility.

Suggested Volume and Coverage
Generate 10 warehouses, 100 zones, 500 sensors, 1 million readings and 10,000 inventory lots. Include cold/hot zones, door-open events and sensor drift.

Required Edge Cases / Negative Scenarios
Lot assigned to incompatible zone; sensor mapped to multiple zones without rationale; reading gap treated as normal; alarm not linked to affected lots.

Validation Rules / Acceptance Criteria
Affected lots are identified from time and zone overlap; alert thresholds are consistent; sensor gaps are explicit; remediation links to inventory impact.

Evaluation Focus for Generated Data
Review: (a) realistic distributions and meaningful variation; (b) consistent IDs and valid joins; (c) dependency/lifecycle consistency across entities; (d) correct derived values and reconciliation; (e) normal, boundary, failure and exception coverage; (f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful provenance.

Public Data Guidance / Lawful Use
External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration.

Before you submit, make sure you have
Pushed your code to a public GitHub repository
Added a README explaining how to run it
Included any tests the problem asks for

Candidate FAQ (PDF)
Submission Guidelines (PDF)

Submit your work
You can submit once, so check everything before you do.

GitHub repository URL*
[https://github.com/username/repo](https://github.com/username/repo)
The repository must be public so we can review it.
Your submission is final once sent.
this is challenge i got ... i alo get id card and
```

### 2026-10-30/31 — ChatGPT — Prompt 002: Understand the brief before implementation

```text
lest underatnd the problem statement first ...
```

### 2026-10-30/31 — ChatGPT — Prompt 003: Design the project workflow and system

```text
fine lets crete the projectflow and system. design befor code
```

### 2026-10-30/31 — ChatGPT — Prompt 004: Confirm the technology stack and decision-making approach

```text
# 10. Technology and implementation decisions

Here is the proposed technology stack.

Layer | Technology | Reason
Programming | Python | Main development language
Data generator | NumPy, Python | Deterministic synthetic generation
Data processing | PyArrow, Pandas | Efficient data processing
Database | DuckDB | Relational analytical queries
File format | Parquet, CSV, JSON | Storage and export
API | FastAPI, Pydantic | Typed interfaces and validation
Dashboard | Streamlit | Rapid operational interface
Testing | Pytest | Automated validation
Version control | Git and GitHub | Reviewable development.

only these but we use the decion making using ?
```

### 2026-10-30/31 — ChatGPT — Prompt 005: Clarify whether an AI model was included

```text
we did not used any
```

### 2026-10-30/31 — ChatGPT — Prompt 006: Clarify AI/LLM usage

```text
we did not used any. ai /llm model ?
```

### 2026-10-30/31 — ChatGPT — Prompt 007: Divide work into tracked milestones under the time limit

```text
ok fine lest divide the workflow like eack big step waht we establish so that we track the project working and how much we work .... we hvae only 1 daya and 12 hours to completeit
```

### 2026-10-31 — ChatGPT — Prompt 008: Start repository and environment setup

```text
let strat with making the folder and project rep in git hub and setinng the enviroment
```

## Ongoing maintenance

1. Add every subsequent prompt submitted to ChatGPT or another AI tool.
2. Preserve each prompt verbatim; do not replace it with a summary.
3. Record the tool name and date.
4. Before submission, reconcile this file against the AI tools actually used during the project.
