# ColdWatch AI Data Dictionary

**Schema version:** 1.0  
**Database:** DuckDB  
**Timestamp convention:** UTC, represented as `TIMESTAMPTZ`  
**Units:** Explicitly recorded in the relevant field or sensor metadata  
**Seed:** `20261022` (configured in `config/settings.toml`)

This dictionary documents the operational schema in `schemas/schema.sql`. The ten challenge entities are listed first. Supporting tables follow. Ground truth is documented separately because it must not be available to the risk engine as an input.

## Conventions

- **Required** means `NOT NULL` or part of a primary key.
- **Optional** means nullable.
- **PK** means primary key; **FK** means foreign key.
- String identifiers are immutable. IDs are generated deterministically and are never recycled.
- All time intervals use **[start, end)** semantics: start is inclusive, end is exclusive. A NULL end means the interval remains open.
- Database constraints enforce row-level rules. Cross-row rules (temporal overlap, quantity reconciliation, legal lifecycle transitions, and complete lot impact) are checked by validation services.
- Enum values are stored as VARCHAR with SQL CHECK constraints to keep the schema readable and portable.

---

# 1. Required business entities

## 1.1 Warehouse

**Purpose:** Physical warehouse facility. One warehouse contains multiple zones.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| warehouse_id | VARCHAR | Yes | PK | `WH-001` format | Immutable; unique; must match `^WH-[0-9]{3,}$` |
| site_code | VARCHAR | Yes | UNIQUE | Stable site code | Non-empty; unique across warehouses |
| name | VARCHAR | Yes | — | Human-readable name | Non-empty |
| address_line | VARCHAR | No | — | Free text | Trim whitespace |
| city | VARCHAR | Yes | — | City name | Non-empty |
| region | VARCHAR | No | — | State/province | Trim whitespace |
| country | VARCHAR | Yes | — | Country name/code | Non-empty |
| timezone | VARCHAR | Yes | — | IANA timezone; default UTC | Must resolve to a valid timezone |
| status | VARCHAR | Yes | — | ACTIVE, INACTIVE, MAINTENANCE | Must be one of the allowed values |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set once at creation |
| updated_at | TIMESTAMPTZ | Yes | — | UTC instant | Must be greater than or equal to created_at |

## 1.2 Zone

**Purpose:** Storage or operating zone belonging to exactly one warehouse.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| zone_id | VARCHAR | Yes | PK | `ZONE-<warehouse-id>-001` | Immutable; warehouse portion must reference the parent warehouse |
| warehouse_id | VARCHAR | Yes | FK → warehouse | Existing warehouse ID | Parent must exist |
| name | VARCHAR | Yes | UNIQUE with warehouse_id | Zone name | Non-empty within a warehouse |
| zone_type | VARCHAR | Yes | — | FROZEN, COLD, CHILLED, AMBIENT, HOT, CONTROLLED | Must be an allowed zone type |
| area_sqm | DECIMAL(12,2) | No | — | Square metres (m²) | If present, greater than 0 |
| occupancy_warning_limit_pct | DECIMAL(5,2) | Yes | — | Percent of configured zone capacity; default 90% | Between 0 and 100 |
| status | VARCHAR | Yes | — | ACTIVE, INACTIVE, MAINTENANCE, BLOCKED | Must be an allowed value |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set at creation |
| updated_at | TIMESTAMPTZ | Yes | — | UTC instant | Greater than or equal to created_at |

**Occupancy assumption:** An occupancy sensor reports zone-capacity utilization as a percentage (0–100%). It does not report a human headcount. The warning threshold is stored per zone and can be overridden by configuration where documented.

## 1.3 Sensor

