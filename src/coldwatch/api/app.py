"""FastAPI application exposing ColdWatch operational data and workflows."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from coldwatch.workflows.services import (
    WorkflowError,
    create_inspection,
    create_remediation,
    transition_alarm,
    transition_inspection,
    transition_remediation,
    verify_remediation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_API_DATABASE = PROJECT_ROOT / "data" / "coldwatch-risk-engine.duckdb"

AlarmStatus = Literal["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "CLOSED"]
InspectionStatus = Literal["PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
RemediationStatus = Literal["PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
InspectionType = Literal[
    "PRODUCT_QUALITY",
    "SENSOR_HEALTH",
    "FACILITY",
    "STORAGE_COMPATIBILITY",
    "DOOR_EVENT",
]
ProductDisposition = Literal[
    "PENDING",
    "RELEASED",
    "QUARANTINED",
    "REJECTED",
    "RETEST_REQUIRED",
    "NOT_APPLICABLE",
]
VerificationStatus = Literal["PENDING", "PASSED", "FAILED", "NOT_REQUIRED"]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AlarmTransitionBody(APIModel):
    new_status: AlarmStatus
    changed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=2000)
    assigned_to: str | None = Field(default=None, max_length=120)


class InspectionCreateBody(APIModel):
    inspection_type: InspectionType
    requested_by: str = Field(min_length=1, max_length=120)
    lot_id: str | None = Field(default=None, max_length=80)
    inspector: str | None = Field(default=None, max_length=120)
    requested_at: datetime | None = None
    due_at: datetime | None = None


class InspectionTransitionBody(APIModel):
    new_status: InspectionStatus
    changed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=2000)
    findings: str | None = Field(default=None, max_length=5000)
    product_disposition: ProductDisposition | None = None
    inspector: str | None = Field(default=None, max_length=120)


class RemediationCreateBody(APIModel):
    action_type: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=5, max_length=3000)
    created_by: str = Field(min_length=1, max_length=120)
    lot_id: str | None = Field(default=None, max_length=80)
    assigned_to: str | None = Field(default=None, max_length=120)


class RemediationTransitionBody(APIModel):
    new_status: RemediationStatus
    changed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=2000)


class RemediationVerificationBody(APIModel):
    new_verification_status: VerificationStatus
    verified_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=2000)


def _database_path(request: Request) -> Path:
    return request.app.state.database_path


def _read_connection(request: Request) -> duckdb.DuckDBPyConnection:
    path = _database_path(request)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Operational database is not available: {path}",
        )
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not open operational database: {exc}",
        ) from exc


def _rows(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: list[Any] | None = None,
) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, parameters or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _one_or_404(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: list[Any],
    message: str,
) -> dict[str, Any]:
    rows = _rows(connection, sql, parameters)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return rows[0]


def _workflow_call(function, **kwargs: Any) -> dict[str, Any]:
    try:
        return function(**kwargs)
    except WorkflowError as exc:
        message = str(exc)
        code = status.HTTP_404_NOT_FOUND if message.endswith("not found.") else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=message) from exc
    except (duckdb.Error, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Create an application instance; tests can inject an isolated DuckDB path."""
    selected_path = Path(
        database_path
        or os.getenv("COLDWATCH_DB_PATH", str(DEFAULT_API_DATABASE))
    ).expanduser()

    application = FastAPI(
        title="ColdWatch AI API",
        summary="Warehouse environmental monitoring and operational risk workflows.",
        description=(
            "Local prototype API for alarms, evidence, inventory impacts, inspections, "
            "and remediation. Business decisions are produced by the deterministic rule engine."
        ),
        version="0.1.0",
    )
    application.state.database_path = selected_path
    router = APIRouter(prefix="/api/v1")

    @application.get("/health", tags=["system"])
    def health(request: Request) -> dict[str, Any]:
        path = _database_path(request)
        if not path.is_file():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Database file not found: {path}",
            )
        connection = _read_connection(request)
        try:
            connection.execute("SELECT 1").fetchone()
            tables = connection.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchone()[0]
        except duckdb.Error as exc:
            raise HTTPException(status_code=503, detail=f"Database health check failed: {exc}") from exc
        finally:
            connection.close()
        return {
            "status": "ok",
            "database": str(path),
            "database_readable": True,
            "tables_available": int(tables),
        }

    @router.get("/summary", tags=["system"])
    def summary(request: Request) -> dict[str, Any]:
        connection = _read_connection(request)
        try:
            counts = {
                "warehouses": int(connection.execute("SELECT COUNT(*) FROM warehouse").fetchone()[0]),
                "zones": int(connection.execute("SELECT COUNT(*) FROM zone").fetchone()[0]),
                "sensors": int(connection.execute("SELECT COUNT(*) FROM sensor").fetchone()[0]),
                "sensor_readings": int(connection.execute("SELECT COUNT(*) FROM sensor_reading").fetchone()[0]),
                "inventory_lots": int(connection.execute("SELECT COUNT(*) FROM inventory_lot").fetchone()[0]),
                "alarms": int(connection.execute("SELECT COUNT(*) FROM alarm").fetchone()[0]),
                "inspections": int(connection.execute("SELECT COUNT(*) FROM inspection").fetchone()[0]),
                "remediations": int(connection.execute("SELECT COUNT(*) FROM remediation").fetchone()[0]),
            }
            alarm_status_counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM alarm GROUP BY status ORDER BY status"
                ).fetchall()
            }
            severity_counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    "SELECT severity, COUNT(*) FROM alarm GROUP BY severity ORDER BY severity"
                ).fetchall()
            }
            impact_counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    "SELECT impact_status, COUNT(*) FROM alarm_inventory_impact GROUP BY impact_status ORDER BY impact_status"
                ).fetchall()
            }
            return {
                "database": str(_database_path(request)),
                "counts": counts,
                "alarms_by_status": alarm_status_counts,
                "alarms_by_severity": severity_counts,
                "inventory_impacts_by_status": impact_counts,
            }
        finally:
            connection.close()

    @router.get("/alarms", tags=["alarms"])
    def list_alarms(
        request: Request,
        alarm_status: AlarmStatus | None = Query(default=None, alias="status"),
        severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None,
        alarm_type: str | None = Query(default=None, max_length=80),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if alarm_status:
            clauses.append("status = ?")
            parameters.append(alarm_status)
        if severity:
            clauses.append("severity = ?")
            parameters.append(severity)
        if alarm_type:
            clauses.append("alarm_type = ?")
            parameters.append(alarm_type.upper())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        connection = _read_connection(request)
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM alarm" + where, parameters).fetchone()[0])
            items = _rows(
                connection,
                "SELECT * FROM alarm" + where + " ORDER BY detected_at DESC, alarm_id LIMIT ? OFFSET ?",
                parameters + [limit, offset],
            )
            return {"items": items, "total": total, "limit": limit, "offset": offset}
        finally:
            connection.close()

    @router.get("/alarms/{alarm_id}", tags=["alarms"])
    def get_alarm(alarm_id: str, request: Request) -> dict[str, Any]:
        connection = _read_connection(request)
        try:
            alarm = _one_or_404(
                connection,
                "SELECT * FROM alarm WHERE alarm_id = ?",
                [alarm_id],
                f"Alarm {alarm_id} not found.",
            )
            alarm["evidence"] = _rows(
                connection,
                "SELECT * FROM alarm_evidence WHERE alarm_id = ? ORDER BY observed_at, evidence_id",
                [alarm_id],
            )
            alarm["inventory_impacts"] = _rows(
                connection,
                "SELECT * FROM alarm_inventory_impact WHERE alarm_id = ? ORDER BY lot_id, exposure_start_at",
                [alarm_id],
            )
            alarm["status_history"] = _rows(
                connection,
                "SELECT * FROM alarm_status_history WHERE alarm_id = ? ORDER BY changed_at, history_id",
                [alarm_id],
            )
            return alarm
        finally:
            connection.close()

    @router.get("/alarms/{alarm_id}/impacts", tags=["alarms"])
    def list_alarm_impacts(alarm_id: str, request: Request) -> dict[str, Any]:
        connection = _read_connection(request)
        try:
            _one_or_404(
                connection,
                "SELECT alarm_id FROM alarm WHERE alarm_id = ?",
                [alarm_id],
                f"Alarm {alarm_id} not found.",
            )
            items = _rows(
                connection,
                """
                SELECT i.*, lot.product_code, lot.product_name, lot.product_category,
                       lot.quantity_received, lot.quantity_unit, z.zone_type, z.name AS zone_name
                FROM alarm_inventory_impact i
                JOIN inventory_lot lot USING (lot_id)
                JOIN lot_zone_assignment lza
                  ON lza.assignment_id = i.lot_zone_assignment_id AND lza.lot_id = i.lot_id
                JOIN zone z ON z.zone_id = lza.zone_id
                WHERE i.alarm_id = ?
                ORDER BY lot.lot_id, i.exposure_start_at
                """,
                [alarm_id],
            )
            return {"alarm_id": alarm_id, "items": items, "total": len(items)}
        finally:
            connection.close()

    @router.post("/alarms/{alarm_id}/transitions", tags=["alarms"])
    def alarm_transition(
        alarm_id: str,
        body: AlarmTransitionBody,
        request: Request,
    ) -> dict[str, Any]:
        result = _workflow_call(
            transition_alarm,
            alarm_id=alarm_id,
            new_status=body.new_status,
            changed_by=body.changed_by,
            reason=body.reason,
            assigned_to=body.assigned_to,
            database_path=_database_path(request),
        )
        return {"result": result}

    @router.get("/inspections", tags=["inspections"])
    def list_inspections(
        request: Request,
        inspection_status: InspectionStatus | None = Query(default=None, alias="status"),
        alarm_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if inspection_status:
            clauses.append("status = ?")
            parameters.append(inspection_status)
        if alarm_id:
            clauses.append("alarm_id = ?")
            parameters.append(alarm_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        connection = _read_connection(request)
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM inspection" + where, parameters).fetchone()[0])
            items = _rows(
                connection,
                "SELECT * FROM inspection" + where + " ORDER BY requested_at DESC, inspection_id LIMIT ? OFFSET ?",
                parameters + [limit, offset],
            )
            return {"items": items, "total": total, "limit": limit, "offset": offset}
        finally:
            connection.close()

    @router.get("/inspections/{inspection_id}", tags=["inspections"])
    def get_inspection(inspection_id: str, request: Request) -> dict[str, Any]:
        connection = _read_connection(request)
        try:
            inspection = _one_or_404(
                connection,
                "SELECT * FROM inspection WHERE inspection_id = ?",
                [inspection_id],
                f"Inspection {inspection_id} not found.",
            )
            inspection["status_history"] = _rows(
                connection,
                "SELECT * FROM inspection_status_history WHERE inspection_id = ? ORDER BY changed_at, history_id",
                [inspection_id],
            )
            return inspection
        finally:
            connection.close()

    @router.post(
        "/alarms/{alarm_id}/inspections",
        tags=["inspections"],
        status_code=status.HTTP_201_CREATED,
    )
    def inspection_create(
        alarm_id: str,
        body: InspectionCreateBody,
        request: Request,
    ) -> dict[str, Any]:
        result = _workflow_call(
            create_inspection,
            alarm_id=alarm_id,
            inspection_type=body.inspection_type,
            requested_by=body.requested_by,
            lot_id=body.lot_id,
            inspector=body.inspector,
            requested_at=body.requested_at,
            due_at=body.due_at,
            database_path=_database_path(request),
        )
        return {"result": result}

    @router.post("/inspections/{inspection_id}/transitions", tags=["inspections"])
    def inspection_transition(
        inspection_id: str,
        body: InspectionTransitionBody,
        request: Request,
    ) -> dict[str, Any]:
        result = _workflow_call(
            transition_inspection,
            inspection_id=inspection_id,
            new_status=body.new_status,
            changed_by=body.changed_by,
            reason=body.reason,
            findings=body.findings,
            product_disposition=body.product_disposition,
            inspector=body.inspector,
            database_path=_database_path(request),
        )
        return {"result": result}

    @router.get("/remediations", tags=["remediations"])
    def list_remediations(
        request: Request,
        remediation_status: RemediationStatus | None = Query(default=None, alias="status"),
        inspection_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if remediation_status:
            clauses.append("status = ?")
            parameters.append(remediation_status)
        if inspection_id:
            clauses.append("inspection_id = ?")
            parameters.append(inspection_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        connection = _read_connection(request)
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM remediation" + where, parameters).fetchone()[0])
            items = _rows(
                connection,
                "SELECT * FROM remediation" + where + " ORDER BY created_at DESC, remediation_id LIMIT ? OFFSET ?",
                parameters + [limit, offset],
            )
            return {"items": items, "total": total, "limit": limit, "offset": offset}
        finally:
            connection.close()

    @router.get("/remediations/{remediation_id}", tags=["remediations"])
    def get_remediation(remediation_id: str, request: Request) -> dict[str, Any]:
        connection = _read_connection(request)
        try:
            remediation = _one_or_404(
                connection,
                "SELECT * FROM remediation WHERE remediation_id = ?",
                [remediation_id],
                f"Remediation {remediation_id} not found.",
            )
            remediation["status_history"] = _rows(
                connection,
                "SELECT * FROM remediation_status_history WHERE remediation_id = ? ORDER BY changed_at, history_id",
                [remediation_id],
            )
            remediation["verification_history"] = _rows(
                connection,
                "SELECT * FROM remediation_verification_history WHERE remediation_id = ? ORDER BY verified_at, verification_id",
                [remediation_id],
            )
            return remediation
        finally:
            connection.close()

    @router.post(
        "/inspections/{inspection_id}/remediations",
        tags=["remediations"],
        status_code=status.HTTP_201_CREATED,
    )
    def remediation_create(
        inspection_id: str,
        body: RemediationCreateBody,
        request: Request,
    ) -> dict[str, Any]:
        result = _workflow_call(
            create_remediation,
            inspection_id=inspection_id,
            action_type=body.action_type,
            description=body.description,
            created_by=body.created_by,
            lot_id=body.lot_id,
            assigned_to=body.assigned_to,
            database_path=_database_path(request),
        )
        return {"result": result}

    @router.post("/remediations/{remediation_id}/transitions", tags=["remediations"])
    def remediation_transition(
        remediation_id: str,
        body: RemediationTransitionBody,
        request: Request,
    ) -> dict[str, Any]:
        result = _workflow_call(
            transition_remediation,
            remediation_id=remediation_id,
            new_status=body.new_status,
            changed_by=body.changed_by,
            reason=body.reason,
            database_path=_database_path(request),
        )
        return {"result": result}

    @router.post("/remediations/{remediation_id}/verification", tags=["remediations"])
    def remediation_verification(
        remediation_id: str,
        body: RemediationVerificationBody,
        request: Request,
    ) -> dict[str, Any]:
        result = _workflow_call(
            verify_remediation,
            remediation_id=remediation_id,
            new_verification_status=body.new_verification_status,
            verified_by=body.verified_by,
            reason=body.reason,
            database_path=_database_path(request),
        )
        return {"result": result}

    application.include_router(router)
    return application


app = create_app()
