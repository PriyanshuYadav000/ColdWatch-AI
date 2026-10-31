"""Deterministic, batch-oriented generation of sensor readings and door events."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from coldwatch.config import DEFAULT_SETTINGS_PATH, load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "generated"

TEMPERATURE_BASELINES_C = {
    "FROZEN": -21.0,
    "COLD": 5.0,
    "CHILLED": 5.0,
    "AMBIENT": 22.0,
    "HOT": 25.0,
    "CONTROLLED": 5.0,
}
HUMIDITY_BASELINES_PCT = {
    "FROZEN": 50.0,
    "COLD": 45.0,
    "CHILLED": 62.0,
    "AMBIENT": 50.0,
    "HOT": 48.0,
    "CONTROLLED": 45.0,
}

READING_SCHEMA = pa.schema([
    ("reading_id", pa.string()),
    ("sensor_id", pa.string()),
    ("observed_at", pa.timestamp("us", tz="UTC")),
    ("reading_value", pa.float64()),
    ("unit", pa.string()),
    ("quality_status", pa.string()),
    ("ingested_at", pa.timestamp("us", tz="UTC")),
    ("source_sequence", pa.int64()),
])

DOOR_EVENT_SCHEMA = pa.schema([
    ("door_event_id", pa.string()),
    ("zone_id", pa.string()),
    ("door_id", pa.string()),
    ("event_type", pa.string()),
    ("event_at", pa.timestamp("us", tz="UTC")),
    ("source_system", pa.string()),
    ("ingested_at", pa.timestamp("us", tz="UTC")),
    ("notes", pa.string()),
])


class TelemetryGenerationError(ValueError):
    """Raised when telemetry generation inputs or target state are invalid."""


def _parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TelemetryGenerationError("generation.start_timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reading_content_hash_update(
    digest: Any,
    reading_id: str,
    sensor_id: str,
    observed_at: datetime,
    value: float,
    unit: str,
    sequence: int,
) -> None:
    timestamp_text = observed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    canonical = (
        f"{reading_id}|{sensor_id}|{timestamp_text}|{value:.4f}|{unit}|"
        f"GOOD|{timestamp_text}|{sequence}\n"
    )
    digest.update(canonical.encode("utf-8"))


def _get_master_context(
    connection: duckdb.DuckDBPyConnection,
    start_at: datetime,
    coverage_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return sensors with one valid baseline zone mapping and all zones."""
    sensor_rows = connection.execute(
        """
        SELECT
            s.sensor_id,
            s.sensor_type,
            s.unit,
            s.expected_interval_seconds,
            a.zone_id,
            z.zone_type
        FROM sensor s
        JOIN sensor_zone_assignment a
          ON a.sensor_id = s.sensor_id
        JOIN zone z
          ON z.zone_id = a.zone_id
        WHERE s.status = 'ACTIVE'
          AND a.valid_from <= ?
          AND (a.valid_to IS NULL OR a.valid_to > ?)
        ORDER BY s.sensor_id
        """,
        [start_at, coverage_end],
    ).fetchall()

    sensor_ids = [row[0] for row in sensor_rows]
    if len(sensor_ids) != len(set(sensor_ids)):
        raise TelemetryGenerationError(
            "A sensor has more than one zone mapping valid for the telemetry period. "
            "Resolve the baseline mapping before generating readings."
        )

    sensors = [
        {
            "sensor_id": row[0],
            "sensor_type": row[1],
            "unit": row[2],
            "expected_interval_seconds": row[3],
            "zone_id": row[4],
            "zone_type": row[5],
        }
        for row in sensor_rows
    ]
    zones = [
        {"zone_id": row[0], "zone_type": row[1]}
        for row in connection.execute(
            "SELECT zone_id, zone_type FROM zone ORDER BY zone_id"
        ).fetchall()
    ]
    if not sensors or not zones:
        raise TelemetryGenerationError(
            "No sensor/zone master data found. Run the master-data generator first."
        )
    if len(zones) != connection.execute("SELECT COUNT(*) FROM zone").fetchone()[0]:
        raise TelemetryGenerationError("Unable to load all zone master records.")
    if any(sensor["expected_interval_seconds"] <= 0 for sensor in sensors):
        raise TelemetryGenerationError("Every sensor must have a positive expected interval.")
    return sensors, zones


