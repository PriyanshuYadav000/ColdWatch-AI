-- ColdWatch AI relational schema
-- Target engine: DuckDB
-- All timestamps are TIMESTAMPTZ and are stored in UTC.
-- Apply this file to an empty or existing DuckDB database.
-- Cross-row temporal overlap and lifecycle transition rules are validated by Python.

CREATE TABLE IF NOT EXISTS warehouse (
    warehouse_id VARCHAR PRIMARY KEY,
    site_code VARCHAR NOT NULL UNIQUE,
    name VARCHAR NOT NULL,
    address_line VARCHAR,
    city VARCHAR NOT NULL,
    region VARCHAR,
    country VARCHAR NOT NULL,
    timezone VARCHAR NOT NULL DEFAULT 'UTC',
    status VARCHAR NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zone (
    zone_id VARCHAR PRIMARY KEY,
    warehouse_id VARCHAR NOT NULL REFERENCES warehouse(warehouse_id),
    name VARCHAR NOT NULL,
    zone_type VARCHAR NOT NULL
        CHECK (zone_type IN ('FROZEN', 'COLD', 'CHILLED', 'AMBIENT', 'HOT', 'CONTROLLED')),
    area_sqm DECIMAL(12, 2)
        CHECK (area_sqm IS NULL OR area_sqm > 0),
    occupancy_warning_limit_pct DECIMAL(5, 2) NOT NULL DEFAULT 90.00
        CHECK (occupancy_warning_limit_pct BETWEEN 0 AND 100),
    status VARCHAR NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE', 'BLOCKED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (warehouse_id, name)
);

CREATE TABLE IF NOT EXISTS sensor (
    sensor_id VARCHAR PRIMARY KEY,
    sensor_type VARCHAR NOT NULL
        CHECK (sensor_type IN ('TEMPERATURE', 'HUMIDITY', 'OCCUPANCY')),
    unit VARCHAR NOT NULL
        CHECK (unit IN ('degC', 'pct_rh', 'pct_capacity')),
    manufacturer VARCHAR,
    model VARCHAR,
    serial_number VARCHAR UNIQUE,
    expected_interval_seconds INTEGER NOT NULL DEFAULT 300
        CHECK (expected_interval_seconds > 0),
    calibration_due_at TIMESTAMPTZ,
    installed_at TIMESTAMPTZ NOT NULL,
    retired_at TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE', 'RETIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (retired_at IS NULL OR retired_at >= installed_at),
    CHECK (
        (sensor_type = 'TEMPERATURE' AND unit = 'degC') OR
        (sensor_type = 'HUMIDITY' AND unit = 'pct_rh') OR
        (sensor_type = 'OCCUPANCY' AND unit = 'pct_capacity')
    )
);

CREATE TABLE IF NOT EXISTS storage_requirement (
    requirement_id VARCHAR PRIMARY KEY,
    product_code VARCHAR NOT NULL,
    product_category VARCHAR NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    temperature_min_c DECIMAL(7, 3),
    temperature_max_c DECIMAL(7, 3),
    humidity_min_pct DECIMAL(6, 3),
    humidity_max_pct DECIMAL(6, 3),
    effective_from TIMESTAMPTZ NOT NULL,
    effective_to TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUPERSEDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_code, version),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (
        (temperature_min_c IS NULL AND temperature_max_c IS NULL) OR
        (temperature_min_c IS NOT NULL AND temperature_max_c IS NOT NULL
            AND temperature_min_c <= temperature_max_c)
    ),
    CHECK (
        (humidity_min_pct IS NULL AND humidity_max_pct IS NULL) OR
        (humidity_min_pct IS NOT NULL AND humidity_max_pct IS NOT NULL
            AND humidity_min_pct <= humidity_max_pct)
    ),
    CHECK (temperature_min_c IS NOT NULL OR humidity_min_pct IS NOT NULL),
    CHECK (humidity_min_pct IS NULL OR (humidity_min_pct >= 0 AND humidity_max_pct <= 100))
);

CREATE TABLE IF NOT EXISTS requirement_allowed_zone_type (
    requirement_id VARCHAR NOT NULL REFERENCES storage_requirement(requirement_id),
    zone_type VARCHAR NOT NULL
        CHECK (zone_type IN ('FROZEN', 'COLD', 'CHILLED', 'AMBIENT', 'HOT', 'CONTROLLED')),
    PRIMARY KEY (requirement_id, zone_type)
);

