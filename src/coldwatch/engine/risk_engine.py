"""Deterministic warehouse risk detection and alarm persistence.

Detector inputs are operational DuckDB tables only. Ground-truth files are
never opened by this module.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import duckdb

from coldwatch.config import DEFAULT_SETTINGS_PATH, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "reports"

ALARM_TYPES = {
    "TEMPERATURE_EXCURSION",
    "HUMIDITY_EXCURSION",
    "OCCUPANCY_LIMIT",
    "DOOR_OPEN_TOO_LONG",
    "SENSOR_OUTAGE",
    "SENSOR_DRIFT",
    "INCOMPATIBLE_STORAGE",
    "INVALID_SENSOR_MAPPING",
    "INCOMPLETE_IMPACT_ASSESSMENT",
}


class RiskEngineError(RuntimeError):
    """Raised when risk detection or alarm persistence cannot complete."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_key(rule_id: str, entity_id: str, zone_id: str | None, start: datetime, end: datetime | None) -> str:
    raw = "|".join((rule_id, entity_id, zone_id or "", _iso(start) or "", _iso(end) or ""))
    return "CW:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20].upper()


def _lots_overlapping_zone_interval(
    connection: duckdb.DuckDBPyConnection,
    zone_id: str,
    start_at: datetime,
    end_at: datetime | None,
) -> list[str]:
    """Resolve inventory lots whose effective-dated zone assignment overlaps an event.

    Intervals use [start, end) semantics. An event without an explicit end is
    treated as a five-minute observation window, matching the telemetry cadence.
    """
    window_end = _utc(end_at) if end_at is not None else _utc(start_at) + timedelta(minutes=5)
    window_start = _utc(start_at)
    if window_end <= window_start:
        raise RiskEngineError("Inventory-impact interval must end after it starts.")
    rows = connection.execute(
        """
        SELECT DISTINCT lot_id
        FROM lot_zone_assignment
        WHERE zone_id = ?
          AND stored_from < ?
          AND (stored_to IS NULL OR stored_to > ?)
        ORDER BY lot_id
        """,
        [zone_id, window_end, window_start],
    ).fetchall()
    return [row[0] for row in rows]


def _detection(
    *,
    issue_type: str,
    alarm_type: str,
    rule_id: str,
    severity: str,
    entity_type: str,
    entity_id: str,
    zone_id: str | None,
    sensor_id: str | None,
    start_at: datetime,
    end_at: datetime | None,
    title: str,
    description: str,
    evidence: list[dict[str, Any]],
    affected_lot_ids: list[str] | None = None,
    existing_alarm_id: str | None = None,
) -> dict[str, Any]:
    event_key = _stable_key(rule_id, entity_id, zone_id, start_at, end_at)
    unique_evidence = {
        (item["evidence_type"], item["source_id"]): item
        for item in evidence
    }
    return {
        "issue_type": issue_type,
        "alarm_type": alarm_type,
        "rule_id": rule_id,
        "severity": severity,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "zone_id": zone_id,
        "sensor_id": sensor_id,
        "event_start_at": _utc(start_at),
        "event_end_at": _utc(end_at) if end_at else None,
        "title": title,
        "description": description,
        "source_event_key": event_key,
        "evidence": sorted(
            unique_evidence.values(),
            key=lambda item: (item["observed_at"], item["evidence_type"], item["source_id"]),
        ),
        "affected_lot_ids": sorted(set(affected_lot_ids or [])),
        "existing_alarm_id": existing_alarm_id,
    }


def _unambiguous_sensor_map_cte() -> str:
    """Return a CTE excluding sensors with overlapping mappings to different zones."""
    return """
        WITH unambiguous_sensor_map AS (
            SELECT a.*
            FROM sensor_zone_assignment a
            WHERE NOT EXISTS (
                SELECT 1
                FROM sensor_zone_assignment b
                WHERE b.sensor_id = a.sensor_id
                  AND b.assignment_id <> a.assignment_id
                  AND b.zone_id <> a.zone_id
                  AND a.valid_from < COALESCE(b.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
                  AND b.valid_from < COALESCE(a.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
            )
        )
    """


def _coalesce_temporal_rows(rows: list[tuple[Any, ...]], *, interval_seconds: int, min_count: int) -> list[dict[str, Any]]:
    """Coalesce many-lot joins into unique timestamps and contiguous breach runs.

    Row layout: sensor_id, zone_id, observed_at, source_id, lot_id, evidence_type.
    """
    grouped: dict[tuple[str, str, datetime], dict[str, Any]] = {}
    for sensor_id, zone_id, observed_at, source_id, lot_id, evidence_type in rows:
        timestamp = _utc(observed_at)
        key = (sensor_id, zone_id, timestamp)
        point = grouped.setdefault(
            key,
            {
                "sensor_id": sensor_id,
                "zone_id": zone_id,
                "observed_at": timestamp,
                "evidence": {},
                "lot_ids": set(),
            },
        )
        point["evidence"][(evidence_type, source_id)] = {
            "evidence_type": evidence_type,
            "source_id": source_id,
            "observed_at": timestamp,
        }
        if lot_id:
            point["lot_ids"].add(lot_id)

    by_sensor_zone: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for point in grouped.values():
        by_sensor_zone[(point["sensor_id"], point["zone_id"])].append(point)

    findings: list[dict[str, Any]] = []
    for (sensor_id, zone_id), points in by_sensor_zone.items():
        points.sort(key=lambda item: item["observed_at"])
        run: list[dict[str, Any]] = []
        for point in points:
            if run and (point["observed_at"] - run[-1]["observed_at"]).total_seconds() != interval_seconds:
                if len(run) >= min_count:
                    findings.append(_temporal_run(sensor_id, zone_id, run, interval_seconds))
                run = []
            run.append(point)
        if len(run) >= min_count:
            findings.append(_temporal_run(sensor_id, zone_id, run, interval_seconds))
    return findings


