"""Inject deterministic, auditable anomalies into a valid ColdWatch baseline.

The operational tables receive only the simulated source events and audit records.
Expected labels are written to a separate CSV consumed only by the evaluator.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from coldwatch.config import DEFAULT_SETTINGS_PATH, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"
DEFAULT_GENERATED_ROOT = PROJECT_ROOT / "data" / "generated"
DEFAULT_GROUND_TRUTH_ROOT = PROJECT_ROOT / "data" / "ground_truth"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "reports"

GROUND_TRUTH_FIELDS = [
    "scenario_id", "issue_type", "entity_type", "entity_id",
    "expected_decision", "expected_severity", "affected_zone_id",
    "event_start_at", "event_end_at", "expected_affected_lot_ids", "rationale",
]


class AnomalyInjectionError(ValueError):
    """Raised when a controlled anomaly cannot be injected safely."""


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AnomalyInjectionError("Timestamps must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str:
    return "" if value is None else value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_csv_temp(rows: list[dict[str, str]], final_path: Path) -> tuple[Path, str]:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=GROUND_TRUTH_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return temp_path, hashlib.sha256(temp_path.read_bytes()).hexdigest()


def _scenario(
    number: int,
    issue_type: str,
    entity_type: str,
    entity_id: str,
    expected_decision: str,
    severity: str,
    zone_id: str | None,
    start_at: datetime,
    end_at: datetime | None,
    lot_ids: list[str],
    rationale: str,
) -> dict[str, str]:
    return {
        "scenario_id": f"SCN-2026-{number:04d}",
        "issue_type": issue_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "expected_decision": expected_decision,
        "expected_severity": severity,
        "affected_zone_id": zone_id or "",
        "event_start_at": _iso(start_at),
        "event_end_at": _iso(end_at),
        "expected_affected_lot_ids": json.dumps(sorted(set(lot_ids)), separators=(",", ":")),
        "rationale": rationale,
    }


def _telemetry_summary(profile: str, generated_root: str | Path) -> dict[str, Any]:
    path = Path(generated_root) / profile / "telemetry" / "telemetry_summary.json"
    if not path.is_file():
        raise FileNotFoundError(f"Telemetry summary not found: {path}. Generate master data and telemetry first.")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary.get("generation_mode") != "NORMAL_BASELINE_BEFORE_ANOMALY_INJECTION":
        raise AnomalyInjectionError("Expected a normal pre-injection telemetry baseline.")
    return summary


def _lots_overlapping(
    con: duckdb.DuckDBPyConnection,
    zone_id: str,
    start_at: datetime,
    end_at: datetime,
) -> list[str]:
    rows = con.execute(
        """
        SELECT DISTINCT lot_id
        FROM lot_zone_assignment
        WHERE zone_id = ?
          AND stored_from < ?
          AND (stored_to IS NULL OR stored_to > ?)
        ORDER BY lot_id
        """,
        [zone_id, end_at, start_at],
    ).fetchall()
    return [row[0] for row in rows]


def _four_consecutive_readings(
    con: duckdb.DuckDBPyConnection,
    sensor_id: str,
    at_or_after: datetime,
    *,
    count: int = 4,
) -> list[tuple[Any, ...]]:
    rows = con.execute(
        """
        SELECT reading_id, observed_at, reading_value, quality_status
        FROM sensor_reading
        WHERE sensor_id = ? AND observed_at >= ?
        ORDER BY observed_at
        LIMIT ?
        """,
        [sensor_id, at_or_after, count],
    ).fetchall()
    if len(rows) != count:
        return []
    if any((rows[i + 1][1] - rows[i][1]).total_seconds() != 300 for i in range(count - 1)):
        return []
    return rows


def _find_environment_candidate(
    con: duckdb.DuckDBPyConnection,
    sensor_type: str,
    requirement_max_column: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    # requirement_max_column is selected only from the internal allow-list below.
    if requirement_max_column not in {"temperature_max_c", "humidity_max_pct"}:
        raise AnomalyInjectionError("Unsupported storage requirement column.")

    # Find candidate sensor/lot pairs before touching the large telemetry table.
    # Joining one million readings to every lot in a zone first creates a large
    # sensor_reading x lot_zone_assignment intermediate result. The baseline has
    # one mapping per sensor, so this query returns at most one candidate lot per
    # sensor and then checks only the four (or eight in other callers) readings
    # needed for the scenario.
    candidates = con.execute(
        f"""
        SELECT s.sensor_id, s.unit, z.zone_id, z.zone_type,
               lza.stored_from, lza.stored_to, req.{requirement_max_column},
               sza.valid_from, sza.valid_to
        FROM sensor s
        JOIN sensor_zone_assignment sza ON sza.sensor_id = s.sensor_id
        JOIN zone z ON z.zone_id = sza.zone_id
        JOIN lot_zone_assignment lza ON lza.zone_id = z.zone_id
        JOIN inventory_lot lot ON lot.lot_id = lza.lot_id
        JOIN storage_requirement req ON req.requirement_id = lot.requirement_id
        WHERE s.sensor_type = ?
          AND req.{requirement_max_column} IS NOT NULL
          AND lza.stored_from < ? - INTERVAL '30 minutes'
          AND (lza.stored_to IS NULL OR lza.stored_to > ? + INTERVAL '1 hour')
          AND sza.valid_from < ? - INTERVAL '30 minutes'
          AND (sza.valid_to IS NULL OR sza.valid_to > ? + INTERVAL '1 hour')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY s.sensor_id
            ORDER BY lza.stored_from, lza.lot_id, sza.valid_from, z.zone_id
        ) = 1
        ORDER BY s.sensor_id, z.zone_id
        """,
        [sensor_type, end_at, start_at, end_at, start_at],
    ).fetchall()

    for row in candidates:
        (
            sensor_id, unit, zone_id, zone_type, stored_from, stored_to,
            maximum, mapping_from, mapping_to,
        ) = row
        candidate_from = max(start_at + timedelta(hours=1), stored_from)
        readings = _four_consecutive_readings(con, sensor_id, candidate_from)
        if len(readings) != 4:
            continue

        event_start = readings[0][1]
        event_end = readings[-1][1] + timedelta(minutes=5)
        # Effective-dated lot and sensor mappings use an exclusive valid_to /
        # stored_to boundary, so the last source reading must precede it.
        if event_start < start_at + timedelta(hours=1):
            continue
        if readings[-1][1] >= end_at - timedelta(minutes=30):
            continue
        if stored_to is not None and stored_to < event_end:
            continue
        if mapping_from > event_start:
            continue
        if mapping_to is not None and mapping_to <= readings[-1][1]:
            continue

        return {
            "sensor_id": sensor_id,
            "unit": unit,
            "zone_id": zone_id,
            "zone_type": zone_type,
            "permitted_max": float(maximum),
            "readings": readings,
        }

    raise AnomalyInjectionError(
        f"No suitable {sensor_type} sensor with four consecutive readings "
        "and overlapping inventory was found."
    )


def _revise_readings(
    con: duckdb.DuckDBPyConnection,
    readings: list[tuple[Any, ...]],
    values: list[float],
    prefix: str,
    note: str,
) -> None:
    if len(readings) != len(values):
        raise AnomalyInjectionError("Revision values must match source reading count.")
    for index, (reading, value) in enumerate(zip(readings, values, strict=True), start=1):
        reading_id, observed_at, original_value, original_quality = reading
        con.execute(
            """
            INSERT INTO sensor_reading_revision (
                revision_id, reading_id, previous_value, revised_value,
                previous_quality_status, revised_quality_status, revised_at, revision_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"{prefix}-{index:03d}", reading_id, original_value, value,
                original_quality, original_quality, observed_at, note,
            ],
        )
        con.execute(
            "UPDATE sensor_reading SET reading_value = ? WHERE reading_id = ?",
            [value, reading_id],
        )