**Purpose:** Identity and configuration of an installed monitoring device. Zone membership is kept in SensorZoneAssignment, not directly on this table.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| sensor_id | VARCHAR | Yes | PK | `SNS-<zone-id>-<TYPE>-01` | Immutable; unique; deterministic |
| sensor_type | VARCHAR | Yes | — | TEMPERATURE, HUMIDITY, OCCUPANCY | Must be an allowed type |
| unit | VARCHAR | Yes | — | degC, pct_rh, pct_capacity | Must match sensor_type |
| manufacturer | VARCHAR | No | — | Manufacturer name | Trim whitespace |
| model | VARCHAR | No | — | Model name | Trim whitespace |
| serial_number | VARCHAR | No | UNIQUE | Manufacturer serial | Unique when supplied |
| expected_interval_seconds | INTEGER | Yes | — | Seconds; default 300 | Positive integer |
| calibration_due_at | TIMESTAMPTZ | No | — | UTC instant | Must be after installation when supplied |
| installed_at | TIMESTAMPTZ | Yes | — | UTC instant | Required installation time |
| retired_at | TIMESTAMPTZ | No | — | UTC instant | Must be greater than or equal to installed_at |
| status | VARCHAR | Yes | — | ACTIVE, INACTIVE, MAINTENANCE, RETIRED | Must be an allowed value |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set at creation |

**Unit mapping:** TEMPERATURE → degC; HUMIDITY → pct_rh; OCCUPANCY → pct_capacity.

## 1.4 SensorReading

**Purpose:** Immutable observation produced by one sensor.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| reading_id | VARCHAR | Yes | PK | `RDG-<sensor-id>-<UTC timestamp>` | Deterministic from sensor ID and observed time |
| sensor_id | VARCHAR | Yes | FK → sensor | Existing sensor ID | Parent must exist |
| observed_at | TIMESTAMPTZ | Yes | UNIQUE with sensor_id | UTC instant | One reading per sensor per timestamp |
| reading_value | DECIMAL(12,4) | Yes | — | Unit defined by sensor | Humidity and occupancy must be 0–100%; temperature has no artificial universal bound |
| unit | VARCHAR | Yes | — | degC, pct_rh, pct_capacity | Must match parent sensor's unit |
| quality_status | VARCHAR | Yes | — | GOOD, SUSPECT, BAD | Must be an allowed value |
| ingested_at | TIMESTAMPTZ | Yes | — | UTC instant; default current time | Must be greater than or equal to observed_at unless an explicitly documented clock-skew case exists |
| source_sequence | BIGINT | No | — | Source sequence number | Non-negative when supplied |

A missing observation is **not** represented as a fabricated SensorReading. Missing intervals are derived from expected cadence and persisted as SensorHealthEvent records.

## 1.5 DoorEvent

**Purpose:** Discrete OPEN or CLOSE event for a door within a zone. Door identity is stable within the zone. The event is sourced from a door controller/event source; it is not modelled as an environmental SensorReading.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| door_event_id | VARCHAR | Yes | PK | Deterministic event ID | Immutable and unique |
| zone_id | VARCHAR | Yes | FK → zone | Existing zone ID | Parent must exist |
| door_id | VARCHAR | Yes | UNIQUE with zone, event_type and event_at | Stable door code | Non-empty |
| event_type | VARCHAR | Yes | — | OPEN, CLOSE | Must be an allowed value |
| event_at | TIMESTAMPTZ | Yes | UNIQUE with zone, door, type | UTC instant | Used to pair sequential OPEN/CLOSE events |
| source_system | VARCHAR | Yes | — | Default SYNTHETIC | Non-empty source identifier |
| ingested_at | TIMESTAMPTZ | Yes | — | UTC instant | Records ingestion time |
| notes | VARCHAR | No | — | Free text | Optional context |

**Door duration:** Pair an OPEN event with the next valid CLOSE event for the same zone and door. Duplicate OPEN events, CLOSE without OPEN, and unclosed doors are explicit exceptions; they are not silently discarded.

## 1.6 InventoryLot

**Purpose:** Product batch/lot stored in one or more zones over time.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| lot_id | VARCHAR | Yes | PK | `LOT-2026-000001` | Immutable and unique |
| product_code | VARCHAR | Yes | — | Product catalogue code | Non-empty |
| product_name | VARCHAR | Yes | — | Product name | Non-empty |
| product_category | VARCHAR | Yes | — | Synthetic category such as PHARMA, FROZEN_FOOD, DAIRY, PRODUCE | Must map to a documented product category |
| requirement_id | VARCHAR | Yes | FK → storage_requirement | Applicable requirement version | Parent must exist and product_code must match the requirement |
| quantity_received | DECIMAL(18,3) | Yes | — | quantity_unit | Greater than 0 |
| quantity_unit | VARCHAR | Yes | — | e.g. case, kg, pallet, litre | Non-empty; consistent with assignments |
| manufactured_at | TIMESTAMPTZ | No | — | UTC instant | Must be less than or equal to received_at |
| received_at | TIMESTAMPTZ | Yes | — | UTC instant | Required receipt time |
| expires_at | TIMESTAMPTZ | No | — | UTC instant | If supplied, later than received_at |
| status | VARCHAR | Yes | — | RECEIVED, STORED, QUARANTINED, RELEASED, EXPIRED, DISPOSED | Must follow documented lifecycle transitions |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set at creation |

