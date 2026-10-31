"""Run deterministic ColdWatch risk detection and alarm persistence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coldwatch.config import load_settings
from coldwatch.engine.risk_engine import (
    DEFAULT_DATABASE,
    DEFAULT_REPORT_ROOT,
    run_risk_engine,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect warehouse risks and persist traceable alarms and lot impacts."
    )
    parser.add_argument("--profile", choices=("starter", "full"), help="Dataset profile; defaults to config.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect risks and write a report without persisting alarms or evidence.",
    )
    args = parser.parse_args()
    selected_profile = args.profile or load_settings()["generation"]["profile"]

    report = run_risk_engine(
        database_path=args.database,
        profile=selected_profile,
        report_path=args.report_root / f"risk_engine_{selected_profile}.json",
        persist=not args.dry_run,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
