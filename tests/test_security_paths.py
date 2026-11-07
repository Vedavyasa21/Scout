"""Workspace confinement: traversal and symlink escape protection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.security import (
    PathAccessError,
    ensure_existing_file,
    ensure_within_workspaces,
    redact_secrets,
)


def test_path_inside_workspace_is_allowed(workspace: Path) -> None:
    target = workspace / "notes.txt"
    target.write_text("hello")
    assert ensure_existing_file(target, [workspace]) == target.resolve()


def test_parent_traversal_is_rejected(workspace: Path) -> None:
    outside = workspace.parent / "secret.txt"
    outside.write_text("secret")
    with pytest.raises(PathAccessError):
        ensure_within_workspaces(workspace / ".." / "secret.txt", [workspace])


def test_absolute_outside_path_is_rejected(workspace: Path, tmp_path: Path) -> None:
    with pytest.raises(PathAccessError):
        ensure_within_workspaces(tmp_path / "other", [workspace])


def test_missing_file_raises(workspace: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ensure_existing_file(workspace / "missing.txt", [workspace])


def test_symlink_escape_is_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("leak")
    link = workspace / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this system")
    with pytest.raises(PathAccessError):
        ensure_within_workspaces(link / "leak.txt", [workspace])


def test_redact_secrets_removes_values() -> None:
    text = "request failed with key sk-1234567890"
    assert "sk-1234567890" not in redact_secrets(text, ["sk-1234567890"])
    assert redact_secrets(text, [None, ""]) == text
