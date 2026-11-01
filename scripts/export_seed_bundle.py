"""Export operational DuckDB seed tables as CSV and replayable SQL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from coldwatch.exports.seed_bundle import (
    ALL_OPERATIONAL_TABLES,
    MASTER_SEED_TABLES,
    SeedExportError,
    export_seed_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export DuckDB operational seed data as CSV files plus a SQL restore script."
    )
    parser.add_argument("--database", type=Path, required=True, help="Source DuckDB database.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for CSV, SQL and JSON files.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include telemetry, alarms, evidence, impacts and workflow history. Full exports can be large.",
    )
    parser.add_argument(
        "--tables",
        help="Optional comma-separated operational table names; overrides the default master-data table set.",
    )
    args = parser.parse_args()

    if args.tables:
        selected_tables = [item.strip() for item in args.tables.split(",") if item.strip()]
    elif args.all:
        selected_tables = list(ALL_OPERATIONAL_TABLES)
    else:
        selected_tables = list(MASTER_SEED_TABLES)

    try:
        result = export_seed_bundle(
            database_path=args.database,
            output_dir=args.output_dir,
            tables=selected_tables,
        )
    except (OSError, SeedExportError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
