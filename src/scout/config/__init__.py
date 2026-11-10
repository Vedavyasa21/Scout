"""Configuration models and loading.

Secrets live only in the environment / `.env` file. They are never written to
logs, the database, prompts or error messages.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Annotated

from platformdirs import user_config_path, user_data_path, user_log_path
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

# Whole screenshots are refused when the focused window looks sensitive.
DEFAULT_SCREENSHOT_BLOCKED_TITLES: tuple[str, ...] = (
    "1password",
    "bitwarden",
    "keepass",
    "lastpass",
    "dashlane",
    "authenticator",
    "password manager",
    "online banking",
    "bank account",
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
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    provider: str = "gemini"
    api_key: str = ""
    # Flash-lite answers tool-call turns in ~1s; larger Flash models take 9-70s.
    model: str = "gemini-3.5-flash-lite"
    # -1 leaves the provider default. 0 disables thinking on models that allow it.
    thinking_budget: int = Field(default=-1, ge=-1, le=32768)
    # Cheaper than a numeric budget and supported by Gemini 3.5 flash-lite.
    # Empty string leaves the provider default.
    thinking_level: str = "minimal"

    max_tool_steps: int = Field(default=8, ge=1, le=40)
    tool_timeout: float = Field(default=60.0, gt=0, le=600)
    request_timeout: float = Field(default=120.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)

    workspaces: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    allowed_apps: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_APPS)
    )

    voice_model: str = ""  # empty = use `model`
    voice_max_seconds: float = Field(default=15.0, gt=1.0, le=60.0)
    voice_silence_seconds: float = Field(default=1.2, gt=0.3, le=5.0)
    overlay_opacity: float = Field(default=0.65, ge=0.3, le=1.0)
    overlay_label: str = Field(default="scout", max_length=60)
    full_access: bool = Field(
        default=False,
        description=(
            "When true, bypasses manual tool approval checks. Disabled by default; "
            "enable only for fully trusted, non-interactive automation."
        ),
    )
    # Hard-deny wins over full access; auto-approve skips the prompt only.
    denied_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)
    auto_approve_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)
    egress_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    audit_enabled: bool = True
    audit_file: Path | None = None

    screenshot_blocked_titles: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_SCREENSHOT_BLOCKED_TITLES)
    )
    screenshot_retention_days: int = Field(default=7, ge=0, le=365)
    screenshot_max_files: int = Field(default=50, ge=0, le=1000)

    # Cloud wake-word audio is opt-in; hotkey/custom local detectors still work.
    wake_cloud_fallback: bool = False

    log_level: str = "INFO"
    log_file: Path | None = None

    @field_validator(
        "workspaces",
        "allowed_apps",
        "denied_tools",
        "auto_approve_tools",
        "egress_allowlist",
        "screenshot_blocked_titles",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("thinking_level")
    @classmethod
    def _check_thinking_level(cls, value: str) -> str:
        level = value.strip().lower()
        allowed = {"", "minimal", "low", "medium", "high"}
        if level not in allowed:
            raise ValueError(f"thinking_level must be one of {sorted(allowed)}")
        return level

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

    @property
    def resolved_audit_file(self) -> Path:
        """Durable approval/tool ledger, separate from rotating diagnostics."""
        if self.audit_file:
            path = self.audit_file.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        return self.data_dir / "audit.jsonl"

    @property
    def transcription_model(self) -> str:
        return self.voice_model.strip() or self.model

    def require_api_key(self) -> str:
        if not self.api_key.strip():
            raise MissingCredentialError(
                "No API key configured. Run `scout init` to store your own Gemini "
                "key in Windows Credential Manager (or the per-user config), or set "
                "SCOUT_API_KEY in the environment."
            )
        return self.api_key.strip()


class ConfigurationError(Exception):
    """Raised for user-facing configuration problems."""


class MissingCredentialError(ConfigurationError):
    """Raised when a required credential is absent. Never carries the value."""


def global_env_file() -> Path:
    """Per-user `.env` (outside the repo) so an installed Scout works from anywhere."""
    return user_config_path(APP_NAME, appauthor=False) / ".env"


def env_files() -> tuple[str, str]:
    """Config files in increasing priority: user config, then project `.env`."""
    return str(global_env_file()), ".env"


def _remove_persisted_key(path: Path) -> None:
    """Drop a plaintext key after it has been moved to Credential Manager."""
    try:
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if line.split("=", 1)[0].strip() != "SCOUT_API_KEY"]
        path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    except OSError:
        return


def save_api_key(api_key: str, path: Path | None = None) -> Path | None:
    """Store the key securely, returning the file path or None for Credential Manager."""
    key = api_key.strip()
    if not key or any(char.isspace() for char in key):
        raise ConfigurationError("The API key must be a single value without spaces.")
    if path is None:
        from scout.security.credentials import save_credential

        if save_credential(key):
            _remove_persisted_key(global_env_file())
            return None
    target = path or global_env_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        setting = "SCOUT_API_KEY"
        for index, line in enumerate(lines):
            name = line.split("=", 1)[0].strip()
            if name == setting:
                lines[index] = f"{setting}={key}"
                break
        else:
            lines.append(f"{setting}={key}")
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(target, 0o600)
    except OSError as exc:
        raise ConfigurationError(f"Could not save the API key to {target}: {exc}") from exc
    return target


def load_settings(**overrides: object) -> Settings:
    """Load settings from env, `.env`, then Credential Manager."""
    try:
        settings = Settings(_env_file=env_files(), **overrides)  # type: ignore[arg-type]
    except Exception as exc:  # pydantic ValidationError or ValueError
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
    if not settings.api_key.strip():
        from scout.security.credentials import load_credential

        stored = load_credential()
        if stored:
            settings = settings.model_copy(update={"api_key": stored})
    return settings
