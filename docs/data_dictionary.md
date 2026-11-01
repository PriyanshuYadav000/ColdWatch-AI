# ColdWatch AI — Data Dictionary

This dictionary describes the ten challenge entities and the supporting bridge, evidence, audit, and health-event tables implemented in schemas/schema.sql. SQL types and constraints in that schema are authoritative. Timestamps are timezone-aware (TIMESTAMPTZ) and normalized to UTC by the application.

Notation: PK = primary key; FK = foreign key; UQ = unique; Required means NOT NULL; Optional means nullable. Numeric limits are illustrative synthetic configuration values, not regulatory guidance.

## Core challenge entities

### Warehouse

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| warehouse_id | VARCHAR | Yes | PK; WH-001 | Immutable warehouse identifier. |
| site_code | VARCHAR | Yes | UQ | Unique site/business code. |
| name | VARCHAR | Yes | — | Display name. |
| address_line | VARCHAR | No | — | Optional street address. |
| city | VARCHAR | Yes | — | City name. |
| region | VARCHAR | No | — | State/province/region. |
| country | VARCHAR | Yes | — | Country name/code. |
| timezone | VARCHAR | Yes | Default UTC | IANA timezone identifier expected by the application. |
| status | VARCHAR | Yes | ACTIVE, INACTIVE, MAINTENANCE; default ACTIVE | Operational state. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |
| updated_at | TIMESTAMPTZ | Yes | Default current timestamp | Must be >= created_at. |

### Zone

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| zone_id | VARCHAR | Yes | PK; ZONE-WH-001-001 | Immutable zone identifier. |
| warehouse_id | VARCHAR | Yes | FK → warehouse | Every zone belongs to one warehouse. |
| name | VARCHAR | Yes | UQ with warehouse_id | Name unique within a warehouse. |
| zone_type | VARCHAR | Yes | FROZEN, COLD, CHILLED, AMBIENT, HOT, CONTROLLED | Storage/operating environment category. |
| area_sqm | DECIMAL(12,2) | No | Square metres; > 0 when present | Physical area. |
| occupancy_warning_limit_pct | DECIMAL(5,2) | Yes | Percent; 0–100; default 90 | Occupancy warning threshold. |
| status | VARCHAR | Yes | ACTIVE, INACTIVE, MAINTENANCE, BLOCKED | Zone operational state. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |
| updated_at | TIMESTAMPTZ | Yes | Default current timestamp | Must be >= created_at. |

### Sensor

A sensor's zone is derived through the effective-dated sensor_zone_assignment bridge, not a direct zone_id column.

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| sensor_id | VARCHAR | Yes | PK; SNS-ZONE-WH-001-001-TEMP-01 | Immutable sensor identifier. |
| sensor_type | VARCHAR | Yes | TEMPERATURE, HUMIDITY, OCCUPANCY | Measurement type. |
| unit | VARCHAR | Yes | degC, pct_rh, pct_capacity | Must correspond to sensor_type. |
| manufacturer | VARCHAR | No | — | Manufacturer. |
| model | VARCHAR | No | — | Device model. |
| serial_number | VARCHAR | No | UQ when present | Manufacturer serial number. |
| expected_interval_seconds | INTEGER | Yes | Seconds; > 0; default 300 | Expected telemetry cadence. |
| calibration_due_at | TIMESTAMPTZ | No | — | If present, later than installed_at. |
| installed_at | TIMESTAMPTZ | Yes | — | Installation timestamp. |
| retired_at | TIMESTAMPTZ | No | — | If present, not earlier than installed_at. |
| status | VARCHAR | Yes | ACTIVE, INACTIVE, MAINTENANCE, RETIRED | Sensor lifecycle state. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |

### SensorReading

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| reading_id | VARCHAR | Yes | PK; RDG-<sensor-id>-<timestamp> | Immutable reading identifier. |
| sensor_id | VARCHAR | Yes | FK → sensor | Source sensor. |
| observed_at | TIMESTAMPTZ | Yes | UQ with sensor_id | Measurement event time; UTC. |
| reading_value | DECIMAL(12,4) | Yes | Unit follows sensor | Reading value; humidity and occupancy percentages must be 0–100. |
| unit | VARCHAR | Yes | degC, pct_rh, pct_capacity | Measurement unit. |
| quality_status | VARCHAR | Yes | GOOD, SUSPECT, BAD; default GOOD | Ingestion/quality status. |
| ingested_at | TIMESTAMPTZ | Yes | Default current timestamp | Time persisted by the platform. |
| source_sequence | BIGINT | No | Integer >= 0 | Optional source sequence number. |

### DoorEvent

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| door_event_id | VARCHAR | Yes | PK | Immutable event identifier. |
| zone_id | VARCHAR | Yes | FK → zone | Zone containing the door. |
| door_id | VARCHAR | Yes | UQ with zone_id, event_type, event_at | Stable door identifier within a zone. |
| event_type | VARCHAR | Yes | OPEN, CLOSE | Door state event. |
| event_at | TIMESTAMPTZ | Yes | UTC | Event timestamp. |
| source_system | VARCHAR | Yes | Default SYNTHETIC | Producer/source identifier. |
| ingested_at | TIMESTAMPTZ | Yes | Default current timestamp | Persistence timestamp. |
| notes | VARCHAR | No | — | Optional event context. |

### InventoryLot

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| lot_id | VARCHAR | Yes | PK; LOT-2026-000001 | Immutable product lot/batch identifier. |
| product_code | VARCHAR | Yes | — | Product catalogue code. |
| product_name | VARCHAR | Yes | — | Product display name. |
| product_category | VARCHAR | Yes | — | Product category. |
| requirement_id | VARCHAR | Yes | FK → storage_requirement | Storage requirement version governing the lot. |
| quantity_received | DECIMAL(18,3) | Yes | > 0 | Quantity received. |
| quantity_unit | VARCHAR | Yes | — | Unit such as cases, kg, or units. |
| manufactured_at | TIMESTAMPTZ | No | — | If present, not later than received_at. |
| received_at | TIMESTAMPTZ | Yes | — | Receipt timestamp. |
| expires_at | TIMESTAMPTZ | No | — | If present, later than received_at. |
| status | VARCHAR | Yes | RECEIVED, STORED, QUARANTINED, RELEASED, EXPIRED, DISPOSED | Inventory lifecycle state. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |

The lot's storage location and dwell intervals are maintained in lot_zone_assignment, allowing moves and split quantities to be retained historically.

### StorageRequirement

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| requirement_id | VARCHAR | Yes | PK | Immutable requirement-version identifier. |
| product_code | VARCHAR | Yes | UQ with version | Product governed by the requirement. |
| product_category | VARCHAR | Yes | — | Product category. |
| version | INTEGER | Yes | > 0 | Requirement version. |
| temperature_min_c | DECIMAL(7,3) | Conditional | °C | Both min/max must be null or both present; min <= max. |
| temperature_max_c | DECIMAL(7,3) | Conditional | °C | Both min/max must be null or both present; min <= max. |
| humidity_min_pct | DECIMAL(6,3) | Conditional | % RH; 0–100 | Both min/max must be null or both present; min <= max. |
| humidity_max_pct | DECIMAL(6,3) | Conditional | % RH; 0–100 | Both min/max must be null or both present; min <= max. |
| effective_from | TIMESTAMPTZ | Yes | — | Inclusive start of requirement validity. |
| effective_to | TIMESTAMPTZ | No | — | Exclusive end; later than effective_from when present. |
| status | VARCHAR | Yes | ACTIVE, INACTIVE, SUPERSEDED; default ACTIVE | Requirement lifecycle state. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |

At least one temperature or humidity range must be defined. Allowed zone types are normalized in requirement_allowed_zone_type.