A lot may have several LotZoneAssignment rows over time, or concurrent rows for a deliberately split quantity. The sum of concurrent assigned quantities must not exceed quantity_received.

## 1.7 StorageRequirement

**Purpose:** Versioned environmental requirements for a product. A lot references the exact requirement version applicable to it.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| requirement_id | VARCHAR | Yes | PK | Deterministic requirement/version ID | Immutable and unique |
| product_code | VARCHAR | Yes | UNIQUE with version | Product code | Non-empty |
| product_category | VARCHAR | Yes | — | Documented synthetic category | Must match the lot category |
| version | INTEGER | Yes | UNIQUE with product_code | Positive integer | Starts at 1; increment on revision |
| temperature_min_c | DECIMAL(7,3) | No | — | °C | Must be paired with temperature_max_c; min ≤ max |
| temperature_max_c | DECIMAL(7,3) | No | — | °C | Must be paired with temperature_min_c |
| humidity_min_pct | DECIMAL(6,3) | No | — | %RH | Must be paired with humidity_max_pct; 0–100 |
| humidity_max_pct | DECIMAL(6,3) | No | — | %RH | Must be paired with humidity_min_pct; 0–100 |
| effective_from | TIMESTAMPTZ | Yes | — | UTC instant | Start of applicability |
| effective_to | TIMESTAMPTZ | No | — | UTC instant | If supplied, later than effective_from |
| status | VARCHAR | Yes | — | ACTIVE, INACTIVE, SUPERSEDED | Must be an allowed value |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set at creation |

At least one complete environmental range (temperature or humidity) is required. Requirements are illustrative synthetic assumptions and are not represented as regulatory limits.

---

# 2. Supporting relationship and traceability tables

## 2.1 RequirementAllowedZoneType

**Purpose:** Normalized many-to-many-compatible list of zone types permitted by a storage requirement (one requirement can permit multiple zone types).

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| requirement_id | VARCHAR | Yes | PK part; FK → storage_requirement | Existing requirement ID | Parent must exist |
| zone_type | VARCHAR | Yes | PK part | FROZEN, COLD, CHILLED, AMBIENT, HOT, CONTROLLED | Unique per requirement |

At least one allowed zone type is required per active requirement; validate this with a grouped query.

## 2.2 SensorZoneAssignment

**Purpose:** Effective-dated mapping of sensors to zones.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| assignment_id | VARCHAR | Yes | PK | Deterministic assignment ID | Unique |
| sensor_id | VARCHAR | Yes | FK → sensor | Existing sensor ID | Parent must exist |
| zone_id | VARCHAR | Yes | FK → zone | Existing zone ID | Parent must exist |
| valid_from | TIMESTAMPTZ | Yes | UNIQUE with sensor_id, zone_id | UTC instant | Inclusive start |
| valid_to | TIMESTAMPTZ | No | — | UTC instant | Exclusive end; if present, later than valid_from |
| rationale | VARCHAR | No | — | Free text | Required for an overlapping multi-zone assignment |
| is_approved | BOOLEAN | Yes | — | TRUE / FALSE | If TRUE, rationale, approved_by and approved_at are required |
| approved_by | VARCHAR | No | — | Actor identifier | Required when approved |
| approved_at | TIMESTAMPTZ | No | — | UTC instant | Required when approved |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set at creation |

**Cross-row validation:** For each sensor, detect overlapping assignments to different zones. An overlap is valid only when explicitly approved with rationale. Non-overlapping assignments represent ordinary relocation history.

## 2.3 LotZoneAssignment

