from __future__ import annotations

from coldwatch.evaluation.risk_metrics import EvaluationError, evaluate_risk_findings


def _scenario(
    scenario_id: str,
    issue_type: str,
    entity_id: str,
    start: str,
    end: str,
    lots: list[str] | None = None,
) -> dict[str, str]:
    import json

    return {
        "scenario_id": scenario_id,
        "issue_type": issue_type,
        "entity_type": "SENSOR",
        "entity_id": entity_id,
        "expected_severity": "HIGH",
        "affected_zone_id": "ZONE-1",
        "event_start_at": start,
        "event_end_at": end,
        "expected_affected_lot_ids": json.dumps(lots or []),
    }


def _finding(
    issue_type: str,
    entity_id: str,
    start: str,
    end: str,
    lots: list[str] | None = None,
    severity: str = "HIGH",
) -> dict:
    return {
        "issue_type": issue_type,
        "entity_type": "SENSOR",
        "entity_id": entity_id,
        "zone_id": "ZONE-1",
        "severity": severity,
        "event_start_at": start,
        "event_end_at": end,
        "affected_lot_ids": lots or [],
    }


def test_evaluation_reports_tp_fp_fn_and_lot_link_quality():
    truth = [
        _scenario(
            "SCN-1", "TEMPERATURE_EXCURSION", "SNS-1",
            "2026-10-22T00:00:00Z", "2026-10-22T00:15:00Z", ["LOT-1"],
        ),
        _scenario(
            "SCN-2", "HUMIDITY_EXCURSION", "SNS-2",
            "2026-10-22T01:00:00Z", "2026-10-22T01:15:00Z", ["LOT-3"],
        ),
    ]
    findings = [
        _finding(
            "TEMPERATURE_EXCURSION", "SNS-1",
            "2026-10-22T00:05:00Z", "2026-10-22T00:10:00Z",
            ["LOT-1", "LOT-2"], severity="MEDIUM",
        ),
        # Duplicate detection of the same scenario must remain an FP.
        _finding(
            "TEMPERATURE_EXCURSION", "SNS-1",
            "2026-10-22T00:05:00Z", "2026-10-22T00:10:00Z",
            ["LOT-1"],
        ),
        _finding(
            "DOOR_OPEN_TOO_LONG", "DOOR-1",
            "2026-10-22T02:00:00Z", "2026-10-22T02:15:00Z",
        ),
    ]

    result = evaluate_risk_findings(findings, truth)
    metrics = result["metrics"]

    assert result["matched_scenarios"] == 1
    assert result["unmatched_scenarios"] == 1
    assert result["unmatched_findings"] == 2
    assert metrics["true_positives"] == 1
    assert metrics["false_positives"] == 2
    assert metrics["false_negatives"] == 1
    assert metrics["precision"] == 0.3333
    assert metrics["recall"] == 0.5
    assert metrics["f1_score"] == 0.4
    assert metrics["severity_accuracy"] == 0.0
    assert metrics["affected_lot_links"]["true_positives"] == 1
    assert metrics["affected_lot_links"]["false_positives"] == 1
    assert metrics["affected_lot_links"]["false_negatives"] == 1


def test_matching_requires_event_overlap_and_expected_zone():
    truth = [
        _scenario(
            "SCN-1", "TEMPERATURE_EXCURSION", "SNS-1",
            "2026-10-22T00:00:00Z", "2026-10-22T00:10:00Z",
        )
    ]
    wrong_time = _finding(
        "TEMPERATURE_EXCURSION", "SNS-1",
        "2026-10-22T01:00:00Z", "2026-10-22T01:10:00Z",
    )
    result = evaluate_risk_findings([wrong_time], truth)
    assert result["matched_scenarios"] == 0
    assert result["unmatched_scenarios"] == 1
    assert result["unmatched_findings"] == 1

    wrong_zone = _finding(
        "TEMPERATURE_EXCURSION", "SNS-1",
        "2026-10-22T00:01:00Z", "2026-10-22T00:08:00Z",
    )
    wrong_zone["zone_id"] = "ZONE-2"
    result = evaluate_risk_findings([wrong_zone], truth)
    assert result["matched_scenarios"] == 0


def test_evaluation_rejects_empty_ground_truth_and_bad_lot_json():
    try:
        evaluate_risk_findings([], [])
    except EvaluationError as exc:
        assert "Ground truth is empty" in str(exc)
    else:
        raise AssertionError("Expected EvaluationError for empty ground truth.")

    truth = [_scenario(
        "SCN-1", "TEMPERATURE_EXCURSION", "SNS-1",
        "2026-10-22T00:00:00Z", "2026-10-22T00:10:00Z",
    )]
    truth[0]["expected_affected_lot_ids"] = "not-json"
    finding = _finding(
        "TEMPERATURE_EXCURSION", "SNS-1",
        "2026-10-22T00:01:00Z", "2026-10-22T00:08:00Z",
    )
    try:
        evaluate_risk_findings([finding], truth)
    except EvaluationError as exc:
        assert "JSON array" in str(exc)
    else:
        raise AssertionError("Expected EvaluationError for invalid lot labels.")
