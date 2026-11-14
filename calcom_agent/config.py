"""Explicit configuration loading; never load credentials at import time."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import math

from dotenv import dotenv_values


class ConfigError(ValueError):
    """Configuration error safe to display without configuration values."""


@dataclass(frozen=True)
class Settings:
    cal_api_key: str = field(repr=False)
    cal_username: str
    ollama_model: str = "qwen3:30b-a3b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    default_timezone: str = "America/Los_Angeles"
    ollama_timeout_seconds: float = 120

    def __post_init__(self):
        if (type(self.ollama_timeout_seconds) not in (int, float)
                or not math.isfinite(self.ollama_timeout_seconds)
                or not 1 <= self.ollama_timeout_seconds <= 600):
            raise ConfigError("Invalid OLLAMA_TIMEOUT_SECONDS (expected 1–600)")
        for name in ("cal_api_key", "cal_username", "ollama_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
                raise ConfigError(f"Missing or invalid {name.upper()}")
        try:
            url = urlsplit(self.ollama_base_url)
            valid = (url.scheme in {"http", "https"} and url.hostname
                     and not url.username and not url.password and not url.query
                     and not url.fragment)
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise ConfigError("Invalid OLLAMA_BASE_URL")
        try:
            ZoneInfo(self.default_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ConfigError("Invalid DEFAULT_TIMEZONE") from None


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Read only the explicit file; process environment overrides it.

    Interpolation is disabled to avoid substituting unrelated environment secrets.
    This function does not mutate os.environ or log the file's contents.
    """
    values = {} if env_file is None else dotenv_values(env_file, interpolate=False)
    values.update(os.environ)
    names = {
        "cal_api_key": "CAL_API_KEY", "cal_username": "CAL_USERNAME",
        "ollama_model": "OLLAMA_MODEL", "ollama_base_url": "OLLAMA_BASE_URL",
        "default_timezone": "DEFAULT_TIMEZONE",
    }
    args = {attr: values[key] for attr, key in names.items() if key in values}
    for attr in ("cal_api_key", "cal_username"):
        args.setdefault(attr, "")
    if "OLLAMA_TIMEOUT_SECONDS" in values:
        try:
            args["ollama_timeout_seconds"] = float(values["OLLAMA_TIMEOUT_SECONDS"])
        except (TypeError, ValueError):
            raise ConfigError("Invalid OLLAMA_TIMEOUT_SECONDS (expected 1–600)") from None
    return Settings(**args)
