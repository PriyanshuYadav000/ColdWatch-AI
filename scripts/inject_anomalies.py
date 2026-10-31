"""Inject controlled synthetic anomalies and export separate ground truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coldwatch.generators.anomaly_injector import (
    DEFAULT_DATABASE,
    DEFAULT_GROUND_TRUTH_ROOT,
    DEFAULT_REPORT_ROOT,
    inject_anomalies,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject deterministic warehouse risk scenarios into a baseline dataset."
    )
    parser.add_argument("--profile", choices=("starter", "full"), help="Dataset profile; defaults to config.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="DuckDB containing master and telemetry baseline.")
    parser.add_argument("--ground-truth-root", type=Path, default=DEFAULT_GROUND_TRUTH_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()
    result = inject_anomalies(
        database_path=args.database,
        profile=args.profile,
        ground_truth_root=args.ground_truth_root,
        report_root=args.report_root,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
