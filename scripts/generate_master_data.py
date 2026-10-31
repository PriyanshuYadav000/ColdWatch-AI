"""Generate deterministic master and inventory data into DuckDB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coldwatch.generators.master_data import DEFAULT_DATABASE, generate_master_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ColdWatch AI warehouse, sensor, requirement, and inventory master data."
    )
    parser.add_argument("--profile", choices=("starter", "full"), help="Dataset profile; defaults to config.")
    parser.add_argument("--seed", type=int, help="Override the configured deterministic seed.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="Initialized DuckDB database.")
    args = parser.parse_args()

    summary = generate_master_data(
        database_path=args.database,
        profile=args.profile,
        seed=args.seed,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