def _inject_temperature(con: duckdb.DuckDBPyConnection, start: datetime, end: datetime) -> tuple[dict[str, str], dict[str, Any]]:
    candidate = _find_environment_candidate(con, "TEMPERATURE", "temperature_max_c", start, end)
    target = round(candidate["permitted_max"] + 5.0, 4)
    readings = candidate["readings"]
    event_start = readings[0][1]
    event_end = readings[-1][1] + timedelta(minutes=5)
    lots = con.execute(
        """
        SELECT DISTINCT lza.lot_id
        FROM lot_zone_assignment lza
        JOIN inventory_lot lot USING (lot_id)
        JOIN storage_requirement req USING (requirement_id)
        WHERE lza.zone_id = ?
          AND lza.stored_from < ?
          AND (lza.stored_to IS NULL OR lza.stored_to > ?)
          AND req.temperature_max_c < ?
        ORDER BY lza.lot_id
        """,
        [candidate["zone_id"], event_end, event_start, target],
    ).fetchall()
    lot_ids = [row[0] for row in lots]
    _revise_readings(
        con, readings, [target] * len(readings), "REV-TEMP-001",
        "Controlled synthetic temperature excursion; original value retained in revision ledger.",
    )
    row = _scenario(
        1, "TEMPERATURE_EXCURSION", "SENSOR", candidate["sensor_id"],
        "TEMPERATURE_EXCURSION", "HIGH", candidate["zone_id"], event_start,
        event_end, lot_ids,
        f"Four consecutive readings were revised to {target:.2f} degC, above the applicable maximum of {candidate['permitted_max']:.2f} degC.",
    )
    return row, {"candidate": candidate, "start": event_start, "end": event_end, "lots": lot_ids}