def _temporal_run(sensor_id: str, zone_id: str, points: list[dict[str, Any]], interval_seconds: int) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    lots: set[str] = set()
    for point in points:
        evidence.extend(point["evidence"].values())
        lots.update(point["lot_ids"])
    start_at = points[0]["observed_at"]
    end_at = points[-1]["observed_at"] + timedelta(seconds=interval_seconds)
    return {
        "sensor_id": sensor_id,
        "zone_id": zone_id,
        "start_at": start_at,
        "end_at": end_at,
        "evidence": evidence,
        "lot_ids": sorted(lots),
        "point_count": len(points),
    }


def _detect_environmental(
    connection: duckdb.DuckDBPyConnection,
    *,
    sensor_type: str,
    minimum_column: str,
    maximum_column: str,
    interval_seconds: int,
    min_count: int,
) -> list[dict[str, Any]]:
    if sensor_type not in {"TEMPERATURE", "HUMIDITY"}:
        raise RiskEngineError("Unsupported environmental sensor type.")
    allowed_columns = {"temperature_min_c", "temperature_max_c", "humidity_min_pct", "humidity_max_pct"}
    if minimum_column not in allowed_columns or maximum_column not in allowed_columns:
        raise RiskEngineError("Unsupported requirement threshold column.")

    sql = _unambiguous_sensor_map_cte() + f"""
        SELECT r.sensor_id, m.zone_id, r.observed_at, r.reading_id,
               lza.lot_id, 'SENSOR_READING'
        FROM sensor_reading r
        JOIN sensor s USING (sensor_id)
        JOIN unambiguous_sensor_map m
          ON m.sensor_id = r.sensor_id
         AND m.valid_from <= r.observed_at
         AND (m.valid_to IS NULL OR m.valid_to > r.observed_at)
        JOIN zone z
          ON z.zone_id = m.zone_id
        JOIN lot_zone_assignment lza
          ON lza.zone_id = m.zone_id
         AND lza.stored_from <= r.observed_at
         AND (lza.stored_to IS NULL OR lza.stored_to > r.observed_at)
        JOIN inventory_lot lot USING (lot_id)
        JOIN storage_requirement req USING (requirement_id)
        JOIN requirement_allowed_zone_type allowed
          ON allowed.requirement_id = req.requirement_id
         AND allowed.zone_type = z.zone_type
        WHERE s.sensor_type = ?
          AND r.quality_status = 'GOOD'
          AND req.{minimum_column} IS NOT NULL
          AND req.{maximum_column} IS NOT NULL
          AND (
              r.reading_value < req.{minimum_column}
              OR r.reading_value > req.{maximum_column}
          )
        ORDER BY r.sensor_id, m.zone_id, r.observed_at, lza.lot_id
    """
    rows = connection.execute(sql, [sensor_type]).fetchall()
    runs = _coalesce_temporal_rows(
        rows, interval_seconds=interval_seconds, min_count=min_count
    )

    issue_type = "TEMPERATURE_EXCURSION" if sensor_type == "TEMPERATURE" else "HUMIDITY_EXCURSION"
    rule_id = "TEMP_STORAGE_RANGE_V1" if sensor_type == "TEMPERATURE" else "HUMIDITY_STORAGE_RANGE_V1"
    detections = []
    for run in runs:
        entity_id = run["sensor_id"]
        unit = "degC" if sensor_type == "TEMPERATURE" else "% RH"
        title = "Temperature outside lot storage requirement" if sensor_type == "TEMPERATURE" else "Humidity outside lot storage requirement"
        detections.append(_detection(
            issue_type=issue_type,
            alarm_type=issue_type,
            rule_id=rule_id,
            severity="HIGH",
            entity_type="SENSOR",
            entity_id=entity_id,
            zone_id=run["zone_id"],
            sensor_id=entity_id,
            start_at=run["start_at"],
            end_at=run["end_at"],
            title=title,
            description=(
                f"{run['point_count']} consecutive {sensor_type.lower()} observations "
                f"breached at least one overlapping lot's configured storage range ({unit})."
            ),
            evidence=run["evidence"],
            affected_lot_ids=run["lot_ids"],
        ))
    return detections


