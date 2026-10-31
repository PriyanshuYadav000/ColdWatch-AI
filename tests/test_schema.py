from pathlib import Path

import duckdb
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"

EXPECTED_TABLES = {
    "warehouse",
    "zone",
    "sensor",
    "sensor_reading",
    "door_event",
    "inventory_lot",
    "storage_requirement",
    "requirement_allowed_zone_type",
    "sensor_zone_assignment",
    "lot_zone_assignment",
    "sensor_health_event",
    "alarm",
    "alarm_evidence",
    "alarm_inventory_impact",
    "inspection",
    "remediation",
    "alarm_status_history",
    "inspection_status_history",
    "remediation_status_history",
}


@pytest.fixture
def connection():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield con
    finally:
        con.close()


def test_schema_creates_all_required_and_supporting_tables(connection):
    actual = {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            """
        ).fetchall()
    }
    assert EXPECTED_TABLES.issubset(actual)


def test_schema_can_be_applied_twice(connection):
    # CREATE TABLE IF NOT EXISTS makes local setup repeatable.
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_sensor_reading_rejects_unknown_sensor_foreign_key(connection):
    with pytest.raises(duckdb.ConstraintException):
        connection.execute(
            """
            INSERT INTO sensor_reading
                (reading_id, sensor_id, observed_at, reading_value, unit)
            VALUES
                ('RDG-SNS-MISSING-20261022T100000Z', 'SNS-MISSING',
                 TIMESTAMPTZ '2026-10-22 10:00:00+00', 5.0, 'degC')
            """
        )


def test_storage_requirement_rejects_unpaired_temperature_bounds(connection):
    with pytest.raises(duckdb.ConstraintException):
        connection.execute(
            """
            INSERT INTO storage_requirement
                (requirement_id, product_code, product_category, version,
                 temperature_min_c, effective_from)
            VALUES
                ('REQ-INVALID-V01', 'PROD-INVALID', 'PHARMA', 1,
                 2.0, TIMESTAMPTZ '2026-10-22 00:00:00+00')
            """
        )


def test_alarm_evidence_requires_exactly_one_source_reference(connection):
    connection.execute(
        """
        INSERT INTO alarm
            (alarm_id, rule_id, alarm_type, severity, detected_at,
             event_start_at, title, description)
        VALUES
            ('ALM-TEST-001', 'TEMP_HIGH_V1', 'TEMPERATURE_EXCURSION',
             'HIGH', TIMESTAMPTZ '2026-10-22 10:05:00+00',
             TIMESTAMPTZ '2026-10-22 10:00:00+00',
             'Test excursion', 'Evidence constraint test')
        """
    )
    with pytest.raises(duckdb.ConstraintException):
        connection.execute(
            """
            INSERT INTO alarm_evidence
                (evidence_id, alarm_id, evidence_type, observed_at)
            VALUES
                ('EVD-INVALID-001', 'ALM-TEST-001', 'SENSOR_READING',
                 TIMESTAMPTZ '2026-10-22 10:00:00+00')
            """
        )