def _inject_humidity(con: duckdb.DuckDBPyConnection, start: datetime, end: datetime) -> dict[str, str]:
    candidate = _find_environment_candidate(con, "HUMIDITY", "humidity_max_pct", start, end)
    target = round(min(99.0, candidate["permitted_max"] + 10.0), 4)
    if target <= candidate["permitted_max"]:
        raise AnomalyInjectionError("Could not construct a humidity value above the selected lot's maximum.")
    readings = candidate["readings"]
    event_start = readings[0][1]
    event_end = readings[-1][1] + timedelta(minutes=5)
    lots = con.execute(
        """
        SELECT DISTINCT lza.lot_id
        FROM lot_zone_assignment lza
        JOIN inventory_lot lot USING (lot_id)
        JOIN storage_requirement req USING (requirement_id)
        WHERE lza.zone_id = ?
          AND lza.stored_from < ?
          AND (lza.stored_to IS NULL OR lza.stored_to > ?)
          AND req.humidity_max_pct < ?
        ORDER BY lza.lot_id
        """,
        [candidate["zone_id"], event_end, event_start, target],
    ).fetchall()
    lot_ids = [row[0] for row in lots]
    _revise_readings(
        con, readings, [target] * len(readings), "REV-HUM-001",
        "Controlled synthetic humidity excursion; original value retained in revision ledger.",
    )
    return _scenario(
        2, "HUMIDITY_EXCURSION", "SENSOR", candidate["sensor_id"],
        "HUMIDITY_EXCURSION", "HIGH", candidate["zone_id"], event_start,
        event_end, lot_ids,
        f"Four consecutive readings were revised to {target:.2f}% RH, above the applicable maximum of {candidate['permitted_max']:.2f}% RH.",
    )


def _inject_occupancy(con: duckdb.DuckDBPyConnection, start: datetime, end: datetime) -> dict[str, str]:
    candidates = con.execute(
        """
        SELECT s.sensor_id, s.unit, sza.zone_id, z.occupancy_warning_limit_pct
        FROM sensor s
        JOIN sensor_zone_assignment sza ON sza.sensor_id = s.sensor_id
        JOIN zone z ON z.zone_id = sza.zone_id
        WHERE s.sensor_type = 'OCCUPANCY'
        ORDER BY s.sensor_id
        """
    ).fetchall()
    candidate = None
    for sensor_id, unit, zone_id, warning_limit in candidates:
        readings = _four_consecutive_readings(con, sensor_id, start + (end - start) / 3)
        if len(readings) == 4:
            candidate = (sensor_id, unit, zone_id, float(warning_limit), readings)
            break
    if candidate is None:
        raise AnomalyInjectionError("No occupancy sensor with four consecutive readings was found.")
    sensor_id, _, zone_id, warning_limit, readings = candidate
    target = min(99.0, max(98.0, warning_limit + 5.0))
    _revise_readings(
        con, readings, [target] * 4, "REV-OCC-001",
        "Controlled synthetic high-occupancy scenario; original value retained in revision ledger.",
    )
    return _scenario(
        3, "OCCUPANCY_LIMIT", "SENSOR", sensor_id, "OCCUPANCY_LIMIT",
        "MEDIUM", zone_id, readings[0][1], readings[-1][1] + timedelta(minutes=5),
        _lots_for_zone(con, zone_id, readings[0][1], readings[-1][1] + timedelta(minutes=5)),
        f"Occupancy utilization was revised to {target:.1f}%, above the zone warning threshold of {warning_limit:.1f}%.",
    )


def _lots_for_zone(con: duckdb.DuckDBPyConnection, zone_id: str, start: datetime, end: datetime) -> list[str]:
    return [
        row[0]
        for row in con.execute(
            """
            SELECT DISTINCT lot_id
            FROM lot_zone_assignment
            WHERE zone_id = ? AND stored_from < ?
              AND (stored_to IS NULL OR stored_to > ?)
            ORDER BY lot_id
            """,
            [zone_id, end, start],
        ).fetchall()
    ]


