from copy import deepcopy

import pytest

from coldwatch.config import load_settings
from coldwatch.generators.master_data import (
    MasterDataGenerationError,
    build_master_data,
    validate_master_data_bundle,
)


def test_starter_master_data_counts_and_relationships():
    settings = load_settings()
    data = build_master_data(settings, profile="starter")
    counts = validate_master_data_bundle(data)

    assert counts["warehouse"] == 2
    assert counts["zone"] == 10
    assert counts["sensor"] == 50
    assert counts["storage_requirement"] == 6
    assert counts["inventory_lot"] == 500
    assert counts["sensor_zone_assignment"] == 50
    assert counts["lot_zone_assignment"] == 500


def test_master_data_is_deterministic_for_same_seed():
    settings = load_settings()
    first = build_master_data(settings, profile="starter", seed=20261022)
    second = build_master_data(settings, profile="starter", seed=20261022)
    assert first == second


def test_all_generated_sensor_ids_follow_zone_convention():
    data = build_master_data(profile="starter")
    zone_ids = {row["zone_id"] for row in data["zone"]}
    for sensor in data["sensor"]:
        assert any(sensor["sensor_id"].startswith(f"SNS-{zone_id}-") for zone_id in zone_ids)


def test_baseline_lots_are_assigned_only_to_allowed_zone_types():
    data = build_master_data(profile="starter")
    requirements = {
        row["requirement_id"]: {
            item["zone_type"]
            for item in data["requirement_allowed_zone_type"]
            if item["requirement_id"] == row["requirement_id"]
        }
        for row in data["storage_requirement"]
    }
    lots = {row["lot_id"]: row for row in data["inventory_lot"]}
    zone_types = {row["zone_id"]: row["zone_type"] for row in data["zone"]}

    for assignment in data["lot_zone_assignment"]:
        lot = lots[assignment["lot_id"]]
        assert zone_types[assignment["zone_id"]] in requirements[lot["requirement_id"]]


def test_validator_detects_incompatible_baseline_lot_assignment():
    data = build_master_data(profile="starter")
    broken = deepcopy(data)
    frozen_zone = next(row for row in broken["zone"] if row["zone_type"] == "FROZEN")
    ambient_lot = next(row for row in broken["inventory_lot"] if row["product_category"] == "AMBIENT_GOODS")
    assignment = next(row for row in broken["lot_zone_assignment"] if row["lot_id"] == ambient_lot["lot_id"])
    assignment["zone_id"] = frozen_zone["zone_id"]

    with pytest.raises(MasterDataGenerationError, match="incompatible zone"):
        validate_master_data_bundle(broken)


def test_unknown_profile_fails_clearly():
    with pytest.raises(MasterDataGenerationError, match="Unknown profile"):
        build_master_data(profile="enterprise")
