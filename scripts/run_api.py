"""Launch the ColdWatch FastAPI application."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from coldwatch.api.app import DEFAULT_API_DATABASE


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ColdWatch AI REST API.")
    parser.add_argument("--database", type=Path, default=DEFAULT_API_DATABASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    database_path = args.database.expanduser().resolve()
    if not database_path.is_file():
        parser.error(
            f"Database not found: {database_path}. Run the schema, data, anomaly, "
            "and risk-engine setup first, or pass --database with a valid DuckDB file."
        )
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")

    os.environ["COLDWATCH_DB_PATH"] = str(database_path)
    uvicorn.run(
        "coldwatch.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
