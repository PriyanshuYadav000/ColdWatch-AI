from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data
from coldwatch.generators.telemetry import generate_telemetry
from coldwatch.generators.anomaly_injector import inject_anomalies
from coldwatch.engine.risk_engine import run_risk_engine
from coldwatch.workflows.services import (
    WorkflowError,
    create_inspection,
    create_remediation,
    transition_alarm,
    transition_inspection,
    transition_remediation,
    verify_remediation,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


def _seed_and_run_engine(tmp_path: Path):
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
    return database_path, risk_report


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_alarm_lifecycle_requires_impact_completion_and_records_history(tmp_path: Path):
    database_path, risk_report = _seed_and_run_engine(tmp_path)
    alarm_id = risk_report["persistence"]["alarm_ids_created"][0]
    now = _now()

    first = transition_alarm(
        alarm_id=alarm_id,
        new_status="ACKNOWLEDGED",
        changed_by="warehouse.operator",
        reason="Operator reviewed the alert.",
        database_path=database_path,
        assigned_to="quality.lead",
        changed_at=now,
    )
    assert first["new_status"] == "ACKNOWLEDGED"

    investigating = transition_alarm(
        alarm_id=alarm_id,
        new_status="INVESTIGATING",
        changed_by="quality.lead",
        reason="Investigation started.",
        database_path=database_path,
        changed_at=now + timedelta(minutes=1),
    )
    assert investigating["new_status"] == "INVESTIGATING"

    resolved = transition_alarm(
        alarm_id=alarm_id,
        new_status="RESOLVED",
        changed_by="quality.lead",
        reason="Inventory impact was reviewed and actions are complete.",
        database_path=database_path,
        changed_at=now + timedelta(minutes=2),
    )
    assert resolved["new_status"] == "RESOLVED"

    closed = transition_alarm(
        alarm_id=alarm_id,
        new_status="CLOSED",
        changed_by="warehouse.manager",
        reason="Closure approved.",
        database_path=database_path,
        changed_at=now + timedelta(minutes=3),
    )
    assert closed["new_status"] == "CLOSED"

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM alarm_status_history WHERE alarm_id = ?",
            [alarm_id],
        ).fetchone()[0] == 5  # Initial OPEN + four transitions.
    finally:
        connection.close()

    with pytest.raises(WorkflowError, match="not allowed"):
        transition_alarm(
            alarm_id=alarm_id,
            new_status="INVESTIGATING",
            changed_by="warehouse.operator",
            reason="Attempt to reopen a closed alarm.",
            database_path=database_path,
        )


def test_failed_impact_alarm_cannot_be_resolved(tmp_path: Path):
    database_path, _ = _seed_and_run_engine(tmp_path)

    with pytest.raises(WorkflowError, match="impact assessment is COMPLETE"):
        transition_alarm(
            alarm_id="ALM-ANOM-000001",
            new_status="RESOLVED",
            changed_by="quality.lead",
            reason="Attempted resolution before impact review.",
            database_path=database_path,
        )

    result = transition_alarm(
        alarm_id="ALM-ANOM-000001",
        new_status="ACKNOWLEDGED",
        changed_by="quality.lead",
        reason="Negative-control alarm acknowledged for investigation.",
        database_path=database_path,
    )
    assert result["new_status"] == "ACKNOWLEDGED"