def _inject_door_open(con: duckdb.DuckDBPyConnection, start: datetime, end: datetime) -> dict[str, str]:
    zone_id = con.execute("SELECT zone_id FROM zone ORDER BY zone_id LIMIT 1").fetchone()[0]
    open_at = start + timedelta(hours=1)
    close_at = open_at + timedelta(minutes=25)
    door_id = "DOOR-ANOM-01"
    for event_type, event_at in (("OPEN", open_at), ("CLOSE", close_at)):
        timestamp = event_at.strftime("%Y%m%dT%H%M%SZ")
        event_id = f"DRE-{zone_id}-{door_id}-{timestamp}-{event_type}"
        con.execute(
            """
            INSERT INTO door_event (
                door_event_id, zone_id, door_id, event_type, event_at,
                source_system, ingested_at, notes
            )
            VALUES (?, ?, ?, ?, ?, 'ANOMALY_INJECTOR', ?, ?)
            """,
            [
                event_id, zone_id, door_id, event_type, event_at, event_at,
                "Controlled synthetic prolonged door-open scenario.",
            ],
        )
    return _scenario(
        4, "DOOR_OPEN_TOO_LONG", "DOOR", f"{zone_id}:{door_id}",
        "DOOR_OPEN_TOO_LONG", "HIGH", zone_id, open_at, close_at,
        _lots_for_zone(con, zone_id, open_at, close_at),
        "A dedicated anomaly door was left open for 25 minutes, exceeding the illustrative 10-minute warning threshold.",
    )


def _inject_outage(
    con: duckdb.DuckDBPyConnection, start: datetime, end: datetime
) -> tuple[dict[str, str], int]:
    candidates = con.execute(
        """
        SELECT sensor_id
        FROM sensor
        WHERE sensor_type = 'TEMPERATURE' AND status = 'ACTIVE'
        ORDER BY sensor_id
        """
    ).fetchall()
    selected = None
    for (sensor_id,) in candidates:
        rows = con.execute(
            """
            SELECT reading_id, observed_at
            FROM sensor_reading
            WHERE sensor_id = ? AND observed_at >= ? AND observed_at < ?
            ORDER BY observed_at
            """,
            [sensor_id, start + (end - start) / 2, end - timedelta(minutes=20)],
        ).fetchall()
        if len(rows) >= 4:
            selected = (sensor_id, rows[1], rows[2], rows[3])
            break
    if selected is None:
        raise AnomalyInjectionError("Could not find consecutive interior readings for outage injection.")
    sensor_id, first_missing, second_missing, recovery_row = selected
    first_id, first_at = first_missing
    second_id, second_at = second_missing
    recovery_at = recovery_row[1]
    interval_seconds = 300
    zone_id = con.execute(
        """
        SELECT zone_id FROM sensor_zone_assignment
        WHERE sensor_id = ? ORDER BY valid_from DESC LIMIT 1
        """,
        [sensor_id],
    ).fetchone()[0]
    for index, (reading_id, expected_at) in enumerate(
        ((first_id, first_at), (second_id, second_at)), start=1
    ):
        con.execute(
            """
            INSERT INTO sensor_reading_gap (
                gap_id, sensor_id, expected_reading_id, expected_at,
                expected_interval_seconds, gap_status, detected_at, recovered_at, gap_note
            )
            VALUES (?, ?, ?, ?, ?, 'RECOVERED', ?, ?, ?)
            """,
            [
                f"GAP-ANOM-{index:03d}", sensor_id, reading_id, expected_at,
                interval_seconds, recovery_at, recovery_at,
                "Expected observation intentionally withheld to simulate a sensor outage.",
            ],
        )
        con.execute("DELETE FROM sensor_reading WHERE reading_id = ?", [reading_id])
    health_id = "SHE-ANOM-OUTAGE-001"
    con.execute(
        """
        INSERT INTO sensor_health_event (
            health_event_id, sensor_id, event_type, started_at, ended_at,
            status, expected_interval_seconds, missed_interval_count, details, detected_at
        )
        VALUES (?, ?, 'OUTAGE', ?, ?, 'RESOLVED', ?, 2, ?, ?)
        """,
        [
            health_id, sensor_id, first_at, recovery_at, interval_seconds,
            "Two consecutive expected telemetry observations were not received.",
            recovery_at,
        ],
    )
    return _scenario(
        5, "SENSOR_OUTAGE", "SENSOR", sensor_id, "SENSOR_OUTAGE", "HIGH",
        zone_id, first_at, recovery_at,
        _lots_for_zone(con, zone_id, first_at, recovery_at),
        "Two expected five-minute readings were withheld; explicit gap tombstones and a sensor health event preserve the outage.",
    ), 2


