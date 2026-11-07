"""Application allowlisting and launch resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.tools import ToolContext
from scout.tools.applications import (
    AppNotAllowedError,
    AppNotFoundError,
    is_application_installed,
    launch_application,
    resolve_application,
)


def test_non_allowlisted_app_is_rejected() -> None:
    with pytest.raises(AppNotAllowedError, match="not on the application allowlist"):
        resolve_application("evil.exe", ["notepad"])


def test_allowlist_reports_available_names() -> None:
    with pytest.raises(AppNotAllowedError) as excinfo:
        resolve_application("evil", ["notepad", "calc"])
    assert "notepad" in str(excinfo.value)


def test_absolute_path_must_be_allowlisted(tmp_path: Path) -> None:
    fake = tmp_path / "tool.exe"
    fake.write_text("")
    with pytest.raises(AppNotAllowedError):
        resolve_application(str(fake), ["notepad"])


def test_absolute_allowlisted_path_resolves(tmp_path: Path) -> None:
    fake = tmp_path / "tool.exe"
    fake.write_text("")
    assert resolve_application(str(fake), [str(fake)]) == fake


def test_missing_allowlisted_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "gone.exe"
    with pytest.raises(AppNotFoundError):
        resolve_application(str(missing), [str(missing)])


def test_launch_of_disallowed_app_is_refused(settings) -> None:
    result = launch_application({"application": "evil"}, ToolContext.from_settings(settings))
    assert not result.ok
    assert "allowlist" in (result.error or "")


def test_installed_probe_for_disallowed_app(settings) -> None:
    result = is_application_installed({"application": "evil"}, ToolContext.from_settings(settings))
    assert result.ok
    assert result.data["allowlisted"] is False
    assert result.data["installed"] is None


def test_installed_probe_for_missing_allowlisted_app(settings) -> None:
    settings.allowed_apps = ["definitely-not-installed-xyz"]
    result = is_application_installed(
        {"application": "definitely-not-installed-xyz"},
        ToolContext.from_settings(settings),
    )
    assert result.ok
    assert result.data == {
        "application": "definitely-not-installed-xyz",
        "allowlisted": True,
        "installed": False,
    }
