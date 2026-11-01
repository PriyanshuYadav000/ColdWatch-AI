from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb

from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data
from coldwatch.generators.telemetry import generate_telemetry
from coldwatch.generators.anomaly_injector import inject_anomalies
from coldwatch.engine.risk_engine import detect_risks, run_risk_engine


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"

EXPECTED_ISSUES = {
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


def _seed_and_inject(tmp_path: Path):
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
    ground_truth_root = tmp_path / "ground_truth"
    report_root = tmp_path / "reports"
    inject_anomalies(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=ground_truth_root,
        report_root=report_root,
    )
    return database_path, report_root


def test_risk_engine_detects_all_controlled_issue_types_without_ground_truth(tmp_path: Path):
    database_path, _ = _seed_and_inject(tmp_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        findings = detect_risks(connection, rules=load_settings()["rules"])
    finally:
        connection.close()

    detected_issues = {finding["issue_type"] for finding in findings}
    assert detected_issues == EXPECTED_ISSUES
    assert all(finding["evidence"] for finding in findings)
    assert all(finding["source_event_key"] for finding in findings)

    impact_finding = next(
        item for item in findings
        if item["issue_type"] == "ALARM_NOT_LINKED_TO_AFFECTED_LOTS"
    )
    assert impact_finding["existing_alarm_id"] == "ALM-ANOM-000001"
    assert impact_finding["affected_lot_ids"]

    mapping_finding = next(
        item for item in findings
        if item["issue_type"] == "SENSOR_MAPPED_TO_MULTIPLE_ZONES"
    )
    # The mapping anomaly must not reuse a sensor that carries another injected
    # anomaly, otherwise the detector cannot isolate the individual scenarios.
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        affected_sensor_ids = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT r.sensor_id
                FROM sensor_reading_revision rev
                JOIN sensor_reading r USING (reading_id)
                UNION
                SELECT sensor_id FROM sensor_health_event
                """
            ).fetchall()
        }
    finally:
        connection.close()
    assert mapping_finding["entity_id"] not in affected_sensor_ids


def test_risk_engine_persists_traceable_alarms_and_is_idempotent(tmp_path: Path):
    database_path, report_root = _seed_and_inject(tmp_path)
    report_path = report_root / "risk_engine_starter.json"

    first = run_risk_engine(
        database_path=database_path,
        profile="starter",
        report_path=report_path,
        persist=True,
    )

    assert first["ground_truth_used"] is False
    assert set(first["findings_by_issue_type"]) == EXPECTED_ISSUES
    assert first["persistence"]["alarms_created"] > 0
    assert first["persistence"]["evidence_rows_created"] > 0
    assert first["persistence"]["inventory_impact_rows_created"] > 0
    assert first["persistence"]["negative_control_findings_not_modified"] == 1
    assert report_path.is_file()

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        negative_control_links = connection.execute(
            """
            SELECT COUNT(*) FROM alarm_inventory_impact
            WHERE alarm_id = 'ALM-ANOM-000001'
            """
        ).fetchone()[0]
        assert negative_control_links == 0

        created_alarm_ids = first["persistence"]["alarm_ids_created"]
        assert created_alarm_ids
        for alarm_id in created_alarm_ids:
            assert connection.execute(
                "SELECT COUNT(*) FROM alarm_status_history WHERE alarm_id = ?",
                [alarm_id],
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM alarm_evidence WHERE alarm_id = ?",
                [alarm_id],
            ).fetchone()[0] > 0
    finally:
        connection.close()

    second = run_risk_engine(
        database_path=database_path,
        profile="starter",
        report_path=report_path,
        persist=True,
    )
    assert second["persistence"]["alarms_created"] == 0


def test_zone_event_findings_link_all_overlapping_inventory_lots(tmp_path: Path):
    database_path, _report_root = _seed_and_inject(tmp_path)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        findings = detect_risks(connection, rules=load_settings()["rules"])
    finally:
        connection.close()

    ground_truth_path = tmp_path / "ground_truth" / "starter" / "ground_truth.csv"
    with ground_truth_path.open(newline="", encoding="utf-8") as file:
        scenarios = list(csv.DictReader(file))

    findings_by_identity = {
        (finding["issue_type"], finding["entity_id"]): finding
        for finding in findings
    }
    for scenario in scenarios:
        identity = (scenario["issue_type"], scenario["entity_id"])
        assert identity in findings_by_identity, f"Missing finding for {identity}"
        expected_lots = set(json.loads(scenario["expected_affected_lot_ids"]))
        detected_lots = set(findings_by_identity[identity]["affected_lot_ids"])
        assert detected_lots == expected_lots, (
            f"Inventory links differ for {identity}: "
            f"expected {sorted(expected_lots)}, detected {sorted(detected_lots)}"
        )

    # The intentionally incomplete negative-control alarm is detected but its
    # missing AlarmInventoryImpact links are never repaired by the detector.
    negative_control = findings_by_identity[
        ("ALARM_NOT_LINKED_TO_AFFECTED_LOTS", "ALM-ANOM-000001")
    ]
    assert negative_control["affected_lot_ids"]
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM alarm_inventory_impact WHERE alarm_id = 'ALM-ANOM-000001'"
        ).fetchone()[0] == 0
    finally:
        connection.close()