def _assert_telemetry_tables_empty(connection: duckdb.DuckDBPyConnection) -> None:
    existing = {
        "sensor_reading": connection.execute("SELECT COUNT(*) FROM sensor_reading").fetchone()[0],
        "door_event": connection.execute("SELECT COUNT(*) FROM door_event").fetchone()[0],
    }
    populated = {name: count for name, count in existing.items() if count}
    if populated:
        raise TelemetryGenerationError(
            f"Telemetry tables are not empty: {populated}. Use a fresh initialized database."
        )


def _zone_baseline(zone_type: str, sensor_type: str) -> tuple[float, float, float]:
    if sensor_type == "TEMPERATURE":
        return TEMPERATURE_BASELINES_C[zone_type], 0.30, 0.10
    if sensor_type == "HUMIDITY":
        return HUMIDITY_BASELINES_PCT[zone_type], 1.80, 0.60
    if sensor_type == "OCCUPANCY":
        return 50.0, 17.0, 2.0
    raise TelemetryGenerationError(f"Unsupported sensor_type: {sensor_type}")


def _generate_sensor_readings(
    sensors: list[dict[str, Any]],
    *,
    total_readings: int,
    seed: int,
    start_at: datetime,
    interval_seconds: int,
    batch_size: int,
    output_path: Path,
) -> tuple[int, str, datetime, datetime]:
    """Stream readings to Parquet in bounded batches, with a canonical content hash."""
    if total_readings < len(sensors):
        raise TelemetryGenerationError(
            "sensor_readings must be at least the number of sensors so every sensor receives a reading."
        )
    if batch_size <= 0:
        raise TelemetryGenerationError("generation.batch_size must be a positive integer.")

    base_count, remainder = divmod(total_readings, len(sensors))
    per_sensor_counts = [
        base_count + (1 if index < remainder else 0)
        for index in range(len(sensors))
    ]
    max_count = max(per_sensor_counts)
    min_count = min(per_sensor_counts)
    if min_count <= 0:
        raise TelemetryGenerationError("Each sensor must receive at least one reading.")

    content_digest = hashlib.sha256()
    buffer: list[dict[str, Any]] = []
    rows_written = 0
    writer: pq.ParquetWriter | None = None
    coverage_end = start_at + timedelta(seconds=(max_count - 1) * interval_seconds)

    try:
        writer = pq.ParquetWriter(
            output_path,
            READING_SCHEMA,
            compression="zstd",
            use_dictionary=["sensor_id", "unit", "quality_status"],
            write_statistics=True,
        )
        for sensor_index, (sensor, reading_count) in enumerate(
            zip(sensors, per_sensor_counts, strict=True)
        ):
            baseline, amplitude, noise = _zone_baseline(
                sensor["zone_type"], sensor["sensor_type"]
            )
            sensor_rng = np.random.default_rng(
                np.random.SeedSequence([seed, 1000, sensor_index])
            )
            sensor_bias = float(sensor_rng.normal(0.0, noise * 0.15))
            sample_indexes = np.arange(reading_count, dtype=np.float64)
            daily_signal = np.sin(
                (2.0 * np.pi * sample_indexes / 288.0) + (sensor_index % 7) * 0.17
            )
            values = baseline + amplitude * daily_signal + sensor_bias
            values += sensor_rng.normal(0.0, noise, size=reading_count)
            if sensor["sensor_type"] in {"HUMIDITY", "OCCUPANCY"}:
                values = np.clip(values, 0.0, 100.0)
            values = np.round(values, 4)

            for sample_index, value in enumerate(values, start=1):
                observed_at = start_at + timedelta(
                    seconds=(sample_index - 1) * interval_seconds
                )
                timestamp_id = observed_at.strftime("%Y%m%dT%H%M%SZ")
                reading_id = f"RDG-{sensor['sensor_id']}-{timestamp_id}"
                numeric_value = float(value)
                buffer.append({
                    "reading_id": reading_id,
                    "sensor_id": sensor["sensor_id"],
                    "observed_at": observed_at,
                    "reading_value": numeric_value,
                    "unit": sensor["unit"],
                    "quality_status": "GOOD",
                    "ingested_at": observed_at,
                    "source_sequence": sample_index,
                })
                _reading_content_hash_update(
                    content_digest,
                    reading_id,
                    sensor["sensor_id"],
                    observed_at,
                    numeric_value,
                    sensor["unit"],
                    sample_index,
                )
                rows_written += 1

                if len(buffer) >= batch_size:
                    table = pa.Table.from_pylist(buffer, schema=READING_SCHEMA)
                    writer.write_table(table, row_group_size=batch_size)
                    buffer.clear()

        if buffer:
            writer.write_table(
                pa.Table.from_pylist(buffer, schema=READING_SCHEMA),
                row_group_size=batch_size,
            )
    finally:
        if writer is not None:
            writer.close()

    if rows_written != total_readings:
        raise TelemetryGenerationError(
            f"Reading count mismatch: expected {total_readings}, generated {rows_written}."
        )
    return rows_written, content_digest.hexdigest(), start_at, coverage_end


