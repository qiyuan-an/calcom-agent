import os
from unittest.mock import patch

import pytest

from calcom_agent.config import ConfigError, Settings, load_settings


def test_explicit_env_file_environment_precedence_and_secret_repr(tmp_path):
    file = tmp_path / "synthetic.env"
    file.write_text('CAL_API_KEY="synthetic-key"\nCAL_USERNAME=demo\nOLLAMA_MODEL=qwen3:8b\n')
    with patch.dict(os.environ, {"OLLAMA_MODEL": "qwen3:14b"}, clear=True):
        before = dict(os.environ)
        settings = load_settings(file)
        assert settings.cal_api_key == "synthetic-key"
        assert settings.cal_username == "demo"
        assert settings.ollama_model == "qwen3:14b"
        assert settings.ollama_base_url == "http://127.0.0.1:11434"
        assert settings.default_timezone == "America/Los_Angeles"
        assert "synthetic-key" not in repr(settings)
        assert dict(os.environ) == before


@pytest.mark.parametrize("override,field", [
    ({"cal_api_key": ""}, "CAL_API_KEY"),
    ({"cal_username": ""}, "CAL_USERNAME"),
    ({"ollama_model": ""}, "OLLAMA_MODEL"),
    ({"default_timezone": "Invalid/Zone"}, "DEFAULT_TIMEZONE"),
    ({"ollama_base_url": "http://user:synthetic-secret@localhost"}, "OLLAMA_BASE_URL"),
    ({"ollama_base_url": "file:///tmp/model"}, "OLLAMA_BASE_URL"),
])
def test_invalid_config_errors_expose_only_field_names(override, field):
    args = {"cal_api_key": "synthetic-key", "cal_username": "demo", **override}
    with pytest.raises(ConfigError) as exc:
        Settings(**args)
    assert field in str(exc.value)
    assert "synthetic" not in str(exc.value)


def test_missing_config_and_no_interpolation(tmp_path):
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ConfigError):
        load_settings(None)
    file = tmp_path / "synthetic.env"
    file.write_text('CAL_API_KEY=${UNRELATED_SECRET}\nCAL_USERNAME=demo\n')
    with patch.dict(os.environ, {"UNRELATED_SECRET": "must-not-substitute"}, clear=True):
        assert load_settings(file).cal_api_key == "${UNRELATED_SECRET}"
        assert load_settings(file).ollama_model == "qwen3:30b-a3b"


def test_model_timeout_configuration_is_validated_without_values(tmp_path):
    file = tmp_path / 'synthetic.env'
    file.write_text('CAL_API_KEY=synthetic\nCAL_USERNAME=demo\nOLLAMA_TIMEOUT_SECONDS=180\n')
    with patch.dict(os.environ, {}, clear=True):
        assert load_settings(file).ollama_timeout_seconds == 180
        for value in ('nan', 'inf', '0', '601', 'synthetic-secret'):
            with patch.dict(os.environ, {'OLLAMA_TIMEOUT_SECONDS': value}):
                with pytest.raises(ConfigError) as exc:
                    load_settings(file)
                assert 'OLLAMA_TIMEOUT_SECONDS' in str(exc.value)
                assert 'synthetic-secret' not in str(exc.value)