def _detect_occupancy(
    connection: duckdb.DuckDBPyConnection,
    *,
    interval_seconds: int,
    min_count: int,
    warning_fraction: float,
) -> list[dict[str, Any]]:
    sql = _unambiguous_sensor_map_cte() + """
        SELECT r.sensor_id, m.zone_id, r.observed_at, r.reading_id,
               NULL, 'SENSOR_READING'
        FROM sensor_reading r
        JOIN sensor s USING (sensor_id)
        JOIN unambiguous_sensor_map m
          ON m.sensor_id = r.sensor_id
         AND m.valid_from <= r.observed_at
         AND (m.valid_to IS NULL OR m.valid_to > r.observed_at)
        JOIN zone z ON z.zone_id = m.zone_id
        WHERE s.sensor_type = 'OCCUPANCY'
          AND r.quality_status = 'GOOD'
          AND r.reading_value > z.occupancy_warning_limit_pct * ?
        ORDER BY r.sensor_id, m.zone_id, r.observed_at
    """
    rows = connection.execute(sql, [warning_fraction]).fetchall()
    runs = _coalesce_temporal_rows(
        rows, interval_seconds=interval_seconds, min_count=min_count
    )
    return [
        _detection(
            issue_type="OCCUPANCY_LIMIT",
            alarm_type="OCCUPANCY_LIMIT",
            rule_id="ZONE_OCCUPANCY_WARNING_V1",
            severity="MEDIUM",
            entity_type="SENSOR",
            entity_id=run["sensor_id"],
            zone_id=run["zone_id"],
            sensor_id=run["sensor_id"],
            start_at=run["start_at"],
            end_at=run["end_at"],
            title="Zone occupancy threshold exceeded",
            description=(
                f"{run['point_count']} consecutive occupancy readings exceeded "
                f"{warning_fraction:.0%} of the configured zone warning threshold."
            ),
            evidence=run["evidence"],
            affected_lot_ids=_lots_overlapping_zone_interval(
                connection, run["zone_id"], run["start_at"], run["end_at"]
            ),
        )
        for run in runs
    ]