def test_inspection_and_remediation_lifecycle_with_verification_history(tmp_path: Path):
    database_path, risk_report = _seed_and_run_engine(tmp_path)
    alarm_id = risk_report["persistence"]["alarm_ids_created"][0]

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        lot_id = connection.execute(
            """
            SELECT lot_id
            FROM alarm_inventory_impact
            WHERE alarm_id = ?
            ORDER BY lot_id
            LIMIT 1
            """,
            [alarm_id],
        ).fetchone()[0]
    finally:
        connection.close()

    requested = _now()
    inspection = create_inspection(
        alarm_id=alarm_id,
        inspection_type="PRODUCT_QUALITY",
        requested_by="quality.coordinator",
        database_path=database_path,
        lot_id=lot_id,
        inspector="quality.inspector",
        requested_at=requested,
        due_at=requested + timedelta(hours=4),
    )
    inspection_id = inspection["inspection_id"]
    assert inspection["status"] == "PENDING"

    with pytest.raises(WorkflowError, match="only be created after"):
        create_remediation(
            inspection_id=inspection_id,
            action_type="RELOCATE_LOT",
            description="Move the lot to compliant storage.",
            created_by="warehouse.manager",
            database_path=database_path,
            lot_id=lot_id,
        )

    started = transition_inspection(
        inspection_id=inspection_id,
        new_status="IN_PROGRESS",
        changed_by="quality.inspector",
        reason="Physical inspection started.",
        database_path=database_path,
        inspector="quality.inspector",
        changed_at=requested + timedelta(minutes=5),
    )
    assert started["new_status"] == "IN_PROGRESS"

    completed = transition_inspection(
        inspection_id=inspection_id,
        new_status="COMPLETED",
        changed_by="quality.inspector",
        reason="Inspection findings recorded.",
        database_path=database_path,
        findings="Packaging intact; lot retained under quarantine pending relocation.",
        product_disposition="QUARANTINED",
        changed_at=requested + timedelta(minutes=20),
    )
    assert completed["product_disposition"] == "QUARANTINED"

    remediation = create_remediation(
        inspection_id=inspection_id,
        action_type="RELOCATE_AND_REVERIFY",
        description="Move the quarantined lot to an approved zone and verify conditions.",
        created_by="warehouse.manager",
        database_path=database_path,
        lot_id=lot_id,
        assigned_to="warehouse.operator",
        created_at=requested + timedelta(minutes=21),
    )
    remediation_id = remediation["remediation_id"]

    with pytest.raises(WorkflowError, match="must be started"):
        transition_remediation(
            remediation_id=remediation_id,
            new_status="COMPLETED",
            changed_by="warehouse.operator",
            reason="Cannot complete before work starts.",
            database_path=database_path,
            changed_at=requested + timedelta(minutes=25),
        )

    with pytest.raises(WorkflowError, match="must be COMPLETED"):
        verify_remediation(
            remediation_id=remediation_id,
            new_verification_status="PASSED",
            verified_by="quality.inspector",
            reason="Premature verification attempt.",
            database_path=database_path,
        )

    transition_remediation(
        remediation_id=remediation_id,
        new_status="IN_PROGRESS",
        changed_by="warehouse.operator",
        reason="Relocation work started.",
        database_path=database_path,
        changed_at=requested + timedelta(minutes=30),
    )
    transition_remediation(
        remediation_id=remediation_id,
        new_status="COMPLETED",
        changed_by="warehouse.operator",
        reason="Relocation completed.",
        database_path=database_path,
        changed_at=requested + timedelta(minutes=60),
    )
    failed_verification = verify_remediation(
        remediation_id=remediation_id,
        new_verification_status="FAILED",
        verified_by="quality.inspector",
        reason="Initial check found the destination temperature outside the target.",
        database_path=database_path,
        verified_at=requested + timedelta(minutes=70),
    )
    assert failed_verification["new_verification_status"] == "FAILED"

    passed_verification = verify_remediation(
        remediation_id=remediation_id,
        new_verification_status="PASSED",
        verified_by="quality.inspector",
        reason="Corrective action completed and storage conditions now comply.",
        database_path=database_path,
        verified_at=requested + timedelta(minutes=90),
    )
    assert passed_verification["new_verification_status"] == "PASSED"

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM inspection_status_history WHERE inspection_id = ?",
            [inspection_id],
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM remediation_status_history WHERE remediation_id = ?",
            [remediation_id],
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM remediation_verification_history WHERE remediation_id = ?",
            [remediation_id],
        ).fetchone()[0] == 2
        final_state = connection.execute(
            """
            SELECT status, verification_status
            FROM remediation WHERE remediation_id = ?
            """,
            [remediation_id],
        ).fetchone()
        assert final_state == ("COMPLETED", "PASSED")
    finally:
        connection.close()


def test_inspection_requires_explicit_completion_evidence(tmp_path: Path):
    database_path, risk_report = _seed_and_run_engine(tmp_path)
    alarm_id = risk_report["persistence"]["alarm_ids_created"][0]
    created = create_inspection(
        alarm_id=alarm_id,
        inspection_type="SENSOR_HEALTH",
        requested_by="operations",
        database_path=database_path,
    )
    inspection_id = created["inspection_id"]
    transition_inspection(
        inspection_id=inspection_id,
        new_status="IN_PROGRESS",
        changed_by="technician",
        reason="Sensor inspection started.",
        database_path=database_path,
    )
    with pytest.raises(WorkflowError, match="findings"):
        transition_inspection(
            inspection_id=inspection_id,
            new_status="COMPLETED",
            changed_by="technician",
            reason="Missing inspection evidence.",
            database_path=database_path,
            product_disposition="NOT_APPLICABLE",
        )
