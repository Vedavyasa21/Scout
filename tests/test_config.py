"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.config import ConfigurationError, MissingCredentialError, Settings, load_settings


def test_defaults_use_home_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_WORKSPACES", raising=False)
    settings = Settings(api_key="k")
    assert settings.workspaces == [Path.home().resolve()]
    assert settings.provider == "gemini"
    assert settings.max_tool_steps == 8


def test_csv_lists_are_parsed(workspace: Path) -> None:
    settings = Settings(
        api_key="k",
        workspaces=f"{workspace}, {workspace.parent}",
        allowed_apps="notepad, calc",
    )
    assert len(settings.workspaces) == 2
    assert settings.allowed_apps == ["notepad", "calc"]


def test_phase2_csv_lists_are_parsed(workspace: Path) -> None:
    settings = Settings(
        api_key="k",
        workspaces=[workspace],
        denied_tools="lock_screen, type_text",
        auto_approve_tools="set_volume",
        egress_allowlist="example.com, docs.example.com",
        screenshot_blocked_titles="vault, bank",
    )
    assert settings.denied_tools == ["lock_screen", "type_text"]
    assert settings.auto_approve_tools == ["set_volume"]
    assert settings.egress_allowlist == ["example.com", "docs.example.com"]
    assert settings.screenshot_blocked_titles == ["vault", "bank"]


def test_missing_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        Settings(api_key="k", workspaces=[tmp_path / "nope"])


def test_file_is_not_a_workspace(tmp_path: Path) -> None:
    file = tmp_path / "file.txt"
    file.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        Settings(api_key="k", workspaces=[file])


def test_invalid_log_level() -> None:
    with pytest.raises(ValueError, match="log_level"):
        Settings(api_key="k", log_level="LOUD")


def test_step_limit_bounds() -> None:
    with pytest.raises(ValueError):
        Settings(api_key="k", max_tool_steps=0)
    with pytest.raises(ValueError):
        Settings(api_key="k", max_tool_steps=1000)


def test_missing_api_key_raises_without_echoing_it() -> None:
    settings = Settings(api_key="   ")
    with pytest.raises(MissingCredentialError) as excinfo:
        settings.require_api_key()
    assert "SCOUT_API_KEY" in str(excinfo.value)


def test_load_settings_wraps_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(api_key="k", workspaces=[tmp_path / "missing"])