def _detect_door_events(
    connection: duckdb.DuckDBPyConnection,
    *,
    warning_minutes: float,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH ordered AS (
            SELECT door_event_id, zone_id, door_id, event_type, event_at,
                   LEAD(door_event_id) OVER (
                       PARTITION BY zone_id, door_id ORDER BY event_at
                   ) AS next_event_id,
                   LEAD(event_type) OVER (
                       PARTITION BY zone_id, door_id ORDER BY event_at
                   ) AS next_event_type,
                   LEAD(event_at) OVER (
                       PARTITION BY zone_id, door_id ORDER BY event_at
                   ) AS next_event_at
            FROM door_event
        )
        SELECT door_event_id, next_event_id, zone_id, door_id, event_at, next_event_at
        FROM ordered
        WHERE event_type = 'OPEN'
          AND next_event_type = 'CLOSE'
          AND date_diff('second', event_at, next_event_at) > ?
        ORDER BY event_at
        """,
        [int(warning_minutes * 60)],
    ).fetchall()

    detections = []
    for open_id, close_id, zone_id, door_id, open_at, close_at in rows:
        start_at, end_at = _utc(open_at), _utc(close_at)
        detections.append(_detection(
            issue_type="DOOR_OPEN_TOO_LONG",
            alarm_type="DOOR_OPEN_TOO_LONG",
            rule_id="DOOR_OPEN_DURATION_V1",
            severity="HIGH",
            entity_type="DOOR",
            entity_id=f"{zone_id}:{door_id}",
            zone_id=zone_id,
            sensor_id=None,
            start_at=start_at,
            end_at=end_at,
            title="Door remained open beyond warning duration",
            description=(
                f"Door {door_id} remained open for "
                f"{(end_at - start_at).total_seconds() / 60:.1f} minutes; "
                f"configured warning is {warning_minutes:.1f} minutes."
            ),
            evidence=[
                {"evidence_type": "DOOR_EVENT", "source_id": open_id, "observed_at": start_at},
                {"evidence_type": "DOOR_EVENT", "source_id": close_id, "observed_at": end_at},
            ],
            affected_lot_ids=_lots_overlapping_zone_interval(
                connection, zone_id, start_at, end_at
            ),
        ))
    return detections


def _detect_outages(
    connection: duckdb.DuckDBPyConnection,
    *,
    minimum_missed_intervals: int,
) -> list[dict[str, Any]]:
    sql = _unambiguous_sensor_map_cte() + """
        SELECT g.sensor_id, m.zone_id, g.expected_at, g.gap_id,
               NULL, 'SENSOR_HEALTH_EVENT', g.expected_interval_seconds
        FROM sensor_reading_gap g
        JOIN unambiguous_sensor_map m
          ON m.sensor_id = g.sensor_id
         AND m.valid_from <= g.expected_at
         AND (m.valid_to IS NULL OR m.valid_to > g.expected_at)
        WHERE g.gap_status IN ('OPEN', 'RECOVERED')
        ORDER BY g.sensor_id, m.zone_id, g.expected_at
    """
    rows = connection.execute(sql).fetchall()
    grouped: dict[tuple[str, str], list[tuple[datetime, str, int]]] = defaultdict(list)
    for sensor_id, zone_id, expected_at, gap_id, _lot, _evidence_type, interval in rows:
        grouped[(sensor_id, zone_id)].append((_utc(expected_at), gap_id, int(interval)))

    detections = []
    for (sensor_id, zone_id), gaps in grouped.items():
        gaps.sort(key=lambda item: item[0])
        run: list[tuple[datetime, str, int]] = []
        for gap in gaps:
            if run and (
                (gap[0] - run[-1][0]).total_seconds() != run[-1][2]
                or gap[2] != run[-1][2]
            ):
                if len(run) >= minimum_missed_intervals:
                    detections.append(_outage_detection(connection, sensor_id, zone_id, run))
                run = []
            run.append(gap)
        if len(run) >= minimum_missed_intervals:
            detections.append(_outage_detection(connection, sensor_id, zone_id, run))
    return detections


def _outage_detection(
    connection: duckdb.DuckDBPyConnection,
    sensor_id: str,
    zone_id: str,
    gaps: list[tuple[datetime, str, int]],
) -> dict[str, Any]:
    start_at = gaps[0][0]
    end_at = gaps[-1][0] + timedelta(seconds=gaps[-1][2])
    health_rows = connection.execute(
        """
        SELECT health_event_id, detected_at
        FROM sensor_health_event
        WHERE sensor_id = ? AND event_type = 'OUTAGE'
          AND started_at <= ? AND (ended_at IS NULL OR ended_at >= ?)
        ORDER BY detected_at
        """,
        [sensor_id, start_at, end_at],
    ).fetchall()
    evidence = [
        {
            "evidence_type": "SENSOR_HEALTH_EVENT",
            "source_id": health_id,
            "observed_at": _utc(detected_at),
        }
        for health_id, detected_at in health_rows
    ]
    return _detection(
        issue_type="SENSOR_OUTAGE",
        alarm_type="SENSOR_OUTAGE",
        rule_id="SENSOR_MISSING_INTERVALS_V1",
        severity="HIGH",
        entity_type="SENSOR",
        entity_id=sensor_id,
        zone_id=zone_id,
        sensor_id=sensor_id,
        start_at=start_at,
        end_at=end_at,
        title="Sensor telemetry outage detected",
        description=f"{len(gaps)} consecutive expected telemetry intervals are missing.",
        evidence=evidence,
        affected_lot_ids=_lots_overlapping_zone_interval(
            connection, zone_id, start_at, end_at
        ),
    )


def _detect_drift(
    connection: duckdb.DuckDBPyConnection,
    *,
    tolerance: float,
    window_size: int = 8,
) -> list[dict[str, Any]]:
    sql = _unambiguous_sensor_map_cte() + """
        SELECT r.sensor_id, m.zone_id, r.reading_id, r.observed_at,
               CAST(r.reading_value AS DOUBLE) - MEDIAN(CAST(peer_r.reading_value AS DOUBLE)) AS residual
        FROM sensor_reading r
        JOIN sensor s
          ON s.sensor_id = r.sensor_id AND s.sensor_type = 'TEMPERATURE'
        JOIN unambiguous_sensor_map m
          ON m.sensor_id = r.sensor_id
         AND m.valid_from <= r.observed_at
         AND (m.valid_to IS NULL OR m.valid_to > r.observed_at)
        JOIN sensor peer
          ON peer.sensor_type = 'TEMPERATURE' AND peer.sensor_id <> r.sensor_id
        JOIN unambiguous_sensor_map peer_map
          ON peer_map.sensor_id = peer.sensor_id
         AND peer_map.zone_id = m.zone_id
         AND peer_map.valid_from <= r.observed_at
         AND (peer_map.valid_to IS NULL OR peer_map.valid_to > r.observed_at)
        JOIN sensor_reading peer_r
          ON peer_r.sensor_id = peer.sensor_id
         AND peer_r.observed_at = r.observed_at
         AND peer_r.quality_status = 'GOOD'
        WHERE r.quality_status = 'GOOD'
        GROUP BY r.sensor_id, m.zone_id, r.reading_id, r.observed_at, r.reading_value
        ORDER BY r.sensor_id, m.zone_id, r.observed_at
    """
    rows = connection.execute(sql).fetchall()
    grouped: dict[tuple[str, str], list[tuple[str, datetime, float]]] = defaultdict(list)
    for sensor_id, zone_id, reading_id, observed_at, residual in rows:
        grouped[(sensor_id, zone_id)].append((reading_id, _utc(observed_at), float(residual)))

    detections = []
    for (sensor_id, zone_id), points in grouped.items():
        points.sort(key=lambda item: item[1])
        for offset in range(0, len(points) - window_size + 1):
            window = points[offset:offset + window_size]
            times = [item[1] for item in window]
            if any((times[i + 1] - times[i]).total_seconds() != 300 for i in range(window_size - 1)):
                continue
            residuals = [item[2] for item in window]
            net_change = residuals[-1] - residuals[0]
            x_mean = (window_size - 1) / 2
            y_mean = sum(residuals) / window_size
            denominator = sum((i - x_mean) ** 2 for i in range(window_size))
            slope = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(residuals)) / denominator

            # A gradual drift should move the candidate sensor consistently
            # above its peer. The reciprocal peer residual is negative, even
            # when it rises back toward zero after the other sensor recovers.
            # Require a positive sustained residual to avoid flagging that peer.
            if y_mean <= tolerance * 0.25:
                continue

            # Excursion injection creates a sharp step in readings; it must not
            # be classified again as gradual sensor drift. A genuine drift in
            # this project advances in small increments over the full window.
            largest_step = max(
                abs(residuals[index + 1] - residuals[index])
                for index in range(window_size - 1)
            )
            if largest_step > tolerance * 0.75:
                continue

            if net_change < tolerance or slope < (tolerance / (window_size - 1)) * 0.8:
                continue

            start_at = times[0]
            end_at = times[-1] + timedelta(minutes=5)
            evidence = [
                {"evidence_type": "SENSOR_READING", "source_id": item[0], "observed_at": item[1]}
                for item in window
            ]
            detections.append(_detection(
                issue_type="SENSOR_DRIFT",
                alarm_type="SENSOR_DRIFT",
                rule_id="REDUNDANT_SENSOR_DRIFT_V1",
                severity="MEDIUM",
                entity_type="SENSOR",
                entity_id=sensor_id,
                zone_id=zone_id,
                sensor_id=sensor_id,
                start_at=start_at,
                end_at=end_at,
                title="Sensor drift relative to redundant zone sensor",
                description=(
                    f"Across {window_size} consecutive readings, the sensor residual "
                    f"relative to peer sensors increased by {net_change:.2f} degC "
                    f"(configured drift tolerance {tolerance:.2f} degC)."
                ),
                evidence=evidence,
            ))
            break
    return detections


def _detect_incompatible_storage(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT lza.assignment_id, lza.lot_id, lza.zone_id,
               lza.stored_from, lza.stored_to, z.zone_type
        FROM lot_zone_assignment lza
        JOIN inventory_lot lot USING (lot_id)
        JOIN zone z USING (zone_id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM requirement_allowed_zone_type allowed
            WHERE allowed.requirement_id = lot.requirement_id
              AND allowed.zone_type = z.zone_type
        )
        ORDER BY lza.stored_from, lza.assignment_id
        """
    ).fetchall()
    detections = []
    for assignment_id, lot_id, zone_id, start_at, end_at, zone_type in rows:
        start = _utc(start_at)
        end = _utc(end_at) if end_at else start + timedelta(minutes=5)
        detections.append(_detection(
            issue_type="INCOMPATIBLE_LOT_ZONE",
            alarm_type="INCOMPATIBLE_STORAGE",
            rule_id="LOT_ZONE_COMPATIBILITY_V1",
            severity="HIGH",
            entity_type="INVENTORY_LOT",
            entity_id=lot_id,
            zone_id=zone_id,
            sensor_id=None,
            start_at=start,
            end_at=end,
            title="Inventory lot stored in an incompatible zone",
            description=f"Lot {lot_id} is placed in zone type {zone_type}, which is not allowed by its storage requirement.",
            evidence=[{
                "evidence_type": "LOT_ZONE_ASSIGNMENT",
                "source_id": assignment_id,
                "observed_at": start,
            }],
            affected_lot_ids=[lot_id],
        ))
    return detections


