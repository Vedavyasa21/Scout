"""Configuration models and loading.

Secrets live only in the environment / `.env` file. They are never written to
logs, the database, prompts or error messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from platformdirs import user_data_path, user_log_path
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

APP_NAME = "scout"

DEFAULT_ALLOWED_APPS: tuple[str, ...] = (
    "notepad",
    "calc",
    "mspaint",
    "explorer",
    "code",
    "gnome-calculator",
    "xdg-open",
    "gedit",
)


def _split_csv(value: object) -> object:
    """Accept comma-separated strings for list fields."""
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    """Validated application settings loaded from env / `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="SCOUT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    provider: str = "gemini"
    api_key: str = ""
    model: str = "gemini-3.5-flash"

    max_tool_steps: int = Field(default=8, ge=1, le=40)
    tool_timeout: float = Field(default=60.0, gt=0, le=600)
    request_timeout: float = Field(default=120.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)

    workspaces: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    allowed_apps: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_APPS)
    )

    log_level: str = "INFO"
    log_file: Path | None = None

    @field_validator("workspaces", "allowed_apps", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @model_validator(mode="after")
    def _default_workspaces(self) -> Settings:
        if not self.workspaces:
            self.workspaces = [Path.home()]
        resolved: list[Path] = []
        for workspace in self.workspaces:
            path = workspace.expanduser()
            try:
                resolved.append(path.resolve(strict=True))
            except OSError as exc:
                raise ValueError(f"workspace does not exist: {path}") from exc
            if not path.is_dir():
                raise ValueError(f"workspace is not a directory: {path}")
        self.workspaces = resolved
        return self

    @property
    def data_dir(self) -> Path:
        path = user_data_path(APP_NAME, appauthor=False)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_log_file(self) -> Path:
        if self.log_file:
            path = self.log_file.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        path = user_log_path(APP_NAME, appauthor=False) / "scout.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def require_api_key(self) -> str:
        if not self.api_key.strip():
            raise MissingCredentialError(
                "No API key configured. Set SCOUT_API_KEY in your environment or "
                "in the project `.env` file (see .env.example)."
            )
        return self.api_key.strip()


class ConfigurationError(Exception):
    """Raised for user-facing configuration problems."""


class MissingCredentialError(ConfigurationError):
    """Raised when a required credential is absent. Never carries the value."""


def load_settings(**overrides: object) -> Settings:
    """Load settings, translating pydantic errors into a friendly exception."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except Exception as exc:  # pydantic ValidationError or ValueError
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