def _inject_drift(con: duckdb.DuckDBPyConnection, start: datetime, end: datetime) -> tuple[dict[str, str], int]:
    candidates = con.execute(
        """
        SELECT s.sensor_id, s.unit, sza.zone_id
        FROM sensor s
        JOIN sensor_zone_assignment sza USING (sensor_id)
        JOIN zone z USING (zone_id)
        WHERE s.sensor_type = 'TEMPERATURE' AND z.zone_type = 'HOT'
        ORDER BY s.sensor_id
        """
    ).fetchall()
    selected = None
    for sensor_id, unit, zone_id in candidates:
        readings = _four_consecutive_readings(con, sensor_id, start + (end - start) / 4, count=8)
        if len(readings) == 8:
            selected = (sensor_id, unit, zone_id, readings)
            break
    if selected is None:
        raise AnomalyInjectionError("Could not find eight consecutive HOT-zone temperature readings for drift.")
    sensor_id, _, zone_id, readings = selected
    offsets = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    values = [round(float(row[2]) + offset, 4) for row, offset in zip(readings, offsets, strict=True)]
    _revise_readings(
        con, readings, values, "REV-DRIFT-001",
        "Controlled synthetic gradual sensor bias; original value retained in revision ledger.",
    )
    health_id = "SHE-ANOM-DRIFT-001"
    start_at = readings[0][1]
    end_at = readings[-1][1] + timedelta(minutes=5)
    con.execute(
        """
        INSERT INTO sensor_health_event (
            health_event_id, sensor_id, event_type, started_at, ended_at,
            status, expected_interval_seconds, missed_interval_count, details, detected_at
        )
        VALUES (?, ?, 'DRIFT', ?, ?, 'RESOLVED', 300, 0, ?, ?)
        """,
        [
            health_id, sensor_id, start_at, end_at,
            "Gradually increasing +0.5 to +4.0 degC bias applied to one redundant sensor.",
            end_at,
        ],
    )
    return _scenario(
        6, "SENSOR_DRIFT", "SENSOR", sensor_id, "SENSOR_DRIFT", "MEDIUM",
        zone_id, start_at, end_at, [],
        "One of two redundant HOT-zone temperature sensors received a gradual bias while remaining within the illustrative product limit.",
    ), 8


def _inject_incompatible_storage(
    con: duckdb.DuckDBPyConnection, start: datetime, end: datetime
) -> dict[str, str]:
    lots = con.execute(
        """
        SELECT lza.assignment_id, lza.lot_id, lza.zone_id, lza.assigned_quantity,
               lza.quantity_unit, lza.stored_from, lot.requirement_id, z.warehouse_id
        FROM lot_zone_assignment lza
        JOIN inventory_lot lot USING (lot_id)
        JOIN zone z ON z.zone_id = lza.zone_id
        WHERE lza.status = 'ACTIVE' AND lza.stored_to IS NULL
          AND lza.stored_from < ?
        ORDER BY lza.stored_from, lza.lot_id
        """,
        [end - timedelta(hours=2)],
    ).fetchall()
    chosen = None
    for row in lots:
        assignment_id, lot_id, original_zone, quantity, unit, stored_from, requirement_id, warehouse_id = row
        allowed = [
            item[0] for item in con.execute(
                "SELECT zone_type FROM requirement_allowed_zone_type WHERE requirement_id = ?",
                [requirement_id],
            ).fetchall()
        ]
        wrong = con.execute(
            """
            SELECT zone_id FROM zone
            WHERE warehouse_id = ?
              AND zone_id <> ?
              AND zone_type NOT IN (
                  SELECT zone_type FROM requirement_allowed_zone_type WHERE requirement_id = ?
              )
            ORDER BY zone_id LIMIT 1
            """,
            [warehouse_id, original_zone, requirement_id],
        ).fetchone()
        if wrong is None:
            wrong = con.execute(
                """
                SELECT zone_id FROM zone
                WHERE zone_id <> ?
                  AND zone_type NOT IN (
                      SELECT zone_type FROM requirement_allowed_zone_type WHERE requirement_id = ?
                  )
                ORDER BY zone_id LIMIT 1
                """,
                [original_zone, requirement_id],
            ).fetchone()
        if wrong is not None:
            chosen = (assignment_id, lot_id, original_zone, quantity, unit, stored_from, wrong[0], allowed)
            break
    if chosen is None:
        raise AnomalyInjectionError("No inventory lot and incompatible zone pair found.")

    assignment_id, lot_id, original_zone, quantity, unit, stored_from, wrong_zone, allowed = chosen
    incident_start = max(start + timedelta(hours=1), stored_from + timedelta(minutes=5))
    incident_end = incident_start + timedelta(minutes=30)
    if incident_end >= end:
        raise AnomalyInjectionError("Telemetry window is too short for the incompatible storage scenario.")

    con.execute(
        "UPDATE lot_zone_assignment SET stored_to = ?, status = 'ENDED' WHERE assignment_id = ?",
        [incident_start, assignment_id],
    )
    con.execute(
        """
        INSERT INTO lot_zone_assignment (
            assignment_id, lot_id, zone_id, assigned_quantity, quantity_unit,
            stored_from, stored_to, assignment_reason, status, created_at
        )
        VALUES ('LZA-ANOM-000001', ?, ?, ?, ?, ?, ?, 'ANOMALY_INJECTION', 'ENDED', ?)
        """,
        [lot_id, wrong_zone, quantity, unit, incident_start, incident_end, incident_start],
    )
    con.execute(
        """
        INSERT INTO lot_zone_assignment (
            assignment_id, lot_id, zone_id, assigned_quantity, quantity_unit,
            stored_from, stored_to, assignment_reason, status, created_at
        )
        VALUES ('LZA-RESTORE-000001', ?, ?, ?, ?, ?, NULL, 'RESTORE_AFTER_ANOMALY', 'ACTIVE', ?)
        """,
        [lot_id, original_zone, quantity, unit, incident_end, incident_end],
    )
    return _scenario(
        7, "INCOMPATIBLE_LOT_ZONE", "INVENTORY_LOT", lot_id,
        "INCOMPATIBLE_STORAGE", "HIGH", wrong_zone, incident_start, incident_end,
        [lot_id],
        f"Lot was deliberately moved from an allowed zone type {allowed} to incompatible zone {wrong_zone} for 30 minutes, then restored.",
    )


