"""Initialize a local DuckDB database from the canonical SQL schema."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "schema.sql"
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"


def initialize_database(database_path: Path, schema_path: Path = DEFAULT_SCHEMA) -> list[str]:
    """Create all schema tables and return their names."""
    if not schema_path.is_file():
        raise FileNotFoundError(f"SQL schema not found: {schema_path}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = schema_path.read_text(encoding="utf-8")

    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(schema_sql)
        connection.execute("COMMIT")
        rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ).fetchall()
        return [row[0] for row in rows]
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the ColdWatch AI DuckDB database.")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Database file path (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"SQL schema path (default: {DEFAULT_SCHEMA})",
    )
    args = parser.parse_args()

    tables = initialize_database(args.database, args.schema)
    print(f"Database initialized: {args.database}")
    print(f"Tables created/verified: {len(tables)}")
    for table in tables:
        print(f"  - {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
