"""Post-injection validation for ColdWatch datasets and audit trails."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from coldwatch.config import DEFAULT_SETTINGS_PATH, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"
DEFAULT_GENERATED_ROOT = PROJECT_ROOT / "data" / "generated"
DEFAULT_GROUND_TRUTH_ROOT = PROJECT_ROOT / "data" / "ground_truth"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "reports"

EXPECTED_ISSUE_TYPES = {
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

REQUIRED_TABLES = {
    "warehouse",
    "zone",
    "sensor",
    "storage_requirement",
    "requirement_allowed_zone_type",
    "sensor_zone_assignment",
    "sensor_reading",
    "door_event",
    "inventory_lot",
    "lot_zone_assignment",
    "sensor_health_event",
    "sensor_reading_revision",
    "sensor_reading_gap",
    "alarm",
    "alarm_evidence",
    "alarm_inventory_impact",
    "alarm_status_history",
    "inspection",
    "inspection_status_history",
    "remediation",
    "remediation_status_history",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return parsed


def _record(
    checks: list[dict[str, Any]],
    name: str,
    actual: Any,
    expected: Any,
    *,
    passed: bool | None = None,
    detail: str | None = None,
) -> None:
    ok = actual == expected if passed is None else passed
    item: dict[str, Any] = {
        "name": name,
        "status": "PASS" if ok else "FAIL",
        "actual": actual,
        "expected": expected,
    }
    if detail:
        item["detail"] = detail
    checks.append(item)


def _finish(
    checks: list[dict[str, Any]],
    profile: str,
    report_path: str | Path | None,
) -> dict[str, Any]:
    passed = sum(item["status"] == "PASS" for item in checks)
    failed = len(checks) - passed
    result = {
        "project": "ColdWatch AI",
        "profile": profile,
        "status": "PASS" if failed == 0 else "FAIL",
        "checks_total": len(checks),
        "checks_passed": passed,
        "checks_failed": failed,
        "checks": checks,
    }
    if report_path is not None:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        result["report_file"] = str(target)
    return result


def _count(connection: duckdb.DuckDBPyConnection, table: str, where: str = "") -> int:
    # Table and WHERE fragments are internal constants, never user-provided SQL.
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(connection.execute(sql).fetchone()[0])


def validate_dataset(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    profile: str | None = None,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
    generated_root: str | Path = DEFAULT_GENERATED_ROOT,
    ground_truth_root: str | Path = DEFAULT_GROUND_TRUTH_ROOT,
    report_root: str | Path = DEFAULT_REPORT_ROOT,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate baseline artifacts, injected scenarios, and operational traceability.

    Expected injected exceptions are checked against the isolated ground-truth
    artifact. They are not treated as unexplained data-quality failures.
    """
    settings = load_settings(settings_path)
    selected_profile = profile or settings["generation"]["profile"]
    if selected_profile not in settings["profiles"]:
        raise ValueError(f"Unknown dataset profile: {selected_profile}")
    selected_report_root = Path(report_root)
    if report_path is None:
        report_path = selected_report_root / f"validation_{selected_profile}.json"

    checks: list[dict[str, Any]] = []
    db_path = Path(database_path)
    telemetry_dir = Path(generated_root) / selected_profile / "telemetry"
    truth_path = Path(ground_truth_root) / selected_profile / "ground_truth.csv"
    manifest_path = selected_report_root / f"anomaly_manifest_{selected_profile}.json"
    required_paths = {
        "database": db_path,
        "baseline sensor readings Parquet": telemetry_dir / "sensor_readings.parquet",
        "baseline door events Parquet": telemetry_dir / "door_events.parquet",
        "baseline telemetry summary": telemetry_dir / "telemetry_summary.json",
        "separate ground-truth CSV": truth_path,
        "anomaly manifest": manifest_path,
    }
    for label, path in required_paths.items():
        _record(checks, f"artifact exists: {label}", path.is_file(), True)
    if any(not path.is_file() for path in required_paths.values()):
        return _finish(checks, selected_profile, report_path)

    try:
        summary = json.loads((telemetry_dir / "telemetry_summary.json").read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with truth_path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            truth_fields = set(reader.fieldnames or [])
            truth_rows = list(reader)
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        _record(checks, "artifact files are readable", str(exc), "readable")
        return _finish(checks, selected_profile, report_path)

    required_truth_fields = {
        "scenario_id", "issue_type", "entity_type", "entity_id",
        "expected_decision", "expected_severity", "affected_zone_id",
        "event_start_at", "event_end_at", "expected_affected_lot_ids", "rationale",
    }
    _record(checks, "ground-truth columns", sorted(truth_fields), sorted(required_truth_fields))
    _record(
        checks,
        "ground truth is isolated from operational database tables",
        "ground_truth" not in REQUIRED_TABLES,
        True,
    )
    _record(
        checks,
        "baseline telemetry generation mode",
        summary.get("generation_mode"),
        "NORMAL_BASELINE_BEFORE_ANOMALY_INJECTION",
    )
    _record(checks, "manifest generation mode", manifest.get("generation_mode"), "CONTROLLED_ANOMALY_INJECTION")
    _record(checks, "manifest profile", manifest.get("profile"), selected_profile)
    _record(checks, "ground-truth scenario count", len(truth_rows), 9)
    _record(checks, "manifest scenario count", manifest.get("scenario_count"), 9)

    actual_issue_types = sorted({row.get("issue_type", "") for row in truth_rows})
    _record(
        checks,
        "ground-truth issue types",
        actual_issue_types,
        sorted(EXPECTED_ISSUE_TYPES),
    )
    scenario_ids = [row.get("scenario_id", "") for row in truth_rows]
    _record(
        checks,
        "ground-truth scenario IDs are unique and populated",
        len(scenario_ids) == len(set(scenario_ids)) and all(scenario_ids),
        True,
    )
    _record(
        checks,
        "ground-truth checksum",
        _sha256(truth_path),
        manifest.get("ground_truth_sha256"),
    )
    _record(
        checks,
        "manifest issue types match ground truth",
        sorted(manifest.get("scenario_issue_types", [])),
        actual_issue_types,
    )

    # Verify that the immutable baseline Parquet artifacts still match the
    # checksums and row counts recorded before anomaly injection.
    try:
        parquet_readings = telemetry_dir / "sensor_readings.parquet"
        parquet_doors = telemetry_dir / "door_events.parquet"
        reading_rows = int(pq.ParquetFile(parquet_readings).metadata.num_rows)
        door_rows = int(pq.ParquetFile(parquet_doors).metadata.num_rows)
        summary_counts = summary["counts"]
        _record(checks, "baseline sensor Parquet row count", reading_rows, int(summary_counts["sensor_readings"]))
        _record(checks, "baseline door Parquet row count", door_rows, int(summary_counts["door_events"]))
        _record(
            checks,
            "baseline sensor Parquet checksum",
            _sha256(parquet_readings),
            summary["parquet_sha256"]["sensor_readings"],
        )
        _record(
            checks,
            "baseline door Parquet checksum",
            _sha256(parquet_doors),
            summary["parquet_sha256"]["door_events"],
        )
    except (KeyError, OSError, ValueError) as exc:
        _record(checks, "baseline Parquet metadata and checksums", str(exc), "valid")
        return _finish(checks, selected_profile, report_path)

    expected_profile = settings["profiles"][selected_profile]
    expected_master_counts = {
        "warehouse": int(expected_profile["warehouses"]),
        "zone": int(expected_profile["zones"]),
        "sensor": int(expected_profile["sensors"]),
        "inventory_lot": int(expected_profile["inventory_lots"]),
    }

    try:
        connection = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        _record(checks, "DuckDB database opens read-only", str(exc), "openable")
        return _finish(checks, selected_profile, report_path)

    try:
        available_tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        missing_tables = sorted(REQUIRED_TABLES - available_tables)
        _record(checks, "required operational tables exist", missing_tables, [])
        if missing_tables:
            return _finish(checks, selected_profile, report_path)

        counts = {table: _count(connection, table) for table in expected_master_counts}
        for table, expected in expected_master_counts.items():
            _record(checks, f"master-data row count: {table}", counts[table], expected)

        # Every lot must retain placement history. Check allocation at each
        # effective-dated boundary so split assignments remain supported.
        unassigned_lots = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM inventory_lot lot
            WHERE NOT EXISTS (
                SELECT 1 FROM lot_zone_assignment lza WHERE lza.lot_id = lot.lot_id
            )
            """
        ).fetchone()[0])
        _record(checks, "every inventory lot has placement history", unassigned_lots, 0)

        overallocated_lot_segments = int(connection.execute(
            """
            WITH boundaries AS (
                SELECT lot_id, stored_from AS boundary_at FROM lot_zone_assignment
                UNION
                SELECT lot_id, stored_to AS boundary_at
                FROM lot_zone_assignment WHERE stored_to IS NOT NULL
            ),
            allocations AS (
                SELECT b.lot_id, b.boundary_at,
                       SUM(lza.assigned_quantity) AS assigned_quantity,
                       MAX(lot.quantity_received) AS quantity_received
                FROM boundaries b
                JOIN lot_zone_assignment lza
                  ON lza.lot_id = b.lot_id
                 AND lza.stored_from <= b.boundary_at
                 AND (lza.stored_to IS NULL OR lza.stored_to > b.boundary_at)
                JOIN inventory_lot lot ON lot.lot_id = b.lot_id
                GROUP BY b.lot_id, b.boundary_at
            )
            SELECT COUNT(*) FROM allocations
            WHERE assigned_quantity > quantity_received
            """
        ).fetchone()[0])
        _record(checks, "concurrent lot allocation never exceeds received quantity", overallocated_lot_segments, 0)

        operational_counts = {
            "sensor_reading_revisions": _count(connection, "sensor_reading_revision"),
            "sensor_reading_gaps": _count(connection, "sensor_reading_gap"),
            "sensor_health_events": _count(connection, "sensor_health_event"),
            "anomaly_door_events": _count(
                connection, "door_event", "source_system = 'ANOMALY_INJECTOR'"
            ),
            "anomaly_sensor_zone_assignments": _count(
                connection, "sensor_zone_assignment", "assignment_id LIKE 'SZA-ANOM-%'"
            ),
            "anomaly_lot_zone_assignments": _count(
                connection, "lot_zone_assignment", "assignment_id LIKE 'LZA-ANOM-%'"
            ),
            "missing_alarm_inventory_links": _count(
                connection, "alarm_inventory_impact", "alarm_id = 'ALM-ANOM-000001'"
            ),
        }
        manifest_counts = manifest.get("operational_audit_counts", {})
        for key, actual in operational_counts.items():
            _record(
                checks,
                f"manifest audit count: {key}",
                actual,
                manifest_counts.get(key),
            )

        health_event_types = {
            row[0]: int(row[1])
            for row in connection.execute(
                "SELECT event_type, COUNT(*) FROM sensor_health_event GROUP BY event_type"
            ).fetchall()
        }
        _record(
            checks,
            "sensor health event types match outage and drift scenarios",
            health_event_types,
            {"DRIFT": 1, "OUTAGE": 1},
        )

        baseline_readings = int(summary_counts["sensor_readings"])
        baseline_doors = int(summary_counts["door_events"])
        current_readings = _count(connection, "sensor_reading")
        current_doors = _count(connection, "door_event")
        _record(
            checks,
            "operational sensor readings reconcile to explicit gap records",
            current_readings,
            baseline_readings - operational_counts["sensor_reading_gaps"],
        )
        _record(
            checks,
            "operational door events reconcile to injected events",
            current_doors,
            baseline_doors + operational_counts["anomaly_door_events"],
        )
        distinct_reading_sensors = int(
            connection.execute("SELECT COUNT(DISTINCT sensor_id) FROM sensor_reading").fetchone()[0]
        )
        _record(
            checks,
            "every configured sensor has telemetry",
            distinct_reading_sensors,
            int(expected_profile["sensors"]),
        )

        # Basic telemetry validity: unit/type compatibility, bounded percentages,
        # allowed quality values, and ingestion time not preceding observation.
        invalid_units = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_reading r JOIN sensor s USING (sensor_id)
            WHERE r.unit <> s.unit
               OR (s.sensor_type = 'TEMPERATURE' AND r.unit <> 'degC')
               OR (s.sensor_type = 'HUMIDITY' AND r.unit <> 'pct_rh')
               OR (s.sensor_type = 'OCCUPANCY' AND r.unit <> 'pct_capacity')
            """
        ).fetchone()[0])
        _record(checks, "sensor reading units match sensor types", invalid_units, 0)

        invalid_values = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_reading
            WHERE (unit IN ('pct_rh', 'pct_capacity') AND reading_value NOT BETWEEN 0 AND 100)
               OR quality_status NOT IN ('GOOD', 'SUSPECT', 'BAD')
               OR ingested_at < observed_at
            """
        ).fetchone()[0])
        _record(checks, "sensor reading values, quality, and timestamps are valid", invalid_values, 0)

        cadence = connection.execute(
            """
            WITH ordered AS (
                SELECT r.sensor_id, r.observed_at,
                       s.expected_interval_seconds,
                       LAG(r.observed_at) OVER (
                           PARTITION BY r.sensor_id ORDER BY r.observed_at
                       ) AS previous_at
                FROM sensor_reading r
                JOIN sensor s USING (sensor_id)
            ),
            deltas AS (
                SELECT date_diff('second', previous_at, observed_at) AS delta_seconds,
                       expected_interval_seconds
                FROM ordered
                WHERE previous_at IS NOT NULL
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE delta_seconds < expected_interval_seconds
                       OR delta_seconds % expected_interval_seconds <> 0
                ) AS irregular_intervals,
                COALESCE(SUM(
                    CASE
                        WHEN delta_seconds >= expected_interval_seconds
                         AND delta_seconds % expected_interval_seconds = 0
                        THEN CAST(delta_seconds / expected_interval_seconds AS BIGINT) - 1
                        ELSE 0
                    END
                ), 0) AS missing_intervals
            FROM deltas
            """
        ).fetchone()
        _record(checks, "telemetry cadence has no irregular intervals", int(cadence[0]), 0)
        _record(
            checks,
            "cadence gaps reconcile to gap tombstones",
            int(cadence[1]),
            operational_counts["sensor_reading_gaps"],
        )
        unresolved_gap_rows = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_reading_gap g
            JOIN sensor_reading r
              ON r.sensor_id = g.sensor_id AND r.observed_at = g.expected_at
            """
        ).fetchone()[0])
        _record(checks, "gap tombstones represent absent observations", unresolved_gap_rows, 0)

        revision_mismatches = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_reading_revision rev
            LEFT JOIN sensor_reading r USING (reading_id)
            WHERE r.reading_id IS NULL OR r.reading_value <> rev.revised_value
            """
        ).fetchone()[0])
        _record(checks, "reading revision ledger matches current readings", revision_mismatches, 0)

        # Normal baseline door events must remain paired and within the configured
        # normal duration range; the deliberately long-open pair is validated apart.
        invalid_normal_doors = int(connection.execute(
            """
            WITH ordered AS (
                SELECT zone_id, door_id, event_type, event_at,
                       LEAD(event_type) OVER (
                           PARTITION BY zone_id, door_id ORDER BY event_at
                       ) AS next_type,
                       LEAD(event_at) OVER (
                           PARTITION BY zone_id, door_id ORDER BY event_at
                       ) AS next_at,
                       LAG(event_type) OVER (
                           PARTITION BY zone_id, door_id ORDER BY event_at
                       ) AS previous_type
                FROM door_event
                WHERE source_system <> 'ANOMALY_INJECTOR'
            )
            SELECT COUNT(*)
            FROM ordered
            WHERE (event_type = 'OPEN' AND (
                       next_type IS DISTINCT FROM 'CLOSE'
                       OR next_at IS NULL
                       OR date_diff('second', event_at, next_at) NOT BETWEEN 120 AND 480
                   ))
               OR (event_type = 'CLOSE' AND previous_type IS DISTINCT FROM 'OPEN')
            """
        ).fetchone()[0])
        _record(checks, "normal door events remain paired and within baseline duration", invalid_normal_doors, 0)

        injected_long_door_pairs = int(connection.execute(
            """
            WITH ordered AS (
                SELECT zone_id, door_id, event_type, event_at,
                       LEAD(event_type) OVER (
                           PARTITION BY zone_id, door_id ORDER BY event_at
                       ) AS next_type,
                       LEAD(event_at) OVER (
                           PARTITION BY zone_id, door_id ORDER BY event_at
                       ) AS next_at
                FROM door_event
                WHERE source_system = 'ANOMALY_INJECTOR'
            )
            SELECT COUNT(*)
            FROM ordered
            WHERE event_type = 'OPEN'
              AND next_type = 'CLOSE'
              AND date_diff('second', event_at, next_at) = 1500
            """
        ).fetchone()[0])
        _record(checks, "injected 25-minute door-open pair", injected_long_door_pairs, 1)

        # Effective-dated mappings may overlap only as explicit, approved exceptions.
        overlap_counts = connection.execute(
            """
            SELECT
                COUNT(*) AS overlap_pairs,
                COUNT(*) FILTER (
                    WHERE NOT (
                        a.is_approved AND b.is_approved
                        AND NULLIF(TRIM(a.rationale), '') IS NOT NULL
                        AND NULLIF(TRIM(b.rationale), '') IS NOT NULL
                    )
                ) AS unapproved_overlap_pairs
            FROM sensor_zone_assignment a
            JOIN sensor_zone_assignment b
              ON a.sensor_id = b.sensor_id
             AND a.zone_id <> b.zone_id
             AND a.assignment_id < b.assignment_id
             AND a.valid_from < COALESCE(b.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
             AND b.valid_from < COALESCE(a.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
            """
        ).fetchone()
        _record(checks, "overlapping sensor-zone mapping pairs match controlled scenario", int(overlap_counts[0]), 1)
        _record(checks, "unapproved overlapping sensor-zone mappings are detected", int(overlap_counts[1]), 1)

        incompatible_lot_assignments = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM lot_zone_assignment lza
            JOIN inventory_lot lot USING (lot_id)
            JOIN zone z USING (zone_id)
            WHERE NOT EXISTS (
                SELECT 1
                FROM requirement_allowed_zone_type allowed
                WHERE allowed.requirement_id = lot.requirement_id
                  AND allowed.zone_type = z.zone_type
            )
            """
        ).fetchone()[0])
        _record(checks, "incompatible lot-zone assignments match controlled scenario", incompatible_lot_assignments, 1)

        no_link_alarm_rows = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM alarm a
            WHERE a.alarm_id = 'ALM-ANOM-000001'
              AND NOT EXISTS (
                  SELECT 1 FROM alarm_inventory_impact i WHERE i.alarm_id = a.alarm_id
              )
            """
        ).fetchone()[0])
        overlapping_alarm_lots = int(connection.execute(
            """
            SELECT COUNT(DISTINCT lza.lot_id)
            FROM alarm a
            JOIN lot_zone_assignment lza
              ON lza.zone_id = a.zone_id
             AND lza.stored_from < a.event_end_at
             AND (lza.stored_to IS NULL OR lza.stored_to > a.event_start_at)
            WHERE a.alarm_id = 'ALM-ANOM-000001'
            """
        ).fetchone()[0])
        _record(checks, "negative alarm scenario has no impact links", no_link_alarm_rows, 1)
        _record(
            checks,
            "negative alarm scenario overlaps inventory lots",
            overlapping_alarm_lots > 0,
            True,
            detail=f"overlapping lots={overlapping_alarm_lots}",
        )

        # Tie each scenario back to its operational evidence rather than merely
        # accepting a correctly-shaped ground-truth CSV.
        scenarios_by_type = {row.get("issue_type"): row for row in truth_rows}
        for issue_type in sorted(EXPECTED_ISSUE_TYPES):
            if issue_type not in scenarios_by_type:
                continue
            row = scenarios_by_type[issue_type]
            sensor_id = row.get("entity_id", "")
            zone_id = row.get("affected_zone_id", "")
            start_at = _timestamp(row["event_start_at"])
            end_at = _timestamp(row["event_end_at"])

            if issue_type in {"TEMPERATURE_EXCURSION", "HUMIDITY_EXCURSION"}:
                column = "temperature_max_c" if issue_type == "TEMPERATURE_EXCURSION" else "humidity_max_pct"
                unit_clause = "r.reading_value > req.temperature_max_c" if issue_type == "TEMPERATURE_EXCURSION" else "r.reading_value > req.humidity_max_pct"
                evidence_count = int(connection.execute(
                    f"""
                    SELECT COUNT(DISTINCT r.reading_id)
                    FROM sensor_reading r
                    JOIN lot_zone_assignment lza
                      ON lza.zone_id = ?
                     AND lza.stored_from < ?
                     AND (lza.stored_to IS NULL OR lza.stored_to > ?)
                    JOIN inventory_lot lot USING (lot_id)
                    JOIN storage_requirement req
                      ON req.requirement_id = lot.requirement_id
                    WHERE r.sensor_id = ?
                      AND r.observed_at >= ? AND r.observed_at < ?
                      AND req.{column} IS NOT NULL
                      AND {unit_clause}
                    """,
                    [zone_id, end_at, start_at, sensor_id, start_at, end_at],
                ).fetchone()[0])
                _record(checks, f"scenario evidence: {issue_type}", evidence_count, 4, passed=evidence_count >= 4)
            elif issue_type == "OCCUPANCY_LIMIT":
                evidence_count = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM sensor_reading r
                    JOIN zone z ON z.zone_id = ?
                    WHERE r.sensor_id = ?
                      AND r.observed_at >= ? AND r.observed_at < ?
                      AND r.reading_value > z.occupancy_warning_limit_pct
                    """,
                    [zone_id, sensor_id, start_at, end_at],
                ).fetchone()[0])
                _record(checks, "scenario evidence: OCCUPANCY_LIMIT", evidence_count, 4, passed=evidence_count >= 4)
            elif issue_type == "DOOR_OPEN_TOO_LONG":
                if ":" in sensor_id:
                    door_zone, door_id = sensor_id.split(":", 1)
                    pair_count = int(connection.execute(
                        """
                        WITH ordered AS (
                            SELECT event_type, event_at,
                                   LEAD(event_type) OVER (ORDER BY event_at) AS next_type,
                                   LEAD(event_at) OVER (ORDER BY event_at) AS next_at
                            FROM door_event
                            WHERE source_system = 'ANOMALY_INJECTOR'
                              AND zone_id = ? AND door_id = ?
                        )
                        SELECT COUNT(*) FROM ordered
                        WHERE event_type = 'OPEN' AND next_type = 'CLOSE'
                          AND date_diff('second', event_at, next_at) = 1500
                        """,
                        [door_zone, door_id],
                    ).fetchone()[0])
                else:
                    pair_count = 0
                _record(checks, "scenario evidence: DOOR_OPEN_TOO_LONG", pair_count, 1)
            elif issue_type == "SENSOR_OUTAGE":
                evidence_count = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM sensor_reading_gap
                    WHERE sensor_id = ?
                      AND expected_at >= ? AND expected_at < ?
                    """,
                    [sensor_id, start_at, end_at],
                ).fetchone()[0])
                _record(checks, "scenario evidence: SENSOR_OUTAGE", evidence_count, 2)
            elif issue_type == "SENSOR_DRIFT":
                evidence_count = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM sensor_reading_revision rev
                    JOIN sensor_reading r USING (reading_id)
                    WHERE r.sensor_id = ?
                      AND rev.revised_at >= ? AND rev.revised_at < ?
                """,
                    [sensor_id, start_at, end_at],
                ).fetchone()[0])
                _record(checks, "scenario evidence: SENSOR_DRIFT", evidence_count, 8)
            elif issue_type == "INCOMPATIBLE_LOT_ZONE":
                evidence_count = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM lot_zone_assignment lza
                    JOIN zone z USING (zone_id)
                    WHERE lza.assignment_id = 'LZA-ANOM-000001'
                      AND lza.lot_id = ? AND lza.zone_id = ?
                      AND lza.stored_from = ? AND lza.stored_to = ?
                    """,
                    [sensor_id, zone_id, start_at, end_at],
                ).fetchone()[0])
                _record(checks, "scenario evidence: INCOMPATIBLE_LOT_ZONE", evidence_count, 1)
            elif issue_type == "SENSOR_MAPPED_TO_MULTIPLE_ZONES":
                evidence_count = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM sensor_zone_assignment
                    WHERE assignment_id = 'SZA-ANOM-000001'
                      AND sensor_id = ? AND zone_id = ?
                      AND is_approved = FALSE AND rationale IS NULL
                    """,
                    [sensor_id, zone_id],
                ).fetchone()[0])
                _record(checks, "scenario evidence: SENSOR_MAPPED_TO_MULTIPLE_ZONES", evidence_count, 1)
            elif issue_type == "ALARM_NOT_LINKED_TO_AFFECTED_LOTS":
                evidence_count = int(connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM alarm a
                    WHERE a.alarm_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM alarm_inventory_impact i WHERE i.alarm_id = a.alarm_id
                      )
                    """,
                    [sensor_id],
                ).fetchone()[0])
                _record(checks, "scenario evidence: ALARM_NOT_LINKED_TO_AFFECTED_LOTS", evidence_count, 1)

        # A source table for expected labels must never exist in DuckDB.
        _record(
            checks,
            "ground truth is not loaded into DuckDB",
            "ground_truth" not in available_tables,
            True,
        )
    except (duckdb.Error, KeyError, ValueError, TypeError) as exc:
        _record(checks, "operational validation completed without query or data errors", str(exc), "no errors")
    finally:
        connection.close()

    return _finish(checks, selected_profile, report_path)
