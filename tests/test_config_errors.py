from pathlib import Path

import pytest

from coldwatch.config import ConfigurationError, load_settings


def test_missing_configuration_raises_clear_error(tmp_path: Path):
    missing_path = tmp_path / "missing.yaml"
    with pytest.raises(ConfigurationError, match="Configuration file not found"):
        load_settings(missing_path)
