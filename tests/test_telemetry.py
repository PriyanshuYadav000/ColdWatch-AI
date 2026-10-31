from pathlib import Path

import duckdb
import pytest

from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data
from coldwatch.generators.telemetry import (
    TelemetryGenerationError,
    generate_telemetry,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


def _seed_starter_database(database_path: Path) -> None:
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        connection.close()
    data = build_master_data(load_settings(), profile="starter", seed=20261022)
    persist_master_data(database_path, data)


def test_starter_telemetry_counts_and_per_sensor_cadence(tmp_path: Path):
    database_path = tmp_path / "starter.duckdb"
    _seed_starter_database(database_path)

    summary = generate_telemetry(
        database_path=database_path,
        profile="starter",
        seed=20261022,
        output_root=tmp_path / "generated",
    )

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM sensor_reading").fetchone()[0] == 10000
        assert connection.execute("SELECT COUNT(*) FROM door_event").fetchone()[0] == 40
        counts = connection.execute(
            """
            SELECT MIN(n), MAX(n)
            FROM (
                SELECT sensor_id, COUNT(*) AS n
                FROM sensor_reading
                GROUP BY sensor_id
            )
            """
        ).fetchone()
        assert counts == (200, 200)
        assert connection.execute(
            "SELECT COUNT(DISTINCT sensor_id) FROM sensor_reading"
        ).fetchone()[0] == 50
        assert connection.execute(
            "SELECT COUNT(DISTINCT zone_id) FROM door_event"
        ).fetchone()[0] == 10
    finally:
        connection.close()

    assert summary["counts"]["sensor_readings"] == 10000
    assert summary["counts"]["door_events"] == 40
    assert Path(summary["files"]["sensor_readings"]).is_file()
    assert Path(summary["files"]["door_events"]).is_file()


def test_baseline_environmental_values_follow_zone_profiles(tmp_path: Path):
    database_path = tmp_path / "profiles.duckdb"
    _seed_starter_database(database_path)
    generate_telemetry(
        database_path=database_path,
        profile="starter",
        seed=20261022,
        output_root=tmp_path / "generated",
    )

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        temperature_ranges = {
            "FROZEN": (-22.0, -20.0),
            "COLD": (4.0, 6.0),
            "CHILLED": (4.0, 6.0),
            "AMBIENT": (21.0, 23.0),
            "HOT": (24.0, 26.0),
            "CONTROLLED": (4.0, 6.0),
        }
        for zone_type, (minimum, maximum) in temperature_ranges.items():
            invalid = connection.execute(
                """
                SELECT COUNT(*)
                FROM sensor_reading r
                JOIN sensor s USING (sensor_id)
                JOIN sensor_zone_assignment a USING (sensor_id)
                JOIN zone z USING (zone_id)
                WHERE s.sensor_type = 'TEMPERATURE'
                  AND z.zone_type = ?
                  AND (r.reading_value < ? OR r.reading_value > ?)
                """,
                [zone_type, minimum, maximum],
            ).fetchone()[0]
            assert invalid == 0, f"Unexpected baseline temperature in {zone_type}"

        humidity_ranges = {
            "FROZEN": (45.0, 55.0),
            "COLD": (40.0, 50.0),
            "CHILLED": (57.0, 67.0),
            "AMBIENT": (45.0, 55.0),
            "HOT": (43.0, 53.0),
            "CONTROLLED": (40.0, 50.0),
        }
        for zone_type, (minimum, maximum) in humidity_ranges.items():
            invalid = connection.execute(
                """
                SELECT COUNT(*)
                FROM sensor_reading r
                JOIN sensor s USING (sensor_id)
                JOIN sensor_zone_assignment a USING (sensor_id)
                JOIN zone z USING (zone_id)
                WHERE s.sensor_type = 'HUMIDITY'
                  AND z.zone_type = ?
                  AND (r.reading_value < ? OR r.reading_value > ?)
                """,
                [zone_type, minimum, maximum],
            ).fetchone()[0]
            assert invalid == 0, f"Unexpected baseline humidity in {zone_type}"

        occupancy_invalid = connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_reading r
            JOIN sensor s USING (sensor_id)
            WHERE s.sensor_type = 'OCCUPANCY'
              AND r.reading_value NOT BETWEEN 0 AND 100
            """
        ).fetchone()[0]
        assert occupancy_invalid == 0
    finally:
        connection.close()


def test_door_events_are_paired_and_have_plausible_normal_duration(tmp_path: Path):
    database_path = tmp_path / "doors.duckdb"
    _seed_starter_database(database_path)
    generate_telemetry(
        database_path=database_path,
        profile="starter",
        seed=20261022,
        output_root=tmp_path / "generated",
    )

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT zone_id, door_id, event_type, event_at
            FROM door_event
            ORDER BY zone_id, door_id, event_at
            """
        ).fetchall()
    finally:
        connection.close()

    events_by_door = {}
    for zone_id, door_id, event_type, event_at in rows:
        events_by_door.setdefault((zone_id, door_id), []).append((event_type, event_at))

    assert len(events_by_door) == 10
    for events in events_by_door.values():
        assert len(events) == 4
        assert [event[0] for event in events] == ["OPEN", "CLOSE", "OPEN", "CLOSE"]
        for index in (0, 2):
            duration = (events[index + 1][1] - events[index][1]).total_seconds()
            assert 120 <= duration <= 480


def test_telemetry_is_reproducible_across_fresh_databases(tmp_path: Path):
    summaries = []
    for name in ("first", "second"):
        database_path = tmp_path / f"{name}.duckdb"
        _seed_starter_database(database_path)
        summary = generate_telemetry(
            database_path=database_path,
            profile="starter",
            seed=20261022,
            output_root=tmp_path / name,
        )
        summaries.append(summary)

    assert summaries[0]["canonical_content_sha256"] == summaries[1]["canonical_content_sha256"]
    assert summaries[0]["counts"] == summaries[1]["counts"]
    assert summaries[0]["parquet_sha256"] == summaries[1]["parquet_sha256"]


def test_telemetry_refuses_to_duplicate_existing_sensor_readings(tmp_path: Path):
    database_path = tmp_path / "duplicate.duckdb"
    _seed_starter_database(database_path)
    generate_telemetry(
        database_path=database_path,
        profile="starter",
        seed=20261022,
        output_root=tmp_path / "first",
    )

    with pytest.raises(TelemetryGenerationError, match="Telemetry tables are not empty"):
        generate_telemetry(
            database_path=database_path,
            profile="starter",
            seed=20261022,
            output_root=tmp_path / "second",
        )
