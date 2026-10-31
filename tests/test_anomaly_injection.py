from pathlib import Path
import csv
import json

import duckdb
import pytest

from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data
from coldwatch.generators.telemetry import generate_telemetry
from coldwatch.generators.anomaly_injector import (
    AnomalyInjectionError,
    inject_anomalies,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


def _seed_baseline(tmp_path: Path):
    database_path = tmp_path / "coldwatch.duckdb"
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        connection.close()

    data = build_master_data(load_settings(), profile="starter", seed=20261022)
    persist_master_data(database_path, data)
    generated_root = tmp_path / "generated"
    generate_telemetry(
        database_path=database_path,
        profile="starter",
        seed=20261022,
        output_root=generated_root,
    )
    return database_path, generated_root


def test_anomaly_injector_creates_separate_ground_truth_and_all_required_scenarios(tmp_path: Path):
    database_path, generated_root = _seed_baseline(tmp_path)
    truth_root = tmp_path / "ground_truth"
    report_root = tmp_path / "reports"

    manifest = inject_anomalies(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=truth_root,
        report_root=report_root,
    )

    expected_issues = {
        "TEMPERATURE_EXCURSION",
        "HUMIDITY_EXCURSION",
        "OCCUPANCY_LIMIT",
        "DOOR_OPEN_TOO_LONG",
        "SENSOR_OUTAGE",
        "SENSOR_DRIFT",
        "INCOMPATIBLE_LOT_ZONE",
        "SENSOR_MAPPED_TO_MULTIPLE_ZONES",
        "ALARM_NOT_LINKED_TO_AFFECTED_LOTS",
    }
    ground_truth_path = truth_root / "starter" / "ground_truth.csv"
    assert ground_truth_path.is_file()
    with ground_truth_path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    assert len(rows) == 9
    assert {row["issue_type"] for row in rows} == expected_issues
    assert len({row["scenario_id"] for row in rows}) == 9
    assert manifest["scenario_count"] == 9
    assert manifest["operational_audit_counts"]["sensor_reading_revisions"] == 20
    assert manifest["operational_audit_counts"]["sensor_reading_gaps"] == 2
    assert manifest["operational_audit_counts"]["sensor_health_events"] == 2
    assert manifest["operational_audit_counts"]["anomaly_door_events"] == 2
    assert manifest["operational_audit_counts"]["anomaly_sensor_zone_assignments"] == 1
    assert manifest["operational_audit_counts"]["anomaly_lot_zone_assignments"] == 1

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM sensor_reading_revision").fetchone()[0] == 20
        assert connection.execute("SELECT COUNT(*) FROM sensor_reading_gap").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM sensor_health_event").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM sensor_reading").fetchone()[0] == 9998
        assert connection.execute(
            "SELECT COUNT(*) FROM door_event WHERE source_system = 'ANOMALY_INJECTOR'"
        ).fetchone()[0] == 2

        missing_alarm = connection.execute(
            "SELECT COUNT(*) FROM alarm_inventory_impact WHERE alarm_id = 'ALM-ANOM-000001'"
        ).fetchone()[0]
        assert missing_alarm == 0

        incompatible = connection.execute(
            """
            SELECT COUNT(*)
            FROM lot_zone_assignment lza
            JOIN inventory_lot lot USING (lot_id)
            JOIN zone z USING (zone_id)
            WHERE lza.assignment_id = 'LZA-ANOM-000001'
              AND NOT EXISTS (
                  SELECT 1
                  FROM requirement_allowed_zone_type allowed
                  WHERE allowed.requirement_id = lot.requirement_id
                    AND allowed.zone_type = z.zone_type
              )
            """
        ).fetchone()[0]
        assert incompatible == 1

        mapping = connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_zone_assignment
            WHERE assignment_id = 'SZA-ANOM-000001'
              AND is_approved = FALSE
              AND rationale IS NULL
            """
        ).fetchone()[0]
        assert mapping == 1
    finally:
        connection.close()

    report = json.loads((report_root / "anomaly_manifest_starter.json").read_text())
    assert report["ground_truth_sha256"] == manifest["ground_truth_sha256"]


def test_anomaly_injection_refuses_a_second_run(tmp_path: Path):
    database_path, generated_root = _seed_baseline(tmp_path)
    args = {
        "database_path": database_path,
        "profile": "starter",
        "generated_root": generated_root,
        "ground_truth_root": tmp_path / "ground_truth",
        "report_root": tmp_path / "reports",
    }
    inject_anomalies(**args)
    with pytest.raises(AnomalyInjectionError, match="already contains anomaly records"):
        inject_anomalies(**args)


def test_anomaly_injection_refuses_missing_telemetry_summary(tmp_path: Path):
    database_path, _ = _seed_baseline(tmp_path)
    with pytest.raises(FileNotFoundError, match="Telemetry summary not found"):
        inject_anomalies(
            database_path=database_path,
            profile="starter",
            generated_root=tmp_path / "not-generated",
            ground_truth_root=tmp_path / "ground_truth",
            report_root=tmp_path / "reports",
        )


def test_ground_truth_is_not_an_operational_duckdb_table(tmp_path: Path):
    database_path, generated_root = _seed_baseline(tmp_path)
    inject_anomalies(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=tmp_path / "ground_truth",
        report_root=tmp_path / "reports",
    )
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert "ground_truth" not in tables
