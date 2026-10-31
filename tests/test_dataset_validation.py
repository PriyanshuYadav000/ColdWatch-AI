from __future__ import annotations

import csv
from pathlib import Path

import duckdb

from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data
from coldwatch.generators.telemetry import generate_telemetry
from coldwatch.generators.anomaly_injector import inject_anomalies
from coldwatch.validation.dataset import validate_dataset


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


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
    return database_path, generated_root, ground_truth_root, report_root


def test_validator_passes_complete_controlled_dataset(tmp_path: Path):
    database_path, generated_root, truth_root, report_root = _seed_and_inject(tmp_path)

    result = validate_dataset(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=truth_root,
        report_root=report_root,
    )

    assert result["status"] == "PASS", [
        check for check in result["checks"] if check["status"] == "FAIL"
    ]
    assert result["checks_total"] >= 40
    assert result["checks_failed"] == 0
    assert (report_root / "validation_starter.json").is_file()


def test_validator_detects_ground_truth_tampering(tmp_path: Path):
    database_path, generated_root, truth_root, report_root = _seed_and_inject(tmp_path)
    truth_path = truth_root / "starter" / "ground_truth.csv"
    with truth_path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
        fieldnames = list(rows[0])

    rows[0]["rationale"] = "Tampered after manifest creation."
    with truth_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    result = validate_dataset(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=truth_root,
        report_root=report_root,
    )

    assert result["status"] == "FAIL"
    failed_names = {item["name"] for item in result["checks"] if item["status"] == "FAIL"}
    assert "ground-truth checksum" in failed_names


def test_validator_detects_invalid_operational_sensor_timestamps(tmp_path: Path):
    database_path, generated_root, truth_root, report_root = _seed_and_inject(tmp_path)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(
            """
            UPDATE sensor_reading
            SET ingested_at = observed_at - INTERVAL '1 minute'
            WHERE reading_id = (
                SELECT r.reading_id
                FROM sensor_reading r
                JOIN sensor s USING (sensor_id)
                WHERE s.sensor_type = 'OCCUPANCY'
                  AND r.reading_id NOT IN (
                      SELECT reading_id FROM sensor_reading_revision
                  )
                ORDER BY r.observed_at
                LIMIT 1
            )
            """
        )
    finally:
        connection.close()

    result = validate_dataset(
        database_path=database_path,
        profile="starter",
        generated_root=generated_root,
        ground_truth_root=truth_root,
        report_root=report_root,
    )

    assert result["status"] == "FAIL"
    failed_names = {item["name"] for item in result["checks"] if item["status"] == "FAIL"}
    assert "sensor reading values, quality, and timestamps are valid" in failed_names
