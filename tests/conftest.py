"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.config import Settings


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def settings(workspace: Path, tmp_path: Path) -> Settings:
    return Settings(
        api_key="test-key-not-real",
        workspaces=[workspace],
        allowed_apps=["notepad", "calc"],
        log_file=tmp_path / "scout-test.log",
        audit_file=tmp_path / "audit.jsonl",
        max_tool_steps=4,
        full_access=False,
    )
