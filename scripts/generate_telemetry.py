"""Generate baseline sensor readings and door events."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coldwatch.generators.telemetry import (
    DEFAULT_DATABASE,
    DEFAULT_OUTPUT_ROOT,
    generate_telemetry,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic sensor readings and paired door events."
    )
    parser.add_argument("--profile", choices=("starter", "full"), help="Dataset profile; defaults to config.")
    parser.add_argument("--seed", type=int, help="Override configured deterministic seed.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="Database containing master data.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Root directory for Parquet output.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace this profile's telemetry output directory. The telemetry tables must still be empty.",
    )
    args = parser.parse_args()

    summary = generate_telemetry(
        database_path=args.database,
        profile=args.profile,
        seed=args.seed,
        output_root=args.output_root,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