**Purpose:** Effective-dated lot placement and dwell-time history.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| assignment_id | VARCHAR | Yes | PK; also unique with lot_id | Deterministic assignment ID | Unique |
| lot_id | VARCHAR | Yes | FK → inventory_lot | Existing lot ID | Parent must exist |
| zone_id | VARCHAR | Yes | FK → zone | Existing zone ID | Parent must exist |
| assigned_quantity | DECIMAL(18,3) | Yes | — | Same unit as lot | Greater than 0 |
| quantity_unit | VARCHAR | Yes | — | Must match lot quantity_unit | Exact unit match |
| stored_from | TIMESTAMPTZ | Yes | UNIQUE with lot_id, zone_id | UTC instant | Inclusive start |
| stored_to | TIMESTAMPTZ | No | — | UTC instant | Exclusive end; if supplied, later than stored_from |
| assignment_reason | VARCHAR | Yes | — | INITIAL_STORAGE, RELOCATION, SPLIT, CONSOLIDATION, CORRECTION | Must be documented |
| status | VARCHAR | Yes | — | ACTIVE, ENDED, CANCELLED | ACTIVE requires NULL stored_to; ENDED/CANCELLED requires stored_to |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Set at creation |

**Cross-row validation:** Concurrent assignments for the same lot must not allocate more than the lot's received quantity. A lot/zone move must close the previous assignment before the new assignment starts unless it is an explicitly modelled split.

## 2.4 SensorHealthEvent

**Purpose:** Explicit sensor outage or drift incident; missing readings are converted into an outage event.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| health_event_id | VARCHAR | Yes | PK | Deterministic health event ID | Unique |
| sensor_id | VARCHAR | Yes | FK → sensor | Existing sensor ID | Parent must exist |
| event_type | VARCHAR | Yes | — | OUTAGE, DRIFT | Must be an allowed value |
| started_at | TIMESTAMPTZ | Yes | — | UTC instant | Incident start |
| ended_at | TIMESTAMPTZ | No | — | UTC instant | If present, later than started_at |
| status | VARCHAR | Yes | — | OPEN, RESOLVED | OPEN requires NULL ended_at; RESOLVED requires ended_at |
| expected_interval_seconds | INTEGER | No | — | Seconds | Positive when supplied |
| missed_interval_count | INTEGER | No | — | Count | Non-negative |
| details | VARCHAR | Yes | — | Explanation | Non-empty reason/evidence |
| detected_at | TIMESTAMPTZ | Yes | — | UTC instant | Detection timestamp |

## 2.5 AlarmEvidence

**Purpose:** Evidence ledger linking an alarm to the source rows that support it.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| evidence_id | VARCHAR | Yes | PK | Deterministic evidence ID | Unique |
| alarm_id | VARCHAR | Yes | FK → alarm | Existing alarm ID | Parent must exist |
| evidence_type | VARCHAR | Yes | — | SENSOR_READING, DOOR_EVENT, SENSOR_HEALTH_EVENT, SENSOR_ZONE_ASSIGNMENT, LOT_ZONE_ASSIGNMENT | Must match the populated evidence FK |
| sensor_reading_id | VARCHAR | No | FK → sensor_reading | Reading ID | Populated only for SENSOR_READING |
| door_event_id | VARCHAR | No | FK → door_event | Door event ID | Populated only for DOOR_EVENT |
| health_event_id | VARCHAR | No | FK → sensor_health_event | Health event ID | Populated only for SENSOR_HEALTH_EVENT |
| sensor_zone_assignment_id | VARCHAR | No | FK → sensor_zone_assignment | Assignment ID | Populated only for SENSOR_ZONE_ASSIGNMENT |
| lot_zone_assignment_id | VARCHAR | No | FK → lot_zone_assignment | Assignment ID | Populated only for LOT_ZONE_ASSIGNMENT |
| observed_at | TIMESTAMPTZ | Yes | — | UTC instant | Evidence event time |
| evidence_note | VARCHAR | No | — | Free text | Optional explanation |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Link creation time |

Exactly one source FK must be populated, and it must correspond to evidence_type. The SQL CHECK constraint enforces this.

## 2.6 AlarmInventoryImpact