def _generate_door_events(
    zones: list[dict[str, Any]],
    *,
    seed: int,
    start_at: datetime,
    coverage_seconds: int,
    pairs_per_zone_per_day: float,
    output_path: Path,
) -> tuple[int, int, str]:
    """Generate paired normal OPEN/CLOSE events for each zone."""
    if pairs_per_zone_per_day <= 0:
        raise TelemetryGenerationError("door_event_pairs_per_zone_per_day must be positive.")
    if coverage_seconds <= 0:
        raise TelemetryGenerationError("Telemetry coverage must be positive to generate door events.")

    pairs_per_zone = max(
        1,
        math.ceil((coverage_seconds / 86400.0) * pairs_per_zone_per_day),
    )
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()

    for zone_index, zone in enumerate(zones):
        event_rng = np.random.default_rng(
            np.random.SeedSequence([seed, 900001, zone_index])
        )
        door_id = "DOOR-01"
        spacing = coverage_seconds / pairs_per_zone
        for pair_index in range(pairs_per_zone):
            center = (pair_index + 0.5) * spacing
            jitter = float(event_rng.uniform(-0.15, 0.15) * spacing)
            open_offset = int(max(0, min(coverage_seconds - 600, center + jitter)))
            duration_seconds = int(event_rng.integers(120, 481))
            close_offset = min(coverage_seconds, open_offset + duration_seconds)
            if close_offset <= open_offset:
                raise TelemetryGenerationError("Generated door close event is not after open event.")

            open_at = start_at + timedelta(seconds=open_offset)
            close_at = start_at + timedelta(seconds=close_offset)
            for event_type, event_at in (("OPEN", open_at), ("CLOSE", close_at)):
                timestamp_id = event_at.strftime("%Y%m%dT%H%M%SZ")
                event_id = (
                    f"DRE-{zone['zone_id']}-{door_id}-{timestamp_id}-{event_type}"
                )
                rows.append({
                    "door_event_id": event_id,
                    "zone_id": zone["zone_id"],
                    "door_id": door_id,
                    "event_type": event_type,
                    "event_at": event_at,
                    "source_system": "SYNTHETIC",
                    "ingested_at": event_at,
                    "notes": "Normal paired synthetic door event",
                })
                canonical = (
                    f"{event_id}|{zone['zone_id']}|{door_id}|{event_type}|"
                    f"{event_at.strftime('%Y-%m-%dT%H:%M:%SZ')}|SYNTHETIC\n"
                )
                digest.update(canonical.encode("utf-8"))

    rows.sort(key=lambda row: (row["event_at"], row["zone_id"], row["door_id"], row["event_type"]))
    pq.write_table(
        pa.Table.from_pylist(rows, schema=DOOR_EVENT_SCHEMA),
        output_path,
        compression="zstd",
        use_dictionary=["zone_id", "door_id", "event_type", "source_system"],
    )
    return len(rows), pairs_per_zone, digest.hexdigest()


