"""Transactional alarm, inspection, and remediation workflows.

Services validate allowed lifecycle transitions and append immutable history
records in the same DuckDB transaction as the operational state update.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"

ALARM_TRANSITIONS = {
    "OPEN": {"ACKNOWLEDGED", "INVESTIGATING"},
    "ACKNOWLEDGED": {"INVESTIGATING"},
    "INVESTIGATING": {"ACKNOWLEDGED", "RESOLVED"},
    "RESOLVED": {"CLOSED", "INVESTIGATING"},
    "CLOSED": set(),
}
INSPECTION_TRANSITIONS = {
    "PENDING": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
REMEDIATION_TRANSITIONS = {
    "PLANNED": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
INSPECTION_TYPES = {
    "PRODUCT_QUALITY",
    "SENSOR_HEALTH",
    "FACILITY",
    "STORAGE_COMPATIBILITY",
    "DOOR_EVENT",
}
PRODUCT_DISPOSITIONS = {
    "PENDING",
    "RELEASED",
    "QUARANTINED",
    "REJECTED",
    "RETEST_REQUIRED",
    "NOT_APPLICABLE",
}
VERIFICATION_TRANSITIONS = {
    "PENDING": {"PASSED", "FAILED", "NOT_REQUIRED"},
    "FAILED": {"PASSED", "NOT_REQUIRED"},
    "PASSED": set(),
    "NOT_REQUIRED": set(),
}


class WorkflowError(ValueError):
    """Raised when an operational workflow transition is invalid."""


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise WorkflowError("Workflow timestamps must include a timezone.")
    return current.astimezone(timezone.utc)


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20].upper()}"


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{field} must be a non-empty string.")
    return value.strip()


def _connect(database_path: str | Path) -> duckdb.DuckDBPyConnection:
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"DuckDB database not found: {path}")
    return duckdb.connect(str(path))


def _fetch_one(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: list[Any],
    entity_label: str,
) -> tuple[Any, ...]:
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise WorkflowError(f"{entity_label} not found.")
    return row


def transition_alarm(
    *,
    alarm_id: str,
    new_status: str,
    changed_by: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE,
    assigned_to: str | None = None,
    changed_at: datetime | None = None,
) -> dict[str, Any]:
    """Transition an alarm and append status history atomically.

    A FAILED impact assessment may be acknowledged and investigated, but it
    cannot be resolved until the impact assessment has been completed.
    """
    alarm_id = _required_text(alarm_id, "alarm_id")
    changed_by = _required_text(changed_by, "changed_by")
    reason = _required_text(reason, "reason")
    new_status = _required_text(new_status, "new_status").upper()
    actor_time = _utc(changed_at)
    if assigned_to is not None:
        assigned_to = _required_text(assigned_to, "assigned_to")

    connection = _connect(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        previous, impact_status = _fetch_one(
            connection,
            "SELECT status, impact_assessment_status FROM alarm WHERE alarm_id = ?",
            [alarm_id],
            f"Alarm {alarm_id}",
        )
        if new_status not in ALARM_TRANSITIONS.get(previous, set()):
            raise WorkflowError(f"Alarm transition {previous} -> {new_status} is not allowed.")
        if new_status == "RESOLVED" and impact_status != "COMPLETE":
            raise WorkflowError(
                "An alarm cannot be resolved until its inventory impact assessment is COMPLETE."
            )

        connection.execute(
            """
            UPDATE alarm
            SET status = ?, assigned_to = COALESCE(?, assigned_to), updated_at = ?
            WHERE alarm_id = ?
            """,
            [new_status, assigned_to, actor_time, alarm_id],
        )
        connection.execute(
            """
            INSERT INTO alarm_status_history (
                history_id, alarm_id, previous_status, new_status,
                changed_at, changed_by, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [_identifier("HST-ALM"), alarm_id, previous, new_status, actor_time, changed_by, reason],
        )
        connection.execute("COMMIT")
        return {
            "alarm_id": alarm_id,
            "previous_status": previous,
            "new_status": new_status,
            "assigned_to": assigned_to,
            "changed_at": actor_time.isoformat(),
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def create_inspection(
    *,
    alarm_id: str,
    inspection_type: str,
    requested_by: str,
    database_path: str | Path = DEFAULT_DATABASE,
    lot_id: str | None = None,
    inspector: str | None = None,
    requested_at: datetime | None = None,
    due_at: datetime | None = None,
) -> dict[str, Any]:
    """Create a PENDING inspection and its initial history record."""
    alarm_id = _required_text(alarm_id, "alarm_id")
    requested_by = _required_text(requested_by, "requested_by")
    inspection_type = _required_text(inspection_type, "inspection_type").upper()
    if inspection_type not in INSPECTION_TYPES:
        raise WorkflowError(f"Unsupported inspection_type: {inspection_type}")
    if lot_id is not None:
        lot_id = _required_text(lot_id, "lot_id")
    if inspector is not None:
        inspector = _required_text(inspector, "inspector")

    requested = _utc(requested_at)
    due = _utc(due_at) if due_at else None
    if due is not None and due < requested:
        raise WorkflowError("due_at cannot be earlier than requested_at.")

    inspection_id = _identifier("INSP")
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        alarm_status = _fetch_one(
            connection,
            "SELECT status FROM alarm WHERE alarm_id = ?",
            [alarm_id],
            f"Alarm {alarm_id}",
        )[0]
        if alarm_status in {"RESOLVED", "CLOSED"}:
            raise WorkflowError("An inspection cannot be opened for a resolved or closed alarm.")

        if lot_id is not None:
            _fetch_one(
                connection,
                "SELECT lot_id FROM inventory_lot WHERE lot_id = ?",
                [lot_id],
                f"Inventory lot {lot_id}",
            )
            linked = connection.execute(
                "SELECT COUNT(*) FROM alarm_inventory_impact WHERE alarm_id = ? AND lot_id = ?",
                [alarm_id, lot_id],
            ).fetchone()[0]
            if linked == 0:
                raise WorkflowError("The selected lot is not linked to this alarm's inventory impact.")

        connection.execute(
            """
            INSERT INTO inspection (
                inspection_id, alarm_id, lot_id, inspection_type,
                requested_at, due_at, inspector, status, product_disposition, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 'PENDING', ?)
            """,
            [inspection_id, alarm_id, lot_id, inspection_type, requested, due, inspector, requested],
        )
        connection.execute(
            """
            INSERT INTO inspection_status_history (
                history_id, inspection_id, previous_status, new_status,
                changed_at, changed_by, reason
            )
            VALUES (?, ?, NULL, 'PENDING', ?, ?, ?)
            """,
            [
                _identifier("HST-INSP"), inspection_id, requested,
                requested_by, "Inspection created and queued.",
            ],
        )
        connection.execute("COMMIT")
        return {
            "inspection_id": inspection_id,
            "alarm_id": alarm_id,
            "lot_id": lot_id,
            "inspection_type": inspection_type,
            "status": "PENDING",
            "requested_at": requested.isoformat(),
            "due_at": due.isoformat() if due else None,
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def transition_inspection(
    *,
    inspection_id: str,
    new_status: str,
    changed_by: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE,
    findings: str | None = None,
    product_disposition: str | None = None,
    inspector: str | None = None,
    changed_at: datetime | None = None,
) -> dict[str, Any]:
    """Advance or cancel an inspection with completion evidence and history."""
    inspection_id = _required_text(inspection_id, "inspection_id")
    changed_by = _required_text(changed_by, "changed_by")
    reason = _required_text(reason, "reason")
    new_status = _required_text(new_status, "new_status").upper()
    actor_time = _utc(changed_at)
    if findings is not None:
        findings = _required_text(findings, "findings")
    if inspector is not None:
        inspector = _required_text(inspector, "inspector")
    disposition = _required_text(product_disposition, "product_disposition").upper() if product_disposition is not None else None
    if disposition is not None and disposition not in PRODUCT_DISPOSITIONS:
        raise WorkflowError(f"Unsupported product_disposition: {disposition}")

    connection = _connect(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        row = _fetch_one(
            connection,
            """
            SELECT status, requested_at, started_at, completed_at, inspection_type, product_disposition
            FROM inspection WHERE inspection_id = ?
            """,
            [inspection_id],
            f"Inspection {inspection_id}",
        )
        previous, requested_at, started_at, completed_at, inspection_type, previous_disposition = row
        if new_status not in INSPECTION_TRANSITIONS.get(previous, set()):
            raise WorkflowError(f"Inspection transition {previous} -> {new_status} is not allowed.")

        new_started_at = started_at
        new_completed_at = completed_at
        new_findings = findings
        new_disposition = disposition or previous_disposition
        if new_status == "IN_PROGRESS":
            if actor_time < _utc(requested_at):
                raise WorkflowError("started_at cannot be earlier than requested_at.")
            new_started_at = actor_time
        elif new_status == "COMPLETED":
            if started_at is None:
                raise WorkflowError("An inspection must be started before it can be completed.")
            if findings is None:
                raise WorkflowError("Completion requires non-empty findings.")
            if disposition is None:
                raise WorkflowError("Completion requires an explicit product_disposition.")
            if actor_time < _utc(started_at):
                raise WorkflowError("completed_at cannot be earlier than started_at.")
            if inspection_type == "PRODUCT_QUALITY" and disposition not in {"RELEASED", "QUARANTINED", "REJECTED", "RETEST_REQUIRED"}:
                raise WorkflowError("PRODUCT_QUALITY inspections require a final product disposition.")
            if inspection_type != "PRODUCT_QUALITY" and disposition not in {"NOT_APPLICABLE", "RELEASED", "QUARANTINED", "RETEST_REQUIRED"}:
                raise WorkflowError("The selected disposition is not valid for this inspection type.")
            new_completed_at = actor_time
            new_findings = findings

        connection.execute(
            """
            UPDATE inspection
            SET status = ?, started_at = ?, completed_at = ?, findings = ?,
                product_disposition = ?, inspector = COALESCE(?, inspector)
            WHERE inspection_id = ?
            """,
            [
                new_status, new_started_at, new_completed_at, new_findings,
                new_disposition, inspector, inspection_id,
            ],
        )
        connection.execute(
            """
            INSERT INTO inspection_status_history (
                history_id, inspection_id, previous_status, new_status,
                changed_at, changed_by, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [_identifier("HST-INSP"), inspection_id, previous, new_status, actor_time, changed_by, reason],
        )
        connection.execute("COMMIT")
        return {
            "inspection_id": inspection_id,
            "previous_status": previous,
            "new_status": new_status,
            "product_disposition": new_disposition,
            "changed_at": actor_time.isoformat(),
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def create_remediation(
    *,
    inspection_id: str,
    action_type: str,
    description: str,
    created_by: str,
    database_path: str | Path = DEFAULT_DATABASE,
    lot_id: str | None = None,
    assigned_to: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Create a PLANNED remediation for a completed inspection."""
    inspection_id = _required_text(inspection_id, "inspection_id")
    action_type = _required_text(action_type, "action_type")
    description = _required_text(description, "description")
    created_by = _required_text(created_by, "created_by")
    if lot_id is not None:
        lot_id = _required_text(lot_id, "lot_id")
    if assigned_to is not None:
        assigned_to = _required_text(assigned_to, "assigned_to")
    created = _utc(created_at)
    remediation_id = _identifier("REMED")

    connection = _connect(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        alarm_id, inspection_lot_id, inspection_status = _fetch_one(
            connection,
            "SELECT alarm_id, lot_id, status FROM inspection WHERE inspection_id = ?",
            [inspection_id],
            f"Inspection {inspection_id}",
        )
        if inspection_status != "COMPLETED":
            raise WorkflowError("Remediation can only be created after its inspection is COMPLETED.")
        if inspection_lot_id is not None and lot_id not in {None, inspection_lot_id}:
            raise WorkflowError("Remediation lot_id must match the inspection lot_id.")
        selected_lot = lot_id or inspection_lot_id
        if selected_lot is not None:
            _fetch_one(
                connection,
                "SELECT lot_id FROM inventory_lot WHERE lot_id = ?",
                [selected_lot],
                f"Inventory lot {selected_lot}",
            )
            linked = connection.execute(
                "SELECT COUNT(*) FROM alarm_inventory_impact WHERE alarm_id = ? AND lot_id = ?",
                [alarm_id, selected_lot],
            ).fetchone()[0]
            if linked == 0:
                raise WorkflowError("The remediation lot is not linked to the inspection alarm.")
        connection.execute(
            """
            INSERT INTO remediation (
                remediation_id, inspection_id, lot_id, action_type, description,
                assigned_to, status, verification_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'PLANNED', 'PENDING', ?)
            """,
            [remediation_id, inspection_id, selected_lot, action_type, description, assigned_to, created],
        )
        connection.execute(
            """
            INSERT INTO remediation_status_history (
                history_id, remediation_id, previous_status, new_status,
                changed_at, changed_by, reason
            )
            VALUES (?, ?, NULL, 'PLANNED', ?, ?, ?)
            """,
            [
                _identifier("HST-REM"), remediation_id, created,
                created_by, "Remediation plan created.",
            ],
        )
        connection.execute("COMMIT")
        return {
            "remediation_id": remediation_id,
            "inspection_id": inspection_id,
            "lot_id": selected_lot,
            "status": "PLANNED",
            "verification_status": "PENDING",
            "created_at": created.isoformat(),
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def transition_remediation(
    *,
    remediation_id: str,
    new_status: str,
    changed_by: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE,
    changed_at: datetime | None = None,
) -> dict[str, Any]:
    """Start, complete, or cancel a remediation with lifecycle history."""
    remediation_id = _required_text(remediation_id, "remediation_id")
    changed_by = _required_text(changed_by, "changed_by")
    reason = _required_text(reason, "reason")
    new_status = _required_text(new_status, "new_status").upper()
    actor_time = _utc(changed_at)

    connection = _connect(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        previous, created_at, started_at, completed_at = _fetch_one(
            connection,
            """
            SELECT status, created_at, started_at, completed_at
            FROM remediation WHERE remediation_id = ?
            """,
            [remediation_id],
            f"Remediation {remediation_id}",
        )
        if new_status not in REMEDIATION_TRANSITIONS.get(previous, set()):
            raise WorkflowError(f"Remediation transition {previous} -> {new_status} is not allowed.")
        if new_status == "IN_PROGRESS" and actor_time < _utc(created_at):
            raise WorkflowError("started_at cannot be earlier than remediation creation.")
        new_started_at = actor_time if new_status == "IN_PROGRESS" else started_at
        new_completed_at = completed_at
        if new_status == "COMPLETED":
            if started_at is None:
                raise WorkflowError("A remediation must be started before it can be completed.")
            if actor_time < _utc(started_at):
                raise WorkflowError("completed_at cannot be earlier than started_at.")
            new_completed_at = actor_time

        connection.execute(
            """
            UPDATE remediation
            SET status = ?, started_at = ?, completed_at = ?
            WHERE remediation_id = ?
            """,
            [new_status, new_started_at, new_completed_at, remediation_id],
        )
        connection.execute(
            """
            INSERT INTO remediation_status_history (
                history_id, remediation_id, previous_status, new_status,
                changed_at, changed_by, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [_identifier("HST-REM"), remediation_id, previous, new_status, actor_time, changed_by, reason],
        )
        connection.execute("COMMIT")
        return {
            "remediation_id": remediation_id,
            "previous_status": previous,
            "new_status": new_status,
            "changed_at": actor_time.isoformat(),
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def verify_remediation(
    *,
    remediation_id: str,
    new_verification_status: str,
    verified_by: str,
    reason: str,
    database_path: str | Path = DEFAULT_DATABASE,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    """Record an independent remediation verification outcome and history."""
    remediation_id = _required_text(remediation_id, "remediation_id")
    verified_by = _required_text(verified_by, "verified_by")
    reason = _required_text(reason, "reason")
    new_status = _required_text(new_verification_status, "new_verification_status").upper()
    verified = _utc(verified_at)

    connection = _connect(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        status, previous, completed_at = _fetch_one(
            connection,
            "SELECT status, verification_status, completed_at FROM remediation WHERE remediation_id = ?",
            [remediation_id],
            f"Remediation {remediation_id}",
        )
        if status != "COMPLETED":
            raise WorkflowError("A remediation must be COMPLETED before verification.")
        if new_status not in VERIFICATION_TRANSITIONS.get(previous, set()):
            raise WorkflowError(f"Verification transition {previous} -> {new_status} is not allowed.")
        if completed_at is None or verified < _utc(completed_at):
            raise WorkflowError("verified_at cannot be earlier than remediation completion.")

        connection.execute(
            """
            UPDATE remediation
            SET verification_status = ?, verified_at = ?
            WHERE remediation_id = ?
            """,
            [new_status, verified, remediation_id],
        )
        connection.execute(
            """
            INSERT INTO remediation_verification_history (
                verification_id, remediation_id,
                previous_verification_status, new_verification_status,
                verified_at, verified_by, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                _identifier("VER"), remediation_id, previous, new_status,
                verified, verified_by, reason,
            ],
        )
        connection.execute("COMMIT")
        return {
            "remediation_id": remediation_id,
            "previous_verification_status": previous,
            "new_verification_status": new_status,
            "verified_at": verified.isoformat(),
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()