**Purpose:** Many-to-many bridge linking a risk alarm to each inventory lot/zone assignment that overlaps its event interval.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| impact_id | VARCHAR | Yes | PK | Deterministic impact ID | Unique |
| alarm_id | VARCHAR | Yes | FK → alarm | Existing alarm ID | Parent must exist |
| lot_id | VARCHAR | Yes | Composite FK with lot_zone_assignment_id | Existing lot ID | Must match the referenced assignment's lot |
| lot_zone_assignment_id | VARCHAR | Yes | Composite FK with lot_id | Existing assignment ID | Must prove the lot was in the relevant zone |
| exposure_start_at | TIMESTAMPTZ | Yes | — | UTC instant | Start of overlap interval |
| exposure_end_at | TIMESTAMPTZ | No | — | UTC instant | NULL for ongoing exposure; otherwise later than start |
| exposure_duration_minutes | DECIMAL(14,3) | No | — | Minutes | Non-negative; NULL for ongoing interval |
| impact_status | VARCHAR | Yes | — | POTENTIALLY_AFFECTED, REQUIRES_INSPECTION, CONFIRMED_AFFECTED, CLEARED, QUARANTINED | Must be an allowed value |
| impact_reason | VARCHAR | Yes | — | Explanation | Must explain the overlap/risk basis |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Record creation time |

Unique constraint: one impact row per alarm and lot-zone assignment. The evaluator must detect when a real overlapping lot assignment is missing from this table.

## 2.7 AlarmStatusHistory

**Purpose:** Append-only record of each alarm transition.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| history_id | VARCHAR | Yes | PK | Deterministic history ID | Unique |
| alarm_id | VARCHAR | Yes | FK → alarm | Existing alarm ID | Parent must exist |
| previous_status | VARCHAR | No | — | NULL or alarm status enum | NULL only for initial status record |
| new_status | VARCHAR | Yes | — | OPEN, ACKNOWLEDGED, INVESTIGATING, RESOLVED, CLOSED | Must be allowed |
| changed_at | TIMESTAMPTZ | Yes | — | UTC instant | Must be ordered per alarm |
| changed_by | VARCHAR | Yes | — | User or system actor | Non-empty |
| reason | VARCHAR | Yes | — | Free text | Non-empty transition reason |

## 2.8 InspectionStatusHistory

Same structure as AlarmStatusHistory, with `inspection_id` as the FK and new_status allowed values: PENDING, IN_PROGRESS, COMPLETED, CANCELLED.

## 2.9 RemediationStatusHistory

Same structure as AlarmStatusHistory, with `remediation_id` as the FK and new_status allowed values: PLANNED, IN_PROGRESS, COMPLETED, CANCELLED.

## 2.10 RemediationVerificationHistory

**Purpose:** Append-only record of independent verification outcomes after a remediation is completed.

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| verification_id | VARCHAR | Yes | PK | Unique verification event ID | Immutable; unique |
| remediation_id | VARCHAR | Yes | FK → remediation | Existing remediation ID | Parent must exist |
| previous_verification_status | VARCHAR | Yes | — | PENDING, PASSED, FAILED, NOT_REQUIRED | Must match the prior operational state |
| new_verification_status | VARCHAR | Yes | — | PENDING, PASSED, FAILED, NOT_REQUIRED | Must be a permitted transition |
| verified_at | TIMESTAMPTZ | Yes | — | UTC instant | At or after remediation completion |
| verified_by | VARCHAR | Yes | — | Reviewer/actor identifier | Non-empty |
| reason | VARCHAR | Yes | — | Verification evidence or outcome | Non-empty |

A remediation must be COMPLETED before verification. The current verification state is maintained on the Remediation row; this table preserves each change as an append-only audit event.

---

# 3. Alarm, Inspection, and Remediation core fields