def _detect_invalid_sensor_mappings(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT a.assignment_id, a.sensor_id, a.zone_id,
               GREATEST(a.valid_from, b.valid_from) AS overlap_start,
               LEAST(
                   COALESCE(a.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00'),
                   COALESCE(b.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
               ) AS overlap_end
        FROM sensor_zone_assignment a
        JOIN sensor_zone_assignment b
          ON b.sensor_id = a.sensor_id
         AND b.zone_id <> a.zone_id
         AND b.assignment_id <> a.assignment_id
         AND a.valid_from < COALESCE(b.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
         AND b.valid_from < COALESCE(a.valid_to, TIMESTAMPTZ '9999-12-31 00:00:00+00')
        WHERE a.is_approved = FALSE
          AND NULLIF(TRIM(a.rationale), '') IS NULL
          AND a.valid_to IS NOT NULL
        ORDER BY a.valid_from, a.assignment_id
        """
    ).fetchall()
    detections = []
    for assignment_id, sensor_id, zone_id, start_at, end_at in rows:
        start, end = _utc(start_at), _utc(end_at)
        detections.append(_detection(
            issue_type="SENSOR_MAPPED_TO_MULTIPLE_ZONES",
            alarm_type="INVALID_SENSOR_MAPPING",
            rule_id="SENSOR_ZONE_MAPPING_OVERLAP_V1",
            severity="HIGH",
            entity_type="SENSOR",
            entity_id=sensor_id,
            zone_id=zone_id,
            sensor_id=sensor_id,
            start_at=start,
            end_at=end,
            title="Sensor has an unapproved overlapping zone mapping",
            description="A sensor is assigned to multiple zones over the same time interval without approval and rationale.",
            evidence=[{
                "evidence_type": "SENSOR_ZONE_ASSIGNMENT",
                "source_id": assignment_id,
                "observed_at": start,
            }],
        ))
    return detections


def _detect_incomplete_impact_assessments(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT a.alarm_id, a.alarm_type, a.severity, a.zone_id, a.sensor_id,
               a.event_start_at, a.event_end_at, a.title, a.description,
               array_agg(DISTINCT lza.lot_id) AS lot_ids
        FROM alarm a
        JOIN lot_zone_assignment lza
          ON lza.zone_id = a.zone_id
         AND lza.stored_from < COALESCE(a.event_end_at, a.event_start_at + INTERVAL '5 minutes')
         AND (lza.stored_to IS NULL OR lza.stored_to > a.event_start_at)
        WHERE NOT EXISTS (
            SELECT 1 FROM alarm_inventory_impact i WHERE i.alarm_id = a.alarm_id
        )
          AND a.impact_assessment_status = 'FAILED'
        GROUP BY a.alarm_id, a.alarm_type, a.severity, a.zone_id, a.sensor_id,
                 a.event_start_at, a.event_end_at, a.title, a.description
        ORDER BY MIN(a.detected_at)
        """
    ).fetchall()
    detections = []
    for alarm_id, alarm_type, severity, zone_id, sensor_id, start_at, end_at, title, description, lot_ids in rows:
        start = _utc(start_at)
        end = _utc(end_at) if end_at else start + timedelta(minutes=5)
        evidence_rows = connection.execute(
            """
            SELECT evidence_type, sensor_reading_id, door_event_id, health_event_id,
                   sensor_zone_assignment_id, lot_zone_assignment_id, observed_at
            FROM alarm_evidence
            WHERE alarm_id = ?
            ORDER BY observed_at
            """,
            [alarm_id],
        ).fetchall()
        evidence = []
        source_columns = {
            "SENSOR_READING": 1,
            "DOOR_EVENT": 2,
            "SENSOR_HEALTH_EVENT": 3,
            "SENSOR_ZONE_ASSIGNMENT": 4,
            "LOT_ZONE_ASSIGNMENT": 5,
        }
        for item in evidence_rows:
            evidence_type = item[0]
            source_id = item[source_columns[evidence_type]]
            if source_id:
                evidence.append({
                    "evidence_type": evidence_type,
                    "source_id": source_id,
                    "observed_at": _utc(item[6]),
                })
        detections.append(_detection(
            issue_type="ALARM_NOT_LINKED_TO_AFFECTED_LOTS",
            alarm_type="INCOMPLETE_IMPACT_ASSESSMENT",
            rule_id="ALARM_INVENTORY_IMPACT_COMPLETENESS_V1",
            severity="HIGH",
            entity_type="ALARM",
            entity_id=alarm_id,
            zone_id=zone_id,
            sensor_id=sensor_id,
            start_at=start,
            end_at=end,
            title="Alarm has no inventory-impact links",
            description=(
                f"Existing alarm {alarm_id} overlaps {len(lot_ids or [])} lot assignments "
                "but has no AlarmInventoryImpact records. The existing alarm was not modified."
            ),
            evidence=evidence,
            affected_lot_ids=list(lot_ids or []),
            existing_alarm_id=alarm_id,
        ))
    return detections


def detect_risks(
    connection: duckdb.DuckDBPyConnection,
    *,
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run all deterministic detectors against operational data only."""
    interval_seconds = int(connection.execute(
        "SELECT MIN(expected_interval_seconds) FROM sensor"
    ).fetchone()[0] or 300)
    min_count = int(rules["consecutive_breach_readings_to_open_alarm"])
    findings: list[dict[str, Any]] = []
    findings.extend(_detect_environmental(
        connection, sensor_type="TEMPERATURE",
        minimum_column="temperature_min_c", maximum_column="temperature_max_c",
        interval_seconds=interval_seconds, min_count=min_count,
    ))
    findings.extend(_detect_environmental(
        connection, sensor_type="HUMIDITY",
        minimum_column="humidity_min_pct", maximum_column="humidity_max_pct",
        interval_seconds=interval_seconds, min_count=min_count,
    ))
    findings.extend(_detect_occupancy(
        connection, interval_seconds=interval_seconds, min_count=min_count,
        warning_fraction=float(rules["occupancy_warning_fraction"]),
    ))
    findings.extend(_detect_door_events(
        connection, warning_minutes=float(rules["door_open_warning_minutes"]),
    ))
    findings.extend(_detect_outages(
        connection, minimum_missed_intervals=int(rules["sensor_missing_interval_multiplier"]),
    ))
    findings.extend(_detect_drift(
        connection, tolerance=float(rules["sensor_drift_tolerance"]),
    ))
    findings.extend(_detect_incompatible_storage(connection))
    findings.extend(_detect_invalid_sensor_mappings(connection))
    findings.extend(_detect_incomplete_impact_assessments(connection))
    findings.sort(key=lambda item: (
        item["event_start_at"], item["alarm_type"], item["entity_id"]
    ))
    return findings


def _persist_detections(
    database_path: Path,
    detections: list[dict[str, Any]],
) -> dict[str, Any]:
    connection = duckdb.connect(str(database_path))
    created_alarm_ids: list[str] = []
    reused_alarm_ids: list[str] = []
    impact_rows = 0
    evidence_rows = 0
    skipped_findings: list[str] = []
    try:
        connection.execute("BEGIN TRANSACTION")
        for finding in detections:
            if finding["alarm_type"] == "INCOMPLETE_IMPACT_ASSESSMENT":
                skipped_findings.append(finding["source_event_key"])
                continue

            existing = connection.execute(
                "SELECT alarm_id FROM alarm WHERE source_event_key = ?",
                [finding["source_event_key"]],
            ).fetchone()
            if existing:
                alarm_id = existing[0]
                reused_alarm_ids.append(alarm_id)
                continue

            # A pre-seeded negative-control alarm may represent exactly the same
            # source incident. Do not duplicate it or silently repair its missing
            # impact links; the detector reports that incompleteness separately.
            same_incident = connection.execute(
                """
                SELECT alarm_id
                FROM alarm
                WHERE alarm_type = ?
                  AND zone_id IS NOT DISTINCT FROM ?
                  AND sensor_id IS NOT DISTINCT FROM ?
                  AND event_start_at = ?
                  AND event_end_at IS NOT DISTINCT FROM ?
                ORDER BY created_at
                LIMIT 1
                """,
                [
                    finding["alarm_type"], finding["zone_id"], finding["sensor_id"],
                    finding["event_start_at"], finding["event_end_at"],
                ],
            ).fetchone()
            if same_incident:
                reused_alarm_ids.append(same_incident[0])
                continue

            alarm_id = _stable_id("ALM-CW-", finding["source_event_key"])
            detected_at = finding["event_end_at"] or finding["event_start_at"]
            connection.execute(
                """
                INSERT INTO alarm (
                    alarm_id, rule_id, alarm_type, severity, zone_id, sensor_id,
                    detected_at, event_start_at, event_end_at, title, description,
                    status, impact_assessment_status, source_event_key,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 'IN_PROGRESS', ?, ?, ?)
                """,
                [
                    alarm_id, finding["rule_id"], finding["alarm_type"], finding["severity"],
                    finding["zone_id"], finding["sensor_id"], detected_at,
                    finding["event_start_at"], finding["event_end_at"],
                    finding["title"], finding["description"], finding["source_event_key"],
                    detected_at, detected_at,
                ],
            )
            created_alarm_ids.append(alarm_id)

            connection.execute(
                """
                INSERT INTO alarm_status_history (
                    history_id, alarm_id, previous_status, new_status,
                    changed_at, changed_by, reason
                )
                VALUES (?, ?, NULL, 'OPEN', ?, 'COLDWATCH_RULE_ENGINE', ?)
                """,
                [
                    _stable_id("HST-CW-", alarm_id),
                    alarm_id,
                    detected_at,
                    "Alarm created by deterministic ColdWatch risk rule.",
                ],
            )

            for item in finding["evidence"]:
                evidence_id = _stable_id(
                    "EVD-CW-", alarm_id + "|" + item["evidence_type"] + "|" + item["source_id"]
                )
                source_column = {
                    "SENSOR_READING": "sensor_reading_id",
                    "DOOR_EVENT": "door_event_id",
                    "SENSOR_HEALTH_EVENT": "health_event_id",
                    "SENSOR_ZONE_ASSIGNMENT": "sensor_zone_assignment_id",
                    "LOT_ZONE_ASSIGNMENT": "lot_zone_assignment_id",
                }.get(item["evidence_type"])
                if source_column is None:
                    continue
                connection.execute(
                    f"""
                    INSERT INTO alarm_evidence (
                        evidence_id, alarm_id, evidence_type, {source_column},
                        observed_at, evidence_note
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        evidence_id, alarm_id, item["evidence_type"], item["source_id"],
                        item["observed_at"], "Source evidence captured by deterministic risk engine.",
                    ],
                )
                evidence_rows += 1

            # A multi-zone sensor mapping is ambiguous: do not attribute
            # inventory exposure to either zone automatically.
            impacted = []
            if finding["zone_id"] and finding["issue_type"] != "SENSOR_MAPPED_TO_MULTIPLE_ZONES":
                overlap_end = finding["event_end_at"] or finding["event_start_at"] + timedelta(minutes=5)
                impact_sql = """
                    SELECT lza.assignment_id, lza.lot_id,
                           GREATEST(lza.stored_from, ?) AS exposure_start,
                           LEAST(COALESCE(lza.stored_to, ?), ?) AS exposure_end
                    FROM lot_zone_assignment lza
                    WHERE lza.zone_id = ?
                      AND lza.stored_from < ?
                      AND (lza.stored_to IS NULL OR lza.stored_to > ?)
                """
                parameters = [
                    finding["event_start_at"], overlap_end, overlap_end,
                    finding["zone_id"], overlap_end, finding["event_start_at"],
                ]
                if finding["affected_lot_ids"]:
                    impact_sql += " AND lza.lot_id IN (SELECT UNNEST(?))"
                    parameters.append(finding["affected_lot_ids"])
                impact_sql += " ORDER BY lza.lot_id, lza.assignment_id"
                impacted = connection.execute(impact_sql, parameters).fetchall()

            for assignment_id, lot_id, exposure_start, exposure_end in impacted:
                if exposure_end <= exposure_start:
                    continue
                impact_id = _stable_id("IMP-CW-", alarm_id + "|" + assignment_id)
                duration = (exposure_end - exposure_start).total_seconds() / 60.0
                connection.execute(
                    """
                    INSERT INTO alarm_inventory_impact (
                        impact_id, alarm_id, lot_id, lot_zone_assignment_id,
                        exposure_start_at, exposure_end_at, exposure_duration_minutes,
                        impact_status, impact_reason, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'POTENTIALLY_AFFECTED', ?, ?)
                    """,
                    [
                        impact_id, alarm_id, lot_id, assignment_id,
                        exposure_start, exposure_end, duration,
                        "Lot assignment overlaps the alarm's zone and event interval.",
                        detected_at,
                    ],
                )
                impact_rows += 1

            connection.execute(
                """
                UPDATE alarm
                SET impact_assessment_status = ?, updated_at = ?
                WHERE alarm_id = ?
                """,
                ["COMPLETE", detected_at, alarm_id],
            )

        connection.execute("COMMIT")
    except Exception as exc:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise RiskEngineError(f"Could not persist risk findings: {exc}") from exc
    finally:
        connection.close()

    return {
        "alarms_created": len(created_alarm_ids),
        "alarms_reused": len(reused_alarm_ids),
        "alarm_ids_created": created_alarm_ids,
        "alarm_ids_reused": reused_alarm_ids,
        "evidence_rows_created": evidence_rows,
        "inventory_impact_rows_created": impact_rows,
        "negative_control_findings_not_modified": len(skipped_findings),
    }


def _json_safe(finding: dict[str, Any]) -> dict[str, Any]:
    result = dict(finding)
    result["event_start_at"] = _iso(finding["event_start_at"])
    result["event_end_at"] = _iso(finding["event_end_at"])
    result["evidence"] = [
        {
            **item,
            "observed_at": _iso(item["observed_at"]),
        }
        for item in finding["evidence"]
    ]
    return result


def run_risk_engine(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    profile: str | None = None,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
    report_path: str | Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Detect risks, optionally persist alarms/traceability, and write a JSON report."""
    settings = load_settings(settings_path)
    selected_profile = profile or settings["generation"]["profile"]
    if selected_profile not in settings["profiles"]:
        raise RiskEngineError(f"Unknown dataset profile: {selected_profile}")
    rules = settings.get("rules", {})
    required_rules = {
        "occupancy_warning_fraction",
        "door_open_warning_minutes",
        "sensor_missing_interval_multiplier",
        "sensor_drift_tolerance",
        "consecutive_breach_readings_to_open_alarm",
    }
    missing_rules = sorted(required_rules - set(rules))
    if missing_rules:
        raise RiskEngineError(f"Missing configured risk rules: {missing_rules}")

    db_path = Path(database_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"DuckDB database not found: {db_path}")
    output_path = Path(report_path) if report_path else DEFAULT_REPORT_ROOT / f"risk_engine_{selected_profile}.json"

    try:
        connection = duckdb.connect(str(db_path), read_only=True)
        try:
            detections = detect_risks(connection, rules=rules)
        finally:
            connection.close()
    except Exception as exc:
        if isinstance(exc, RiskEngineError):
            raise
        raise RiskEngineError(f"Risk detection failed: {exc}") from exc

    persistence = _persist_detections(db_path, detections) if persist else {
        "alarms_created": 0,
        "alarms_reused": 0,
        "alarm_ids_created": [],
        "alarm_ids_reused": [],
        "evidence_rows_created": 0,
        "inventory_impact_rows_created": 0,
        "negative_control_findings_not_modified": 0,
    }
    report = {
        "project": "ColdWatch AI",
        "profile": selected_profile,
        "decision_mode": "DETERMINISTIC_RULE_ENGINE",
        "ground_truth_used": False,
        "findings_count": len(detections),
        "findings_by_issue_type": {
            issue: sum(item["issue_type"] == issue for item in detections)
            for issue in sorted({item["issue_type"] for item in detections})
        },
        "findings": [_json_safe(item) for item in detections],
        "persistence": persistence,
        "notes": [
            "Detector inputs are operational DuckDB tables only; ground-truth artifacts are never opened.",
            "Environmental and occupancy findings are based on readings joined to effective-dated sensor/zone and lot/zone assignments.",
            "Affected inventory is resolved using zone and time overlap.",
            "The deliberately incomplete negative-control alarm is reported but is never silently repaired.",
            "Rule thresholds are illustrative configuration defaults, not regulatory limits.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_file"] = str(output_path)
    return report
