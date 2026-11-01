"""Export DuckDB tables as portable CSV files plus a replayable SQL script."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "schema.sql"

MASTER_SEED_TABLES = (
    "warehouse",
    "storage_requirement",
    "zone",
    "requirement_allowed_zone_type",
    "sensor",
    "sensor_zone_assignment",
    "inventory_lot",
    "lot_zone_assignment",
)

EXPORT_ORDER = (
    "warehouse",
    "storage_requirement",
    "zone",
    "requirement_allowed_zone_type",
    "sensor",
    "sensor_zone_assignment",
    "inventory_lot",
    "lot_zone_assignment",
    "sensor_reading",
    "door_event",
    "sensor_health_event",
    "sensor_reading_revision",
    "sensor_reading_gap",
    "alarm",
    "alarm_evidence",
    "alarm_inventory_impact",
    "inspection",
    "alarm_status_history",
    "inspection_status_history",
    "remediation",
    "remediation_status_history",
    "remediation_verification_history",
)

ALL_OPERATIONAL_TABLES = EXPORT_ORDER


class SeedExportError(RuntimeError):
    """Raised when a portable seed bundle cannot be created."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def export_seed_bundle(
    *,
    database_path: str | Path,
    output_dir: str | Path,
    tables: list[str] | tuple[str, ...] = MASTER_SEED_TABLES,
    schema_path: str | Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Write sorted CSV table snapshots, schema.sql, seed_data.sql and manifest.json.

    The default bundle contains parent/master data. Pass all operational table
    names to include telemetry, alarms, evidence and workflow history. Ground
    truth is deliberately not an operational table and is never exported.
    """
    db_path = Path(database_path).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    schema_file = Path(schema_path).expanduser().resolve()

    if not db_path.is_file():
        raise FileNotFoundError(f"DuckDB database not found: {db_path}")
    if not schema_file.is_file():
        raise FileNotFoundError(f"SQL schema file not found: {schema_file}")

    selected = list(dict.fromkeys(tables))
    unknown = sorted(set(selected) - set(ALL_OPERATIONAL_TABLES))
    if unknown:
        raise SeedExportError(f"Unsupported or non-operational tables requested: {unknown}")
    if not selected:
        raise SeedExportError("At least one operational table must be selected.")

    selected.sort(key=EXPORT_ORDER.index)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(schema_file, target / "schema.sql")

    connection = duckdb.connect(str(db_path), read_only=True)
    table_records: list[dict[str, Any]] = []
    sql_statements = [
        "-- ColdWatch AI portable seed loader.",
        "-- Create an empty database with schema.sql before running this file.",
        "-- Run from this bundle directory so CSV paths resolve correctly.",
        "BEGIN TRANSACTION;",
        "",
    ]
    try:
        existing = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }
        missing = [table for table in selected if table not in existing]
        if missing:
            raise SeedExportError(f"Selected tables are missing from the database: {missing}")

        for table in selected:
            identifier = _sql_identifier(table)
            info = connection.execute(f"PRAGMA table_info({ _sql_string(table) })").fetchall()
            columns = [row[1] for row in info]
            primary_key = [row[1] for row in sorted(
                (row for row in info if row[5]),
                key=lambda row: row[5],
            )]
            ordered_columns = ", ".join(_sql_identifier(column) for column in columns)
            order_clause = (
                " ORDER BY " + ", ".join(_sql_identifier(column) for column in primary_key)
                if primary_key else ""
            )

            csv_name = f"{table}.csv"
            csv_path = target / csv_name
            csv_literal = _sql_string(str(csv_path))
            # DuckDB's default CSV quote/escape character is the standard
            # double quote. Leave those defaults explicit only in read_csv;
            # avoid fragile nested quote literals in COPY's SQL option parser.
            connection.execute(
                f"COPY (SELECT * FROM {identifier}{order_clause}) "
                f"TO {csv_literal} (HEADER, DELIMITER ',')"
            )
            row_count = int(connection.execute(
                f"SELECT COUNT(*) FROM {identifier}"
            ).fetchone()[0])

            csv_reference = _sql_string(csv_name)
            sql_statements.append(
                f"INSERT INTO {identifier} ({ordered_columns}) "
                f"SELECT {ordered_columns} "
                f"FROM read_csv({csv_reference}, header = true, all_varchar = true);"
            )
            table_records.append({
                "table": table,
                "row_count": row_count,
                "csv_file": csv_name,
                "csv_sha256": _sha256(csv_path),
                "columns": columns,
                "primary_key": primary_key,
            })
    finally:
        connection.close()

    sql_statements.extend(["", "COMMIT;", ""])
    sql_path = target / "seed_data.sql"
    sql_path.write_text("\n".join(sql_statements), encoding="utf-8")

    manifest = {
        "project": "ColdWatch AI",
        "format_version": 1,
        "source_database": str(db_path),
        "ground_truth_included": False,
        "tables": table_records,
        "schema_file": "schema.sql",
        "schema_sha256": _sha256(target / "schema.sql"),
        "sql_seed_file": "seed_data.sql",
        "sql_seed_sha256": _sha256(sql_path),
        "restore_order": selected,
        "restore_instructions": [
            "Create an empty DuckDB database.",
            "Execute schema.sql against that database.",
            "Change the working directory to this bundle directory.",
            "Execute seed_data.sql against the database.",
        ],
    }
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_file"] = str(manifest_path)
    return manifest