## 3.1 Alarm

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| alarm_id | VARCHAR | Yes | PK | `ALM-000001` | Immutable; unique |
| rule_id | VARCHAR | Yes | — | Versioned rule code | Non-empty; maps to documented rule catalogue |
| alarm_type | VARCHAR | Yes | — | TEMPERATURE_EXCURSION, HUMIDITY_EXCURSION, OCCUPANCY_LIMIT, DOOR_OPEN_TOO_LONG, SENSOR_OUTAGE, SENSOR_DRIFT, INCOMPATIBLE_STORAGE, INVALID_SENSOR_MAPPING, INCOMPLETE_IMPACT_ASSESSMENT | Must be allowed |
| severity | VARCHAR | Yes | — | LOW, MEDIUM, HIGH, CRITICAL | Assigned by documented rule |
| zone_id | VARCHAR | No | FK → zone | Existing zone ID | NULL for system-wide alarms |
| sensor_id | VARCHAR | No | FK → sensor | Existing sensor ID | NULL when not sensor-specific |
| detected_at | TIMESTAMPTZ | Yes | — | UTC instant | At or after event_start_at |
| event_start_at | TIMESTAMPTZ | Yes | — | UTC instant | Start of detected incident |
| event_end_at | TIMESTAMPTZ | No | — | UTC instant | If present, later than event_start_at |
| title | VARCHAR | Yes | — | Short summary | Non-empty |
| description | VARCHAR | Yes | — | Explainable reason/evidence summary | Non-empty |
| status | VARCHAR | Yes | — | OPEN, ACKNOWLEDGED, INVESTIGATING, RESOLVED, CLOSED | Must follow allowed state transitions |
| impact_assessment_status | VARCHAR | Yes | — | NOT_STARTED, IN_PROGRESS, COMPLETE, FAILED | COMPLETE only after lot-impact reconciliation |
| source_event_key | VARCHAR | No | UNIQUE | Stable deduplication key | Same source incident must not create duplicate alarms |
| assigned_to | VARCHAR | No | — | Actor identifier | Optional owner |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Creation time |
| updated_at | TIMESTAMPTZ | Yes | — | UTC instant | Greater than or equal to created_at |

## 3.2 Inspection

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| inspection_id | VARCHAR | Yes | PK | `INSP-000001` | Immutable; unique |
| alarm_id | VARCHAR | Yes | FK → alarm | Existing alarm ID | Every inspection is triggered by an alarm |
| lot_id | VARCHAR | No | FK → inventory_lot | Existing lot ID | NULL for facility/sensor-only inspections |
| inspection_type | VARCHAR | Yes | — | PRODUCT_QUALITY, SENSOR_HEALTH, FACILITY, STORAGE_COMPATIBILITY, DOOR_EVENT | Must be allowed |
| requested_at | TIMESTAMPTZ | Yes | — | UTC instant | Request time |
| due_at | TIMESTAMPTZ | No | — | UTC instant | At or after requested_at |
| started_at | TIMESTAMPTZ | No | — | UTC instant | At or after requested_at |
| completed_at | TIMESTAMPTZ | No | — | UTC instant | Requires started_at and must be at or after it |
| inspector | VARCHAR | No | — | Actor identifier | Optional until assigned |
| status | VARCHAR | Yes | — | PENDING, IN_PROGRESS, COMPLETED, CANCELLED | Must follow allowed transitions |
| findings | VARCHAR | No | — | Investigation result | Required when completed |
| product_disposition | VARCHAR | Yes | — | PENDING, RELEASED, QUARANTINED, REJECTED, RETEST_REQUIRED, NOT_APPLICABLE | Quality decision; not inferred solely from exposure |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Creation time |

## 3.3 Remediation

| Field | Type | Required? | Key / unique | Allowed values / unit | Validation |
|---|---|---:|---|---|---|
| remediation_id | VARCHAR | Yes | PK | `REM-000001` | Immutable; unique |
| inspection_id | VARCHAR | Yes | FK → inspection | Existing inspection ID | Every remediation arises from an inspection |
| lot_id | VARCHAR | No | FK → inventory_lot | Existing lot ID | NULL for facility/sensor remediation |
| action_type | VARCHAR | Yes | — | Documented action catalogue | Non-empty |
| description | VARCHAR | Yes | — | Corrective action description | Non-empty |
| assigned_to | VARCHAR | No | — | Actor identifier | Optional owner |
| status | VARCHAR | Yes | — | PLANNED, IN_PROGRESS, COMPLETED, CANCELLED | Must follow allowed transitions |
| started_at | TIMESTAMPTZ | No | — | UTC instant | If present, action start |
| completed_at | TIMESTAMPTZ | No | — | UTC instant | Requires started_at and must be at or after it |
| verification_status | VARCHAR | Yes | — | PENDING, PASSED, FAILED, NOT_REQUIRED | Verification outcome |
| verified_at | TIMESTAMPTZ | No | — | UTC instant | Requires completed_at |
| created_at | TIMESTAMPTZ | Yes | — | UTC instant | Creation time |

---

# 4. Ground-truth artifact (separate from operational schema)

Ground truth is **not** a DuckDB operational input table. It is exported separately under `data/ground_truth/` and read only by the independent evaluator.

Recommended fields:

