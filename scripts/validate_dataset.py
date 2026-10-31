"""Run the ColdWatch post-injection dataset validator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coldwatch.validation.dataset import (
    DEFAULT_DATABASE,
    DEFAULT_GENERATED_ROOT,
    DEFAULT_GROUND_TRUTH_ROOT,
    DEFAULT_REPORT_ROOT,
    validate_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ColdWatch baseline artifacts, injected scenarios, and audit trails."
    )
    parser.add_argument("--profile", choices=("starter", "full"), help="Dataset profile; defaults to config.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="DuckDB database path.")
    parser.add_argument("--generated-root", type=Path, default=DEFAULT_GENERATED_ROOT)
    parser.add_argument("--ground-truth-root", type=Path, default=DEFAULT_GROUND_TRUTH_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()

    result = validate_dataset(
        database_path=args.database,
        profile=args.profile,
        generated_root=args.generated_root,
        ground_truth_root=args.ground_truth_root,
        report_root=args.report_root,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
