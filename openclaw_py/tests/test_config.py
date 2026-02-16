import os
import json
import pytest
from openclaw_py.src.config.config import load_config, OpenClawConfig, GatewayConfig

def test_load_config_defaults():
    """Test that default configuration is loaded when no env/file is present."""
    # Ensure OPENCLAW_CONFIG_PATH is not set to a real file for this test
    if "OPENCLAW_CONFIG_PATH" in os.environ:
        del os.environ["OPENCLAW_CONFIG_PATH"]

    config = load_config()
    assert isinstance(config, OpenClawConfig)
    assert config.gateway.port == 18789
    assert config.gateway.mode == "local"

def test_load_config_from_file(tmp_path):
    """Test loading configuration from a temporary JSON5 file."""
    config_file = tmp_path / "test_config.json"
    config_data = {
        "gateway": {
            "port": 9999,
            "mode": "remote",
            # Comments are supported in JSON5
            "bind": "0.0.0.0"
        },
        "logging": {
            "level": "debug"
        }
    }

    with open(config_file, "w") as f:
        json.dump(config_data, f) # Standard JSON is valid JSON5

    os.environ["OPENCLAW_CONFIG_PATH"] = str(config_file)

    try:
        config = load_config()
        assert config.gateway.port == 9999
        assert config.gateway.mode == "remote"
        assert config.gateway.bind == "0.0.0.0"
        assert config.logging.level == "debug"
    finally:
        del os.environ["OPENCLAW_CONFIG_PATH"]

def test_config_validation():
    """Test that invalid config types raise validation errors (handled gracefully)."""
    # Pydantic validation happens on instantiation.
    # Our load_config catches exceptions and returns a default config in case of error.
    # Let's test the Pydantic model directly for strict validation.

    with pytest.raises(Exception):
        GatewayConfig(port="not-a-number")