### Alarm

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| alarm_id | VARCHAR | Yes | PK | Stable alarm identifier. |
| rule_id | VARCHAR | Yes | — | Detector rule identifier. |
| alarm_type | VARCHAR | Yes | Temperature/humidity/occupancy/door/outage/drift/storage/mapping/impact enum in schema | Categorized risk condition. |
| severity | VARCHAR | Yes | LOW, MEDIUM, HIGH, CRITICAL | Priority/severity. |
| zone_id | VARCHAR | No | FK → zone | Optional affected zone. |
| sensor_id | VARCHAR | No | FK → sensor | Optional source sensor. |
| detected_at | TIMESTAMPTZ | Yes | — | Detection time; >= event_start_at. |
| event_start_at | TIMESTAMPTZ | Yes | — | Inclusive incident start. |
| event_end_at | TIMESTAMPTZ | No | — | Exclusive event end; later than event_start_at. |
| title | VARCHAR | Yes | — | Short operator-facing summary. |
| description | VARCHAR | Yes | — | Rule explanation. |
| status | VARCHAR | Yes | OPEN, ACKNOWLEDGED, INVESTIGATING, RESOLVED, CLOSED; default OPEN | Alarm lifecycle state. |
| impact_assessment_status | VARCHAR | Yes | NOT_STARTED, IN_PROGRESS, COMPLETE, FAILED; default NOT_STARTED | Inventory impact assessment state. |
| source_event_key | VARCHAR | No | UQ when present | Deterministic idempotency key for source incident. |
| assigned_to | VARCHAR | No | — | Current owner. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |
| updated_at | TIMESTAMPTZ | Yes | Default current timestamp | Must be >= created_at. |

Evidence and inventory impact are normalized to alarm_evidence and alarm_inventory_impact. State changes are appended to alarm_status_history.

### Inspection

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| inspection_id | VARCHAR | Yes | PK | Inspection identifier. |
| alarm_id | VARCHAR | Yes | FK → alarm | Alarm initiating inspection. |
| lot_id | VARCHAR | No | FK → inventory_lot | Optional inspected lot; workflow also requires it to be linked to the alarm impact. |
| inspection_type | VARCHAR | Yes | PRODUCT_QUALITY, SENSOR_HEALTH, FACILITY, STORAGE_COMPATIBILITY, DOOR_EVENT | Inspection category. |
| requested_at | TIMESTAMPTZ | Yes | Default current timestamp | Request time. |
| due_at | TIMESTAMPTZ | No | — | If present, >= requested_at. |
| started_at | TIMESTAMPTZ | No | — | Set on transition to IN_PROGRESS; >= requested_at. |
| completed_at | TIMESTAMPTZ | No | — | Set on completion; requires started_at and findings. |
| inspector | VARCHAR | No | — | Assigned inspector. |
| status | VARCHAR | Yes | PENDING, IN_PROGRESS, COMPLETED, CANCELLED; default PENDING | Inspection lifecycle state. |
| findings | VARCHAR | No | Required by workflow on completion | Human-entered findings. |
| product_disposition | VARCHAR | Yes | PENDING, RELEASED, QUARANTINED, REJECTED, RETEST_REQUIRED, NOT_APPLICABLE; default PENDING | Explicit human disposition. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |

Status changes are appended to inspection_status_history. Product-quality inspection completion cannot use NOT_APPLICABLE.

### Remediation