CREATE TABLE IF NOT EXISTS sensor_zone_assignment (
    assignment_id VARCHAR PRIMARY KEY,
    sensor_id VARCHAR NOT NULL REFERENCES sensor(sensor_id),
    zone_id VARCHAR NOT NULL REFERENCES zone(zone_id),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    rationale VARCHAR,
    is_approved BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by VARCHAR,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (sensor_id, zone_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (
        (is_approved = FALSE) OR
        (is_approved = TRUE AND rationale IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS sensor_reading (
    reading_id VARCHAR PRIMARY KEY,
    sensor_id VARCHAR NOT NULL REFERENCES sensor(sensor_id),
    observed_at TIMESTAMPTZ NOT NULL,
    reading_value DECIMAL(12, 4) NOT NULL,
    unit VARCHAR NOT NULL CHECK (unit IN ('degC', 'pct_rh', 'pct_capacity')),
    quality_status VARCHAR NOT NULL DEFAULT 'GOOD'
        CHECK (quality_status IN ('GOOD', 'SUSPECT', 'BAD')),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_sequence BIGINT,
    UNIQUE (sensor_id, observed_at),
    CHECK (
        (unit = 'pct_rh' AND reading_value BETWEEN 0 AND 100) OR
        (unit = 'pct_capacity' AND reading_value BETWEEN 0 AND 100) OR
        unit = 'degC'
    )
);

CREATE TABLE IF NOT EXISTS door_event (
    door_event_id VARCHAR PRIMARY KEY,
    zone_id VARCHAR NOT NULL REFERENCES zone(zone_id),
    door_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL CHECK (event_type IN ('OPEN', 'CLOSE')),
    event_at TIMESTAMPTZ NOT NULL,
    source_sensor_id VARCHAR REFERENCES sensor(sensor_id),
    source_system VARCHAR NOT NULL DEFAULT 'SYNTHETIC',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes VARCHAR,
    UNIQUE (zone_id, door_id, event_type, event_at)
);

CREATE TABLE IF NOT EXISTS inventory_lot (
    lot_id VARCHAR PRIMARY KEY,
    product_code VARCHAR NOT NULL,
    product_name VARCHAR NOT NULL,
    product_category VARCHAR NOT NULL,
    requirement_id VARCHAR NOT NULL REFERENCES storage_requirement(requirement_id),
    quantity_received DECIMAL(18, 3) NOT NULL CHECK (quantity_received > 0),
    quantity_unit VARCHAR NOT NULL,
    manufactured_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'RECEIVED'
        CHECK (status IN ('RECEIVED', 'STORED', 'QUARANTINED', 'RELEASED', 'EXPIRED', 'DISPOSED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (manufactured_at IS NULL OR manufactured_at <= received_at),
    CHECK (expires_at IS NULL OR expires_at > received_at)
);

CREATE TABLE IF NOT EXISTS lot_zone_assignment (
    assignment_id VARCHAR PRIMARY KEY,
    lot_id VARCHAR NOT NULL REFERENCES inventory_lot(lot_id),
    zone_id VARCHAR NOT NULL REFERENCES zone(zone_id),
    assigned_quantity DECIMAL(18, 3) NOT NULL CHECK (assigned_quantity > 0),
    quantity_unit VARCHAR NOT NULL,
    stored_from TIMESTAMPTZ NOT NULL,
    stored_to TIMESTAMPTZ,
    assignment_reason VARCHAR NOT NULL DEFAULT 'INITIAL_STORAGE',
    status VARCHAR NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'ENDED', 'CANCELLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (lot_id, zone_id, stored_from),
    UNIQUE (assignment_id, lot_id),
    CHECK (stored_to IS NULL OR stored_to > stored_from),
    CHECK ((status = 'ACTIVE' AND stored_to IS NULL) OR status <> 'ACTIVE')
);

CREATE TABLE IF NOT EXISTS sensor_health_event (
    health_event_id VARCHAR PRIMARY KEY,
    sensor_id VARCHAR NOT NULL REFERENCES sensor(sensor_id),
    event_type VARCHAR NOT NULL CHECK (event_type IN ('OUTAGE', 'DRIFT')),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    expected_interval_seconds INTEGER,
    missed_interval_count INTEGER CHECK (missed_interval_count IS NULL OR missed_interval_count >= 0),
    details VARCHAR NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (ended_at IS NULL OR ended_at > started_at),
    CHECK ((status = 'OPEN' AND ended_at IS NULL) OR (status = 'RESOLVED' AND ended_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS alarm (
    alarm_id VARCHAR PRIMARY KEY,
    rule_id VARCHAR NOT NULL,
    alarm_type VARCHAR NOT NULL CHECK (
        alarm_type IN (
            'TEMPERATURE_EXCURSION',
            'HUMIDITY_EXCURSION',
            'OCCUPANCY_LIMIT',
            'DOOR_OPEN_TOO_LONG',
            'SENSOR_OUTAGE',
            'SENSOR_DRIFT',
            'INCOMPATIBLE_STORAGE',
            'INVALID_SENSOR_MAPPING',
            'INCOMPLETE_IMPACT_ASSESSMENT'
        )
    ),
    severity VARCHAR NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    zone_id VARCHAR REFERENCES zone(zone_id),
    sensor_id VARCHAR REFERENCES sensor(sensor_id),
    detected_at TIMESTAMPTZ NOT NULL,
    event_start_at TIMESTAMPTZ NOT NULL,
    event_end_at TIMESTAMPTZ,
    title VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'INVESTIGATING', 'RESOLVED', 'CLOSED')),
    impact_assessment_status VARCHAR NOT NULL DEFAULT 'NOT_STARTED'
        CHECK (impact_assessment_status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETE', 'FAILED')),
    source_event_key VARCHAR UNIQUE,
    assigned_to VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (event_end_at IS NULL OR event_end_at > event_start_at),
    CHECK (detected_at >= event_start_at)
);

CREATE TABLE IF NOT EXISTS alarm_evidence (
    evidence_id VARCHAR PRIMARY KEY,
    alarm_id VARCHAR NOT NULL REFERENCES alarm(alarm_id),
    evidence_type VARCHAR NOT NULL CHECK (
        evidence_type IN (
            'SENSOR_READING',
            'DOOR_EVENT',
            'SENSOR_HEALTH_EVENT',
            'SENSOR_ZONE_ASSIGNMENT',
            'LOT_ZONE_ASSIGNMENT'
        )
    ),
    sensor_reading_id VARCHAR REFERENCES sensor_reading(reading_id),
    door_event_id VARCHAR REFERENCES door_event(door_event_id),
    health_event_id VARCHAR REFERENCES sensor_health_event(health_event_id),
    sensor_zone_assignment_id VARCHAR REFERENCES sensor_zone_assignment(assignment_id),
    lot_zone_assignment_id VARCHAR REFERENCES lot_zone_assignment(assignment_id),
    observed_at TIMESTAMPTZ NOT NULL,
    evidence_note VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (evidence_type = 'SENSOR_READING'
            AND sensor_reading_id IS NOT NULL AND door_event_id IS NULL
            AND health_event_id IS NULL AND sensor_zone_assignment_id IS NULL
            AND lot_zone_assignment_id IS NULL) OR
        (evidence_type = 'DOOR_EVENT'
            AND sensor_reading_id IS NULL AND door_event_id IS NOT NULL
            AND health_event_id IS NULL AND sensor_zone_assignment_id IS NULL
            AND lot_zone_assignment_id IS NULL) OR
        (evidence_type = 'SENSOR_HEALTH_EVENT'
            AND sensor_reading_id IS NULL AND door_event_id IS NULL
            AND health_event_id IS NOT NULL AND sensor_zone_assignment_id IS NULL
            AND lot_zone_assignment_id IS NULL) OR
        (evidence_type = 'SENSOR_ZONE_ASSIGNMENT'
            AND sensor_reading_id IS NULL AND door_event_id IS NULL
            AND health_event_id IS NULL AND sensor_zone_assignment_id IS NOT NULL
            AND lot_zone_assignment_id IS NULL) OR
        (evidence_type = 'LOT_ZONE_ASSIGNMENT'
            AND sensor_reading_id IS NULL AND door_event_id IS NULL
            AND health_event_id IS NULL AND sensor_zone_assignment_id IS NULL
            AND lot_zone_assignment_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS alarm_inventory_impact (
    impact_id VARCHAR PRIMARY KEY,
    alarm_id VARCHAR NOT NULL REFERENCES alarm(alarm_id),
    lot_id VARCHAR NOT NULL,
    lot_zone_assignment_id VARCHAR NOT NULL,
    exposure_start_at TIMESTAMPTZ NOT NULL,
    exposure_end_at TIMESTAMPTZ,
    exposure_duration_minutes DECIMAL(14, 3),
    impact_status VARCHAR NOT NULL DEFAULT 'POTENTIALLY_AFFECTED'
        CHECK (impact_status IN ('POTENTIALLY_AFFECTED', 'REQUIRES_INSPECTION', 'CONFIRMED_AFFECTED', 'CLEARED', 'QUARANTINED')),
    impact_reason VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lot_zone_assignment_id, lot_id)
        REFERENCES lot_zone_assignment(assignment_id, lot_id),
    UNIQUE (alarm_id, lot_zone_assignment_id),
    CHECK (exposure_end_at IS NULL OR exposure_end_at > exposure_start_at),
    CHECK (exposure_duration_minutes IS NULL OR exposure_duration_minutes >= 0)
);

CREATE TABLE IF NOT EXISTS inspection (
    inspection_id VARCHAR PRIMARY KEY,
    alarm_id VARCHAR NOT NULL REFERENCES alarm(alarm_id),
    lot_id VARCHAR REFERENCES inventory_lot(lot_id),
    inspection_type VARCHAR NOT NULL
        CHECK (inspection_type IN ('PRODUCT_QUALITY', 'SENSOR_HEALTH', 'FACILITY', 'STORAGE_COMPATIBILITY', 'DOOR_EVENT')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    due_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    inspector VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    findings VARCHAR,
    product_disposition VARCHAR NOT NULL DEFAULT 'PENDING'
        CHECK (product_disposition IN ('PENDING', 'RELEASED', 'QUARANTINED', 'REJECTED', 'RETEST_REQUIRED', 'NOT_APPLICABLE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (due_at IS NULL OR due_at >= requested_at),
    CHECK (started_at IS NULL OR started_at >= requested_at),
    CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
);

CREATE TABLE IF NOT EXISTS remediation (
    remediation_id VARCHAR PRIMARY KEY,
    inspection_id VARCHAR NOT NULL REFERENCES inspection(inspection_id),
    lot_id VARCHAR REFERENCES inventory_lot(lot_id),
    action_type VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    assigned_to VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'PLANNED'
        CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    verification_status VARCHAR NOT NULL DEFAULT 'PENDING'
        CHECK (verification_status IN ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')),
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)),
    CHECK (verified_at IS NULL OR completed_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS alarm_status_history (
    history_id VARCHAR PRIMARY KEY,
    alarm_id VARCHAR NOT NULL REFERENCES alarm(alarm_id),
    previous_status VARCHAR,
    new_status VARCHAR NOT NULL
        CHECK (new_status IN ('OPEN', 'ACKNOWLEDGED', 'INVESTIGATING', 'RESOLVED', 'CLOSED')),
    changed_at TIMESTAMPTZ NOT NULL,
    changed_by VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    CHECK (previous_status IS NULL OR previous_status IN ('OPEN', 'ACKNOWLEDGED', 'INVESTIGATING', 'RESOLVED', 'CLOSED')),
    CHECK (previous_status IS NULL OR previous_status <> new_status)
);

CREATE TABLE IF NOT EXISTS inspection_status_history (
    history_id VARCHAR PRIMARY KEY,
    inspection_id VARCHAR NOT NULL REFERENCES inspection(inspection_id),
    previous_status VARCHAR,
    new_status VARCHAR NOT NULL
        CHECK (new_status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    changed_at TIMESTAMPTZ NOT NULL,
    changed_by VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    CHECK (previous_status IS NULL OR previous_status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    CHECK (previous_status IS NULL OR previous_status <> new_status)
);

CREATE TABLE IF NOT EXISTS remediation_status_history (
    history_id VARCHAR PRIMARY KEY,
    remediation_id VARCHAR NOT NULL REFERENCES remediation(remediation_id),
    previous_status VARCHAR,
    new_status VARCHAR NOT NULL
        CHECK (new_status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    changed_at TIMESTAMPTZ NOT NULL,
    changed_by VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    CHECK (previous_status IS NULL OR previous_status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    CHECK (previous_status IS NULL OR previous_status <> new_status)
);

-- Deliberately not created here: ground truth is a separate evaluation artifact,
-- not an operational input table. See docs/erd.md and docs/data_dictionary.md.
