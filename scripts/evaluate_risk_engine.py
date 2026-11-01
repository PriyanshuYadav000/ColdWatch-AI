"""Compare a risk-engine JSON report with isolated ground-truth labels."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from coldwatch.config import load_settings
from coldwatch.evaluation.risk_metrics import EvaluationError, evaluate_risk_findings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_report(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"Could not read risk report {path}: {exc}") from exc


def _read_ground_truth(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
    except (OSError, csv.Error) as exc:
        raise EvaluationError(f"Could not read ground truth {path}: {exc}") from exc
    return rows


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        description="Evaluate existing risk findings against isolated labelled scenarios."
    )
    parser.add_argument(
        "--profile",
        choices=("starter", "full"),
        default=settings["generation"]["profile"],
    )
    parser.add_argument(
        "--risk-report",
        type=Path,
        help="Risk-engine JSON report. Defaults to reports/risk_engine_<profile>.json.",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        help="Evaluation CSV. Defaults to data/ground_truth/<profile>/ground_truth.csv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Evaluation JSON output. Defaults to reports/evaluation_<profile>.json.",
    )
    parser.add_argument(
        "--time-tolerance-seconds",
        type=int,
        default=300,
        help="Start-time tolerance when an event end is unavailable (default: 300).",
    )
    args = parser.parse_args()

    risk_path = args.risk_report or PROJECT_ROOT / "reports" / f"risk_engine_{args.profile}.json"
    truth_path = args.ground_truth or PROJECT_ROOT / "data" / "ground_truth" / args.profile / "ground_truth.csv"
    output_path = args.output or PROJECT_ROOT / "reports" / f"evaluation_{args.profile}.json"

    report = _read_report(risk_path)
    findings = report.get("findings")
    if not isinstance(findings, list):
        parser.error(f"Risk report has no findings array: {risk_path}")
    labels = _read_ground_truth(truth_path)
    result = evaluate_risk_findings(
        findings,
        labels,
        time_tolerance_seconds=args.time_tolerance_seconds,
    )
    result.update({
        "project": "ColdWatch AI",
        "profile": args.profile,
        "risk_report_file": str(risk_path),
        "ground_truth_file": str(truth_path),
        "output_file": str(output_path),
    })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
