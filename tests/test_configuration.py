from coldwatch.config import load_settings


def test_default_configuration_loads():
    settings = load_settings()
    assert settings["project"]["name"] == "ColdWatch AI"
    assert settings["project"]["random_seed"] == 20261022
    assert settings["generation"]["profile"] == "starter"


def test_starter_and_full_profiles_match_challenge_volumes():
    profiles = load_settings()["profiles"]
    assert profiles["starter"] == {
        "warehouses": 2,
        "zones": 10,
        "sensors": 50,
        "sensor_readings": 10000,
        "inventory_lots": 500,
    }
    assert profiles["full"] == {
        "warehouses": 10,
        "zones": 100,
        "sensors": 500,
        "sensor_readings": 1000000,
        "inventory_lots": 10000,
    }