def _validate_persisted_telemetry(
    connection: duckdb.DuckDBPyConnection,
    *,
    expected_readings: int,
    expected_door_events: int,
    expected_sensors: int,
    expected_zones: int,
) -> dict[str, int]:
    actual_readings = connection.execute("SELECT COUNT(*) FROM sensor_reading").fetchone()[0]
    actual_doors = connection.execute("SELECT COUNT(*) FROM door_event").fetchone()[0]
    distinct_sensor_readings = connection.execute(
        "SELECT COUNT(DISTINCT reading_id) FROM sensor_reading"
    ).fetchone()[0]
    distinct_door_events = connection.execute(
        "SELECT COUNT(DISTINCT door_event_id) FROM door_event"
    ).fetchone()[0]
    sensors_with_readings = connection.execute(
        "SELECT COUNT(DISTINCT sensor_id) FROM sensor_reading"
    ).fetchone()[0]
    zones_with_doors = connection.execute(
        "SELECT COUNT(DISTINCT zone_id) FROM door_event"
    ).fetchone()[0]
    if actual_readings != expected_readings or distinct_sensor_readings != expected_readings:
        raise TelemetryGenerationError("SensorReading row count or primary-key validation failed.")
    if actual_doors != expected_door_events or distinct_door_events != expected_door_events:
        raise TelemetryGenerationError("DoorEvent row count or primary-key validation failed.")
    if sensors_with_readings != expected_sensors:
        raise TelemetryGenerationError("Not every sensor received sensor readings.")
    if zones_with_doors != expected_zones:
        raise TelemetryGenerationError("Not every zone received door events.")

    invalid_units = connection.execute(
        """
        SELECT COUNT(*)
        FROM sensor_reading r
        JOIN sensor s USING (sensor_id)
        WHERE r.unit <> s.unit
        """
    ).fetchone()[0]
    invalid_readings = connection.execute(
        """
        SELECT COUNT(*)
        FROM sensor_reading
        WHERE (unit IN ('pct_rh', 'pct_capacity') AND reading_value NOT BETWEEN 0 AND 100)
           OR quality_status NOT IN ('GOOD', 'SUSPECT', 'BAD')
        """
    ).fetchone()[0]
    invalid_doors = connection.execute(
        """
        SELECT COUNT(*)
        FROM door_event
        WHERE event_type NOT IN ('OPEN', 'CLOSE')
        """
    ).fetchone()[0]
    if invalid_units or invalid_readings or invalid_doors:
        raise TelemetryGenerationError(
            "Telemetry value validation failed: "
            f"unit mismatches={invalid_units}, invalid readings={invalid_readings}, "
            f"invalid door events={invalid_doors}."
        )

    per_sensor = connection.execute(
        """
        SELECT COUNT(*) AS sensor_count,
               MIN(reading_count) AS min_readings,
               MAX(reading_count) AS max_readings
        FROM (
            SELECT sensor_id, COUNT(*) AS reading_count
            FROM sensor_reading
            GROUP BY sensor_id
        )
        """
    ).fetchone()
    return {
        "sensor_readings": actual_readings,
        "door_events": actual_doors,
        "sensors_with_readings": sensors_with_readings,
        "zones_with_door_events": zones_with_doors,
        "min_readings_per_sensor": per_sensor[1],
        "max_readings_per_sensor": per_sensor[2],
    }


