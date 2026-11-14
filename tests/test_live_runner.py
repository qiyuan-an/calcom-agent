from unittest.mock import patch

import pytest

from calcom_agent.config import ConfigError
from scripts.check_live_lifecycle import main


@pytest.mark.parametrize("message", ["Missing or invalid CAL_API_KEY", "Missing or invalid CAL_USERNAME",
                                    "Invalid DEFAULT_TIMEZONE", "Invalid OLLAMA_BASE_URL"])
def test_configuration_failure_identifies_setting_without_starting_api(message, capsys):
    with patch("builtins.input", return_value="ready"), \
         patch("scripts.check_live_lifecycle.load_settings", side_effect=ConfigError(message)), \
         patch("scripts.check_live_lifecycle.CalcomClient") as client:
        main()
    output = capsys.readouterr().out
    assert f"Configuration needed: {message}" in output
    assert "environment variables override" in output
    assert "No API requests or booking changes were made" in output
    assert "clean up any created" not in output
    client.assert_not_called()