def _inject_invalid_sensor_mapping(
    con: duckdb.DuckDBPyConnection, start: datetime, end: datetime
) -> dict[str, str]:
    row = con.execute(
        """
        SELECT s.sensor_id, a.zone_id, z.warehouse_id
        FROM sensor s
        JOIN sensor_zone_assignment a USING (sensor_id)
        JOIN zone z ON z.zone_id = a.zone_id
        WHERE a.valid_to IS NULL
          -- Keep this mapping scenario independent from other injected sensor events.
          AND NOT EXISTS (
              SELECT 1
              FROM sensor_reading_revision rev
              JOIN sensor_reading r USING (reading_id)
              WHERE r.sensor_id = s.sensor_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM sensor_health_event health
              WHERE health.sensor_id = s.sensor_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM sensor_zone_assignment other
              WHERE other.sensor_id = s.sensor_id
                AND other.zone_id <> a.zone_id
                AND a.valid_from < COALESCE(other.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
                AND other.valid_from < COALESCE(a.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
          )
        ORDER BY s.sensor_id
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise AnomalyInjectionError("No active sensor assignment found for mapping exception.")
    sensor_id, original_zone, warehouse_id = row
    other_zone = con.execute(
        """
        SELECT zone_id FROM zone
        WHERE warehouse_id = ? AND zone_id <> ?
        ORDER BY zone_id LIMIT 1
        """,
        [warehouse_id, original_zone],
    ).fetchone()
    if other_zone is None:
        other_zone = con.execute(
            "SELECT zone_id FROM zone WHERE zone_id <> ? ORDER BY zone_id LIMIT 1",
            [original_zone],
        ).fetchone()
    if other_zone is None:
        raise AnomalyInjectionError("At least two zones are required for mapping exception.")
    con.execute(
        """
        INSERT INTO sensor_zone_assignment (
            assignment_id, sensor_id, zone_id, valid_from, valid_to,
            rationale, is_approved, created_at
        )
        VALUES ('SZA-ANOM-000001', ?, ?, ?, ?, NULL, FALSE, ?)
        """,
        [sensor_id, other_zone[0], start, end + timedelta(seconds=300), start],
    )
    return _scenario(
        8, "SENSOR_MAPPED_TO_MULTIPLE_ZONES", "SENSOR", sensor_id,
        "INVALID_SENSOR_MAPPING", "HIGH", other_zone[0], start, end,
        [], "An overlapping second zone assignment was inserted without rationale or approval.",
    )


def _inject_alarm_without_lot_links(
    con: duckdb.DuckDBPyConnection,
    temperature_context: dict[str, Any],
) -> dict[str, str]:
    candidate = temperature_context["candidate"]
    start_at = temperature_context["start"]
    end_at = temperature_context["end"]
    zone_id = candidate["zone_id"]
    sensor_id = candidate["sensor_id"]
    lot_ids = _lots_for_zone(con, zone_id, start_at, end_at)
    if not lot_ids:
        raise AnomalyInjectionError("Selected temperature alarm has no overlapping inventory lots.")
    alarm_id = "ALM-ANOM-000001"
    detected_at = start_at + timedelta(minutes=15)
    con.execute(
        """
        INSERT INTO alarm (
            alarm_id, rule_id, alarm_type, severity, zone_id, sensor_id,
            detected_at, event_start_at, event_end_at, title, description,
            status, impact_assessment_status, source_event_key,
            created_at, updated_at
        )
        VALUES (?, 'TEMP_HIGH_V1', 'TEMPERATURE_EXCURSION', 'HIGH', ?, ?,
                ?, ?, ?, ?, ?, 'OPEN', 'FAILED', ?, ?, ?)
        """,
        [
            alarm_id, zone_id, sensor_id, detected_at, start_at, end_at,
            "Temperature excursion with incomplete inventory impact",
            "Controlled negative scenario: the alarm has overlapping inventory lots but no AlarmInventoryImpact rows.",
            "SCENARIO-ALARM-MISSING-LOT-LINK-001", detected_at, detected_at,
        ],
    )
    first_reading_id = candidate["readings"][0][0]
    con.execute(
        """
        INSERT INTO alarm_evidence (
            evidence_id, alarm_id, evidence_type, sensor_reading_id,
            observed_at, evidence_note
        )
        VALUES ('EVD-ANOM-ALM-001', ?, 'SENSOR_READING', ?, ?, ?)
        """,
        [alarm_id, first_reading_id, start_at, "Source reading evidence for the deliberately incomplete alarm."],
    )
    con.execute(
        """
        INSERT INTO alarm_status_history (
            history_id, alarm_id, previous_status, new_status,
            changed_at, changed_by, reason
        )
        VALUES ('HST-ANOM-ALM-001', ?, NULL, 'OPEN', ?, 'ANOMALY_INJECTOR', 'Initial status for controlled negative scenario.')
        """,
        [alarm_id, detected_at],
    )
    return _scenario(
        9, "ALARM_NOT_LINKED_TO_AFFECTED_LOTS", "ALARM", alarm_id,
        "INCOMPLETE_IMPACT_ASSESSMENT", "HIGH", zone_id, start_at, end_at,
        lot_ids, "The alarm overlaps stored lots in its zone, but its AlarmInventoryImpact bridge contains zero links.",
    )


def _preflight(con: duckdb.DuckDBPyConnection) -> None:
    for table in (
        "sensor_reading", "door_event", "lot_zone_assignment",
        "sensor_zone_assignment", "inventory_lot", "sensor",
    ):
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count == 0:
            raise AnomalyInjectionError(f"Required input table {table} is empty.")
    for table in ("sensor_reading_revision", "sensor_reading_gap", "sensor_health_event"):
        if con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            raise AnomalyInjectionError(f"{table} already contains anomaly records; use a fresh baseline database.")
    if con.execute("SELECT COUNT(*) FROM alarm WHERE alarm_id LIKE 'ALM-ANOM-%'").fetchone()[0]:
        raise AnomalyInjectionError("Anomaly alarm already exists; use a fresh baseline database.")
    if con.execute("SELECT COUNT(*) FROM sensor_zone_assignment WHERE assignment_id LIKE 'SZA-ANOM-%'").fetchone()[0]:
        raise AnomalyInjectionError("Anomaly sensor mapping already exists; use a fresh baseline database.")
    if con.execute("SELECT COUNT(*) FROM lot_zone_assignment WHERE assignment_id LIKE 'LZA-ANOM-%'").fetchone()[0]:
        raise AnomalyInjectionError("Anomaly lot assignment already exists; use a fresh baseline database.")


def inject_anomalies(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    profile: str | None = None,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
    generated_root: str | Path = DEFAULT_GENERATED_ROOT,
    ground_truth_root: str | Path = DEFAULT_GROUND_TRUTH_ROOT,
    report_root: str | Path = DEFAULT_REPORT_ROOT,
) -> dict[str, Any]:
    """Inject nine deterministic anomaly scenarios and export separate ground truth."""
    settings = load_settings(settings_path)
    selected_profile = profile or settings["generation"]["profile"]
    if selected_profile not in settings["profiles"]:
        raise AnomalyInjectionError(f"Unknown profile: {selected_profile}")
    summary = _telemetry_summary(selected_profile, generated_root)
    coverage_start = _utc(summary["coverage_start"])
    coverage_end = _utc(summary["coverage_end"])
    if (coverage_end - coverage_start) < timedelta(hours=4):
        raise AnomalyInjectionError("Telemetry coverage must be at least four hours for all scenarios.")

    db_path = Path(database_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"DuckDB database not found: {db_path}")
    con = duckdb.connect(str(db_path))
    temp_gt: Path | None = None
    temp_manifest: Path | None = None
    try:
        _preflight(con)
        con.execute("BEGIN TRANSACTION")
        scenarios: list[dict[str, str]] = []

        temp_scenario, temp_context = _inject_temperature(con, coverage_start, coverage_end)
        scenarios.append(temp_scenario)
        scenarios.append(_inject_humidity(con, coverage_start, coverage_end))
        scenarios.append(_inject_occupancy(con, coverage_start, coverage_end))
        scenarios.append(_inject_door_open(con, coverage_start, coverage_end))

        outage_scenario, missing_count = _inject_outage(con, coverage_start, coverage_end)
        scenarios.append(outage_scenario)
        drift_scenario, drift_revisions = _inject_drift(con, coverage_start, coverage_end)
        scenarios.append(drift_scenario)
        scenarios.append(_inject_incompatible_storage(con, coverage_start, coverage_end))
        scenarios.append(_inject_invalid_sensor_mapping(con, coverage_start, coverage_end))
        scenarios.append(_inject_alarm_without_lot_links(con, temp_context))

        ground_truth_path = Path(ground_truth_root) / selected_profile / "ground_truth.csv"
        temp_gt, ground_truth_hash = _write_csv_temp(scenarios, ground_truth_path)

        revision_count = con.execute("SELECT COUNT(*) FROM sensor_reading_revision").fetchone()[0]
        gap_count = con.execute("SELECT COUNT(*) FROM sensor_reading_gap").fetchone()[0]
        health_count = con.execute("SELECT COUNT(*) FROM sensor_health_event").fetchone()[0]
        door_anomaly_count = con.execute(
            "SELECT COUNT(*) FROM door_event WHERE source_system = 'ANOMALY_INJECTOR'"
        ).fetchone()[0]
        anomaly_mapping_count = con.execute(
            "SELECT COUNT(*) FROM sensor_zone_assignment WHERE assignment_id LIKE 'SZA-ANOM-%'"
        ).fetchone()[0]
        anomaly_lot_assignment_count = con.execute(
            "SELECT COUNT(*) FROM lot_zone_assignment WHERE assignment_id LIKE 'LZA-ANOM-%'"
        ).fetchone()[0]
        alarm_link_count = con.execute(
            "SELECT COUNT(*) FROM alarm_inventory_impact WHERE alarm_id = 'ALM-ANOM-000001'"
        ).fetchone()[0]
        if alarm_link_count != 0:
            raise AnomalyInjectionError("Negative alarm-link scenario unexpectedly has impact links.")

        manifest = {
            "project": "ColdWatch AI",
            "profile": selected_profile,
            "seed": settings["project"]["random_seed"],
            "generation_mode": "CONTROLLED_ANOMALY_INJECTION",
            "baseline_telemetry_summary": str(
                (Path(generated_root) / selected_profile / "telemetry" / "telemetry_summary.json")
            ),
            "ground_truth_file": str(ground_truth_path),
            "ground_truth_sha256": ground_truth_hash,
            "scenario_count": len(scenarios),
            "scenario_issue_types": [row["issue_type"] for row in scenarios],
            "operational_audit_counts": {
                "sensor_reading_revisions": revision_count,
                "sensor_reading_gaps": gap_count,
                "sensor_health_events": health_count,
                "anomaly_door_events": door_anomaly_count,
                "anomaly_sensor_zone_assignments": anomaly_mapping_count,
                "anomaly_lot_zone_assignments": anomaly_lot_assignment_count,
                "missing_alarm_inventory_links": alarm_link_count,
            },
            "notes": [
                "Ground truth is a separate artifact and is not loaded into operational DuckDB tables.",
                "Original values for revised sensor readings are retained in sensor_reading_revision.",
                "Two withheld expected observations are represented in sensor_reading_gap; baseline Parquet remains unchanged.",
                "Incompatible inventory placement is represented by closing the valid assignment, adding a timed bad assignment, and restoring the original zone.",
            ],
        }
        report_dir = Path(report_root)
        report_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = report_dir / f"anomaly_manifest_{selected_profile}.json"
        temp_manifest = manifest_path.with_suffix(".json.tmp")
        temp_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        con.execute("COMMIT")
        temp_gt.replace(ground_truth_path)
        temp_manifest.replace(manifest_path)
        return manifest
    except Exception:
        try:
            con.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        if temp_gt and temp_gt.exists():
            temp_gt.unlink()
        if temp_manifest and temp_manifest.exists():
            temp_manifest.unlink()
        raise
    finally:
        con.close()
