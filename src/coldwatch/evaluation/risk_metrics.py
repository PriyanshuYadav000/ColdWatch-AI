"""Independent evaluation of risk-engine findings against labelled scenarios.

This module is intentionally separate from the operational risk engine. It reads
already-produced findings and evaluation labels; it is never imported by the
detectors or used to make operational decisions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class EvaluationError(ValueError):
    """Raised when risk findings or ground-truth labels are malformed."""


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EvaluationError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    else:
        raise EvaluationError(f"Unsupported timestamp value: {value!r}")
    if parsed.tzinfo is None:
        raise EvaluationError(f"Timestamp must include a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _parse_lot_ids(value: Any) -> set[str]:
    if value is None or value == "":
        return set()
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"Expected a JSON array of lot IDs, got: {value!r}") from exc
    else:
        raise EvaluationError(f"Unsupported affected-lot value: {value!r}")
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise EvaluationError("Affected-lot IDs must be a JSON array of strings.")
    return set(parsed)


def _intervals_match(
    finding: dict[str, Any],
    scenario: dict[str, Any],
    *,
    time_tolerance_seconds: int,
) -> bool:
    expected_start = _parse_timestamp(scenario.get("event_start_at"))
    expected_end = _parse_timestamp(scenario.get("event_end_at"))
    actual_start = _parse_timestamp(finding.get("event_start_at", finding.get("start_at")))
    actual_end = _parse_timestamp(finding.get("event_end_at", finding.get("end_at")))

    # If the expected labels do not define a time, use the entity and issue
    # identity. Otherwise, require time agreement to avoid loose matches.
    if expected_start is None:
        return True
    if actual_start is None:
        return False

    if expected_end is not None and actual_end is not None:
        return actual_start < expected_end and expected_start < actual_end

    return abs((actual_start - expected_start).total_seconds()) <= time_tolerance_seconds


def _matches(
    finding: dict[str, Any],
    scenario: dict[str, Any],
    *,
    time_tolerance_seconds: int,
) -> bool:
    if finding.get("issue_type") != scenario.get("issue_type"):
        return False
    if finding.get("entity_id") != scenario.get("entity_id"):
        return False

    expected_entity_type = scenario.get("entity_type")
    actual_entity_type = finding.get("entity_type")
    if expected_entity_type and actual_entity_type and expected_entity_type != actual_entity_type:
        return False

    expected_zone = scenario.get("affected_zone_id") or scenario.get("zone_id") or ""
    actual_zone = finding.get("zone_id") or ""
    if expected_zone and expected_zone != actual_zone:
        return False

    return _intervals_match(
        finding,
        scenario,
        time_tolerance_seconds=time_tolerance_seconds,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _classification_metrics(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    if precision is None or recall is None or precision + recall == 0:
        f1 = None if precision is None or recall is None else 0.0
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
    }


def evaluate_risk_findings(
    findings: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]],
    *,
    time_tolerance_seconds: int = 300,
) -> dict[str, Any]:
    """Return one-to-one event matches and detection/inventory metrics.

    A finding can match at most one labelled scenario. Matching requires issue
    type and entity identity, compatible entity type and zone when supplied,
    plus overlap of the expected and detected event windows. Extra detections
    are false positives; labelled scenarios with no match are false negatives.
    """
    if time_tolerance_seconds < 0:
        raise EvaluationError("time_tolerance_seconds cannot be negative.")
    if not isinstance(findings, list) or not isinstance(ground_truth, list):
        raise EvaluationError("findings and ground_truth must both be lists.")
    if not ground_truth:
        raise EvaluationError("Ground truth is empty; metrics would be misleading.")

    indexed_findings = list(enumerate(findings))
    unmatched_finding_indexes = set(range(len(findings)))
    matches: list[dict[str, Any]] = []
    unmatched_scenarios: list[dict[str, Any]] = []

    # Stable ordering makes matching and the resulting JSON reproducible.
    ordered_scenarios = sorted(
        enumerate(ground_truth),
        key=lambda item: (item[1].get("scenario_id", ""), item[0]),
    )

    for scenario_index, scenario in ordered_scenarios:
        candidates = []
        for finding_index, finding in indexed_findings:
            if finding_index not in unmatched_finding_indexes:
                continue
            if _matches(
                finding,
                scenario,
                time_tolerance_seconds=time_tolerance_seconds,
            ):
                actual_start = _parse_timestamp(
                    finding.get("event_start_at", finding.get("start_at"))
                )
                expected_start = _parse_timestamp(scenario.get("event_start_at"))
                start_delta = (
                    abs((actual_start - expected_start).total_seconds())
                    if actual_start is not None and expected_start is not None
                    else float("inf")
                )
                candidates.append((start_delta, finding_index, finding))

        if not candidates:
            unmatched_scenarios.append({
                "scenario_id": scenario.get("scenario_id"),
                "issue_type": scenario.get("issue_type"),
                "entity_id": scenario.get("entity_id"),
            })
            continue

        _, finding_index, finding = min(candidates, key=lambda item: (item[0], item[1]))
        unmatched_finding_indexes.remove(finding_index)
        matches.append({
            "scenario_id": scenario.get("scenario_id"),
            "finding_index": finding_index,
            "issue_type": scenario.get("issue_type"),
            "entity_id": scenario.get("entity_id"),
            "expected_severity": scenario.get("expected_severity"),
            "detected_severity": finding.get("severity"),
            "severity_match": scenario.get("expected_severity") == finding.get("severity"),
            "expected_affected_lot_ids": sorted(_parse_lot_ids(
                scenario.get("expected_affected_lot_ids")
            )),
            "detected_affected_lot_ids": sorted(_parse_lot_ids(
                finding.get("affected_lot_ids")
            )),
        })

    false_positives = [
        {
            "finding_index": index,
            "issue_type": findings[index].get("issue_type"),
            "entity_id": findings[index].get("entity_id"),
            "zone_id": findings[index].get("zone_id"),
            "event_start_at": findings[index].get("event_start_at"),
            "event_end_at": findings[index].get("event_end_at"),
        }
        for index in sorted(unmatched_finding_indexes)
    ]

    tp = len(matches)
    fp = len(false_positives)
    fn = len(unmatched_scenarios)

    issue_types = sorted(
        {item.get("issue_type", "") for item in ground_truth}
        | {item.get("issue_type", "") for item in findings}
    )
    per_issue_type = {}
    for issue_type in issue_types:
        issue_tp = sum(item["issue_type"] == issue_type for item in matches)
        issue_fp = sum(item["issue_type"] == issue_type for item in false_positives)
        issue_fn = sum(item["issue_type"] == issue_type for item in unmatched_scenarios)
        per_issue_type[issue_type] = _classification_metrics(issue_tp, issue_fp, issue_fn)

    expected_lot_pairs: set[tuple[str, str]] = set()
    detected_lot_pairs: set[tuple[str, str]] = set()
    for item in matches:
        scenario_id = str(item["scenario_id"])
        expected_lot_pairs.update(
            (scenario_id, lot_id) for lot_id in item["expected_affected_lot_ids"]
        )
        detected_lot_pairs.update(
            (scenario_id, lot_id) for lot_id in item["detected_affected_lot_ids"]
        )

    matched_scenario_ids = {item["scenario_id"] for item in matches}
    for scenario in unmatched_scenarios:
        source = next(
            row for row in ground_truth
            if row.get("scenario_id") == scenario.get("scenario_id")
        )
        expected_lot_pairs.update(
            (str(scenario.get("scenario_id")), lot_id)
            for lot_id in _parse_lot_ids(source.get("expected_affected_lot_ids"))
        )
    for item in false_positives:
        finding = findings[item["finding_index"]]
        detected_lot_pairs.update(
            (f"UNMATCHED_FINDING_{item['finding_index']}", lot_id)
            for lot_id in _parse_lot_ids(finding.get("affected_lot_ids"))
        )

    lot_tp = len(expected_lot_pairs & detected_lot_pairs)
    lot_fp = len(detected_lot_pairs - expected_lot_pairs)
    lot_fn = len(expected_lot_pairs - detected_lot_pairs)
    severity_matches = sum(item["severity_match"] for item in matches)

    return {
        "evaluation_mode": "POST_RUN_GROUND_TRUTH_COMPARISON",
        "ground_truth_used_for_detection": False,
        "matching_policy": (
            "One-to-one match on issue type, entity ID, entity type and zone when "
            "available, with event-window overlap (or configured start-time tolerance)."
        ),
        "time_tolerance_seconds": time_tolerance_seconds,
        "scenario_count": len(ground_truth),
        "finding_count": len(findings),
        "matched_scenarios": tp,
        "unmatched_scenarios": fn,
        "unmatched_findings": fp,
        "metrics": {
            **_classification_metrics(tp, fp, fn),
            "severity_accuracy": _ratio(severity_matches, tp),
            "severity_matches": severity_matches,
            "affected_lot_links": _classification_metrics(lot_tp, lot_fp, lot_fn),
        },
        "per_issue_type": per_issue_type,
        "matches": matches,
        "false_positives": false_positives,
        "false_negatives": unmatched_scenarios,
        "metric_limitations": [
            "No true-negative denominator is defined by the labelled-scenario file; accuracy and specificity are therefore not reported.",
            "Affected-lot link metrics evaluate expected lot associations in the labelled scenarios, not physical product damage.",
            "A match establishes agreement with the controlled synthetic scenario, not proof of real-world detector performance.",
        ],
    }