def generate_telemetry(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    profile: str | None = None,
    seed: int | None = None,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Generate normal baseline telemetry, write Parquet, and bulk-load DuckDB."""
    settings = load_settings(settings_path)
    selected_profile = profile or settings["generation"]["profile"]
    profiles = settings["profiles"]
    if selected_profile not in profiles:
        raise TelemetryGenerationError(
            f"Unknown profile {selected_profile!r}; choose one of {sorted(profiles)}."
        )
    selected_seed = settings["project"]["random_seed"] if seed is None else seed
    counts = profiles[selected_profile]
    generation = settings["generation"]
    interval_seconds = int(generation["reading_interval_minutes"]) * 60
    batch_size = int(generation["batch_size"])
    door_pairs_rate = float(generation.get("door_event_pairs_per_zone_per_day", 2))
    if interval_seconds <= 0:
        raise TelemetryGenerationError("reading_interval_minutes must be positive.")

    sensor_count = int(counts["sensors"])
    total_readings = int(counts["sensor_readings"])
    if total_readings < sensor_count:
        raise TelemetryGenerationError("sensor_readings must be at least the sensor count.")
    per_sensor_max = (total_readings + sensor_count - 1) // sensor_count
    coverage_seconds = (per_sensor_max - 1) * interval_seconds
    start_at = _parse_utc(generation["start_timestamp"])
    coverage_end = start_at + timedelta(seconds=coverage_seconds)

    db_path = Path(database_path)
    if not db_path.is_file():
        raise FileNotFoundError(
            f"DuckDB database not found: {db_path}. Run scripts/init_db.py first."
        )
    connection = duckdb.connect(str(db_path))
    try:
        _assert_telemetry_tables_empty(connection)
        sensors, zones = _get_master_context(connection, start_at, coverage_end)
        if len(sensors) != sensor_count:
            raise TelemetryGenerationError(
                f"Profile expects {sensor_count} sensors, but {len(sensors)} baseline sensor mappings are valid."
            )
        if len(zones) != int(counts["zones"]):
            raise TelemetryGenerationError(
                f"Profile expects {counts['zones']} zones, but found {len(zones)}."
            )
        mismatched_intervals = [
            sensor["sensor_id"]
            for sensor in sensors
            if sensor["expected_interval_seconds"] != interval_seconds
        ]
        if mismatched_intervals:
            raise TelemetryGenerationError(
                "Configured reading interval does not match sensor master data; "
                f"first mismatches: {mismatched_intervals[:5]}."
            )
    finally:
        connection.close()

    output_dir = Path(output_root) / selected_profile / "telemetry"
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"Telemetry output already exists: {output_dir}. "
                "Use --overwrite with a fresh/empty telemetry database or choose another output root."
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    readings_path = output_dir / "sensor_readings.parquet"
    doors_path = output_dir / "door_events.parquet"
    summary_path = output_dir / "telemetry_summary.json"

    reading_rows, reading_hash, _, actual_coverage_end = _generate_sensor_readings(
        sensors,
        total_readings=total_readings,
        seed=selected_seed,
        start_at=start_at,
        interval_seconds=interval_seconds,
        batch_size=batch_size,
        output_path=readings_path,
    )
    coverage_seconds = int((actual_coverage_end - start_at).total_seconds())
    door_rows, door_pairs_per_zone, door_hash = _generate_door_events(
        zones,
        seed=selected_seed,
        start_at=start_at,
        coverage_seconds=coverage_seconds,
        pairs_per_zone_per_day=door_pairs_rate,
        output_path=doors_path,
    )

    connection = duckdb.connect(str(db_path))
    try:
        _assert_telemetry_tables_empty(connection)
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """
            INSERT INTO sensor_reading (
                reading_id, sensor_id, observed_at, reading_value, unit,
                quality_status, ingested_at, source_sequence
            )
            SELECT
                reading_id, sensor_id, observed_at,
                CAST(reading_value AS DECIMAL(12, 4)), unit,
                quality_status, ingested_at, source_sequence
            FROM read_parquet(?)
            """,
            [str(readings_path)],
        )
        connection.execute(
            """
            INSERT INTO door_event (
                door_event_id, zone_id, door_id, event_type,
                event_at, source_system, ingested_at, notes
            )
            SELECT
                door_event_id, zone_id, door_id, event_type,
                event_at, source_system, ingested_at, notes
            FROM read_parquet(?)
            """,
            [str(doors_path)],
        )
        persisted_counts = _validate_persisted_telemetry(
            connection,
            expected_readings=reading_rows,
            expected_door_events=door_rows,
            expected_sensors=sensor_count,
            expected_zones=int(counts["zones"]),
        )
        connection.execute("COMMIT")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()

    summary = {
        "project": "ColdWatch AI",
        "dataset_profile": selected_profile,
        "seed": selected_seed,
        "timestamp_convention": "UTC",
        "generation_mode": "NORMAL_BASELINE_BEFORE_ANOMALY_INJECTION",
        "reading_interval_seconds": interval_seconds,
        "coverage_start": start_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage_end": actual_coverage_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage_duration_hours": round(coverage_seconds / 3600.0, 4),
        "counts": {
            "sensors": sensor_count,
            "zones": len(zones),
            "sensor_readings": reading_rows,
            "door_event_pairs_per_zone": door_pairs_per_zone,
            "door_events": door_rows,
        },
        "canonical_content_sha256": {
            "sensor_readings": reading_hash,
            "door_events": door_hash,
        },
        "parquet_sha256": {
            "sensor_readings": _sha256_file(readings_path),
            "door_events": _sha256_file(doors_path),
        },
        "persisted_counts": persisted_counts,
        "files": {
            "sensor_readings": str(readings_path.relative_to(PROJECT_ROOT))
                if readings_path.is_relative_to(PROJECT_ROOT) else str(readings_path),
            "door_events": str(doors_path.relative_to(PROJECT_ROOT))
                if doors_path.is_relative_to(PROJECT_ROOT) else str(doors_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
