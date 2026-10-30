"""Configuration loading and lightweight validation for ColdWatch AI."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.toml"


class ConfigurationError(ValueError):
    """Raised when the project configuration is missing or invalid."""


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    """Load settings.toml and validate the fields needed by the scaffold."""
    settings_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    if not settings_path.is_file():
        raise ConfigurationError(f"Configuration file not found: {settings_path}")

    try:
        with settings_path.open("rb") as file:
            settings = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {settings_path}: {exc}") from exc

    project = settings.get("project")
    generation = settings.get("generation")
    profiles = settings.get("profiles")
    if not all(isinstance(item, dict) for item in (project, generation, profiles)):
        raise ConfigurationError("Expected project, generation, and profiles tables.")

    if not isinstance(project.get("random_seed"), int):
        raise ConfigurationError("project.random_seed must be an integer.")

    selected_profile = generation.get("profile")
    if selected_profile not in profiles:
        raise ConfigurationError(
            f"Unknown generation profile {selected_profile!r}; "
            f"available profiles: {', '.join(sorted(profiles))}."
        )

    required_counts = {
        "warehouses",
        "zones",
        "sensors",
        "sensor_readings",
        "inventory_lots",
    }
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict) or not required_counts.issubset(profile):
            raise ConfigurationError(
                f"Profile {profile_name!r} must define: {', '.join(sorted(required_counts))}."
            )
        if any(not isinstance(profile[key], int) or profile[key] <= 0 for key in required_counts):
            raise ConfigurationError(f"All counts in profile {profile_name!r} must be positive integers.")

    return settings