| Field | SQL type | Required | Key / allowed values / unit | Validation and meaning |
|---|---|---:|---|---|
| remediation_id | VARCHAR | Yes | PK | Corrective action identifier. |
| inspection_id | VARCHAR | Yes | FK → inspection | Source completed inspection. |
| lot_id | VARCHAR | No | FK → inventory_lot | Optional affected lot; must agree with lot-specific inspection and alarm impact when supplied. |
| action_type | VARCHAR | Yes | Application-defined non-empty string | Corrective action classification. |
| description | VARCHAR | Yes | Application-defined non-empty string | Action details. |
| assigned_to | VARCHAR | No | — | Responsible operator/team. |
| status | VARCHAR | Yes | PLANNED, IN_PROGRESS, COMPLETED, CANCELLED; default PLANNED | Remediation lifecycle state. |
| started_at | TIMESTAMPTZ | No | — | Set when work starts. |
| completed_at | TIMESTAMPTZ | No | — | Requires started_at and cannot precede it. |
| verification_status | VARCHAR | Yes | PENDING, PASSED, FAILED, NOT_REQUIRED; default PENDING | Independent verification state. |
| verified_at | TIMESTAMPTZ | No | — | Set on verification; requires completion. |
| created_at | TIMESTAMPTZ | Yes | Default current timestamp | Creation timestamp. |

Status and independent verification changes are appended to remediation_status_history and remediation_verification_history. Workflow services enforce allowed transitions.

## Supporting relationship and audit tables

| Table | Primary key | Required foreign keys / key fields | Purpose and core validation |
|---|---|---|---|
| requirement_allowed_zone_type | Composite: requirement_id + zone_type | requirement_id → storage_requirement | Normalized allowed-zone bridge; zone_type is a configured enum. |
| sensor_zone_assignment | assignment_id | sensor_id → sensor; zone_id → zone | Effective-dated sensor/zone bridge; [valid_from, valid_to); approved overlaps require rationale and approver metadata. |
| lot_zone_assignment | assignment_id | lot_id → inventory_lot; zone_id → zone | Effective-dated lot placement and dwell time; quantity > 0; active records have no stored_to. |
| sensor_health_event | health_event_id | sensor_id → sensor | OUTAGE/DRIFT health event with start/end, status and expected cadence metadata. |
| alarm_evidence | evidence_id | alarm_id plus exactly one source FK | Links alarm to one reading, door event, health event, sensor mapping, or lot mapping. |
| alarm_inventory_impact | impact_id | alarm_id; composite FK (lot_zone_assignment_id, lot_id) | Many-to-many alarm/lot exposure bridge with exposure interval and impact status. |
| alarm_status_history | history_id | alarm_id → alarm | Append-only previous/new status, actor, reason and timestamp. |
| inspection_status_history | history_id | inspection_id → inspection | Append-only inspection state history. |
| remediation_status_history | history_id | remediation_id → remediation | Append-only remediation state history. |
| remediation_verification_history | verification_id | remediation_id → remediation | Append-only prior/new verification status, verifier, reason and timestamp. |
| sensor_reading_revision | revision_id | reading_id → sensor_reading | Retains previous and revised value/quality plus revision note and timestamp. |
| sensor_reading_gap | gap_id | sensor_id → sensor | Explicit tombstone for an expected but missing reading; deliberately no FK to absent sensor_reading row. |

## Identifier and temporal conventions

- Warehouse: WH-001.
- Zone: ZONE-WH-001-001.
- Sensor: SNS-ZONE-WH-001-001-TEMP-01.
- Reading: RDG-<sensor-id>-<UTC-timestamp>.
- Inventory lot: LOT-2026-000001.
- Assignment/history/evidence identifiers use entity-specific prefixes.
- All temporal assignment and exposure intervals use half-open semantics: start inclusive, end exclusive; a NULL end means open-ended.
- Application timestamps are timezone-aware and normalized to UTC. User interfaces may render local offsets.
- Foreign keys enforce parent existence; application validators enforce cross-row temporal overlaps, quantity reconciliation, business transitions and scenario-specific constraints.

## Evaluation labels are not operational data

The ground-truth CSV is an isolated evaluation artifact under data/ground_truth/<profile>/ground_truth.csv. It is not a DuckDB table and is not read by the risk engine. The independent evaluator reads it only after a risk report exists.
