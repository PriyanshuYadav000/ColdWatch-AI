from __future__ import annotations

from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from coldwatch.api.app import create_app
from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data
from coldwatch.generators.telemetry import generate_telemetry
from coldwatch.generators.anomaly_injector import inject_anomalies
from coldwatch.engine.risk_engine import run_risk_engine


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


def _build_api_client(tmp_path: Path):
    database_path = tmp_path / "coldwatch-api.duckdb"
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
    report_root = tmp_path / "reports"
    inject_anomalies(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=tmp_path / "ground_truth",
        report_root=report_root,
    )
    risk_report = run_risk_engine(
        database_path=database_path,
        profile="starter",
        report_path=report_root / "risk_engine_starter.json",
        persist=True,
    )
    client = TestClient(create_app(database_path))
    return client, database_path, risk_report


def test_api_health_summary_alarm_list_detail_and_impacts(tmp_path: Path):
    client, _database_path, risk_report = _build_api_client(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database_readable"] is True

    summary = client.get("/api/v1/summary")
    assert summary.status_code == 200
    assert summary.json()["counts"]["warehouses"] == 2
    assert summary.json()["counts"]["zones"] == 10
    assert summary.json()["counts"]["alarms"] > 0

    alarms = client.get("/api/v1/alarms", params={"status": "OPEN", "severity": "HIGH"})
    assert alarms.status_code == 200
    assert alarms.json()["total"] > 0
    assert alarms.json()["items"]

    alarm_id = risk_report["persistence"]["alarm_ids_created"][0]
    detail = client.get(f"/api/v1/alarms/{alarm_id}")
    assert detail.status_code == 200
    assert detail.json()["alarm_id"] == alarm_id
    assert detail.json()["evidence"]
    assert "status_history" in detail.json()

    impacts = client.get(f"/api/v1/alarms/{alarm_id}/impacts")
    assert impacts.status_code == 200
    assert impacts.json()["total"] > 0
    assert impacts.json()["items"][0]["product_code"]

    missing = client.get("/api/v1/alarms/ALM-DOES-NOT-EXIST")
    assert missing.status_code == 404


def test_api_drives_alarm_inspection_and_remediation_lifecycle(tmp_path: Path):
    client, database_path, risk_report = _build_api_client(tmp_path)
    alarm_id = risk_report["persistence"]["alarm_ids_created"][0]

    # A direct OPEN -> RESOLVED transition is rejected by the workflow contract.
    invalid = client.post(
        f"/api/v1/alarms/{alarm_id}/transitions",
        json={
            "new_status": "RESOLVED",
            "changed_by": "quality.lead",
            "reason": "Attempted premature resolution.",
        },
    )
    assert invalid.status_code == 409

    acknowledged = client.post(
        f"/api/v1/alarms/{alarm_id}/transitions",
        json={
            "new_status": "ACKNOWLEDGED",
            "changed_by": "warehouse.operator",
            "reason": "Alarm reviewed and assigned.",
            "assigned_to": "quality.lead",
        },
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["result"]["new_status"] == "ACKNOWLEDGED"

    investigating = client.post(
        f"/api/v1/alarms/{alarm_id}/transitions",
        json={
            "new_status": "INVESTIGATING",
            "changed_by": "quality.lead",
            "reason": "Investigation started.",
        },
    )
    assert investigating.status_code == 200

    impact_response = client.get(f"/api/v1/alarms/{alarm_id}/impacts")
    lot_id = impact_response.json()["items"][0]["lot_id"]
    created_inspection = client.post(
        f"/api/v1/alarms/{alarm_id}/inspections",
        json={
            "inspection_type": "PRODUCT_QUALITY",
            "requested_by": "quality.coordinator",
            "lot_id": lot_id,
            "inspector": "quality.inspector",
        },
    )
    assert created_inspection.status_code == 201
    inspection_id = created_inspection.json()["result"]["inspection_id"]

    started_inspection = client.post(
        f"/api/v1/inspections/{inspection_id}/transitions",
        json={
            "new_status": "IN_PROGRESS",
            "changed_by": "quality.inspector",
            "reason": "Physical inspection started.",
        },
    )
    assert started_inspection.status_code == 200

    completed_inspection = client.post(
        f"/api/v1/inspections/{inspection_id}/transitions",
        json={
            "new_status": "COMPLETED",
            "changed_by": "quality.inspector",
            "reason": "Inspection completed with documented findings.",
            "findings": "Packaging intact; lot retained under quarantine pending relocation.",
            "product_disposition": "QUARANTINED",
        },
    )
    assert completed_inspection.status_code == 200
    assert completed_inspection.json()["result"]["product_disposition"] == "QUARANTINED"

    created_remediation = client.post(
        f"/api/v1/inspections/{inspection_id}/remediations",
        json={
            "action_type": "RELOCATE_AND_REVERIFY",
            "description": "Move the quarantined lot to an approved zone and verify conditions.",
            "created_by": "warehouse.manager",
            "lot_id": lot_id,
            "assigned_to": "warehouse.operator",
        },
    )
    assert created_remediation.status_code == 201
    remediation_id = created_remediation.json()["result"]["remediation_id"]

    started_remediation = client.post(
        f"/api/v1/remediations/{remediation_id}/transitions",
        json={
            "new_status": "IN_PROGRESS",
            "changed_by": "warehouse.operator",
            "reason": "Relocation started.",
        },
    )
    assert started_remediation.status_code == 200

    completed_remediation = client.post(
        f"/api/v1/remediations/{remediation_id}/transitions",
        json={
            "new_status": "COMPLETED",
            "changed_by": "warehouse.operator",
            "reason": "Relocation completed.",
        },
    )
    assert completed_remediation.status_code == 200

    verified = client.post(
        f"/api/v1/remediations/{remediation_id}/verification",
        json={
            "new_verification_status": "PASSED",
            "verified_by": "quality.inspector",
            "reason": "Follow-up check confirms storage conditions comply.",
        },
    )
    assert verified.status_code == 200
    assert verified.json()["result"]["new_verification_status"] == "PASSED"

    remediation_detail = client.get(f"/api/v1/remediations/{remediation_id}")
    assert remediation_detail.status_code == 200
    assert remediation_detail.json()["verification_history"]
    assert remediation_detail.json()["status_history"]

    inspection_detail = client.get(f"/api/v1/inspections/{inspection_id}")
    assert inspection_detail.status_code == 200
    assert len(inspection_detail.json()["status_history"]) == 3

    # Resolution is allowed only after inventory impact is marked COMPLETE.
    resolved = client.post(
        f"/api/v1/alarms/{alarm_id}/transitions",
        json={
            "new_status": "RESOLVED",
            "changed_by": "quality.lead",
            "reason": "Inventory impact reviewed and corrective action verified.",
        },
    )
    assert resolved.status_code == 200

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM remediation_verification_history WHERE remediation_id = ?",
            [remediation_id],
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_api_rejects_extra_request_fields_and_unavailable_database(tmp_path: Path):
    client, _database_path, risk_report = _build_api_client(tmp_path)
    alarm_id = risk_report["persistence"]["alarm_ids_created"][0]

    invalid = client.post(
        f"/api/v1/alarms/{alarm_id}/transitions",
        json={
            "new_status": "ACKNOWLEDGED",
            "changed_by": "operator",
            "reason": "Review alert.",
            "unexpected": "not allowed",
        },
    )
    assert invalid.status_code == 422

    unavailable = TestClient(create_app(tmp_path / "missing.duckdb"))
    response = unavailable.get("/health")
    assert response.status_code == 503