| Field | Type | Required? | Validation |
|---|---|---:|---|
| scenario_id | VARCHAR | Yes | Unique deterministic scenario ID |
| issue_type | VARCHAR | Yes | Documented anomaly/risk category |
| entity_type | VARCHAR | Yes | Entity type under test |
| entity_id | VARCHAR | Yes | References the affected synthetic record ID |
| expected_decision | VARCHAR | Yes | Expected rule-engine result |
| expected_severity | VARCHAR | No | Expected severity when applicable |
| affected_zone_id | VARCHAR | No | Existing zone ID |
| event_start_at | TIMESTAMPTZ | No | UTC interval start |
| event_end_at | TIMESTAMPTZ | No | Greater than start when present |
| expected_affected_lot_ids | JSON / list | No | Expected set of impacted lot IDs |
| rationale | VARCHAR | Yes | Human-readable reason the scenario is positive/negative |
| created_by_seed | BIGINT | Yes | Fixed generator seed |

**Leakage prevention:** The risk engine must never read this artifact. It may use ordinary sensor, inventory, assignment, and event records only. The evaluator joins predicted results to ground truth after execution. Do not add labels, scenario identifiers, or expected outcomes to model input features.

---

# 5. Cross-row validation rules

The following conditions cannot be fully guaranteed by row-level SQL CHECK constraints and must be implemented in the validation module.

| Validation | Rule |
|---|---|
| Sensor-zone temporal overlap | A sensor may have multiple zone assignments only when the overlapping interval is approved and has a rationale |
| Reading-to-zone mapping | At each observed_at timestamp, the sensor should resolve to an applicable zone assignment |
| Reading unit/type | SensorReading.unit must equal Sensor.unit |
| Lot requirement match | InventoryLot.product_code and product_category must match its referenced StorageRequirement |
| Requirement effective date | Lot receipt/storage period must be covered by the referenced requirement version, or an explicit exception must be recorded |
| Allowed storage zone | The assigned zone_type must exist in RequirementAllowedZoneType for the lot's requirement |
| Lot quantity reconciliation | Concurrent assigned quantities for a lot must not exceed quantity_received |
| Lot assignment interval | Lot movement intervals must not overlap unexpectedly; documented split storage is allowed within quantity balance |
| Door pairing | OPEN/CLOSE events must pair by zone and door; unmatched events remain explicit exceptions |
| Sensor outage | Gap is measured from expected cadence and tolerance; missing interval is not considered normal |
| Alarm impact | Every alarm that overlaps a lot's zone assignment must have the corresponding impact row or a documented no-impact assessment |
| Impact interval | Impact interval equals the intersection of alarm interval and lot-zone assignment interval |
| Evidence consistency | Evidence source must belong to the alarm's zone/sensor context where applicable |
| Lifecycle transition | Only documented transitions are allowed; current status must equal the last history event |
| Remediation linkage | Lot-specific remediation should reference a lot in the related inspection/impact scope |
| Duplicate detection | Sensor + observed_at and zone + door + event_type + event_at are unique |
| Reproducibility | Same seed/config produces equal canonical row counts and checksums, excluding generated run metadata |

# 6. ID formats

| Entity | Example |
|---|---|
| Warehouse | `WH-001` |
| Zone | `ZONE-WH-001-001` |
| Sensor | `SNS-ZONE-WH-001-001-TEMP-01` |
| SensorReading | `RDG-SNS-ZONE-WH-001-001-TEMP-01-20261022T100000Z` |
| DoorEvent | `DRE-ZONE-WH-001-001-DOOR-01-20261022T100000Z-OPEN` |
| InventoryLot | `LOT-2026-000001` |
| StorageRequirement | `REQ-PROD-001-V01` |
| Alarm | `ALM-000001` |
| Inspection | `INSP-000001` |
| Remediation | `REM-000001` |

Supporting-table IDs use stable prefixes such as `SZA-` (sensor-zone assignment), `LZA-` (lot-zone assignment), `SHE-` (sensor health event), `EVD-` (evidence), `IMP-` (impact), and `HST-` (history). Counters are zero-padded. SensorReading IDs use UTC timestamps without punctuation.

## 7. Source and provenance

No external dataset is required for the initial submission. All generated records are synthetic and reproducible using the configured seed. If public reference data is introduced later, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and discarded fields in a source manifest. Do not include confidential or personal information.
