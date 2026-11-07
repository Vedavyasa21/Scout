"""Filesystem tools: workspace confinement and read-only behaviour."""

from __future__ import annotations

from pathlib import Path

from scout.tools import ToolContext
from scout.tools.filesystem import list_directory, read_text_file, search_files


def _ctx(settings) -> ToolContext:
    return ToolContext.from_settings(settings)


def test_search_finds_file(workspace: Path, settings) -> None:
    (workspace / "gem5-project").mkdir()
    (workspace / "notes.md").write_text("hello")
    result = search_files({"query": "gem5"}, _ctx(settings))
    assert result.ok
    assert [match["name"] for match in result.data["matches"]] == ["gem5-project"]


def test_search_includes_directories_when_asked(workspace: Path, settings) -> None:
    (workspace / "research").mkdir()
    result = search_files({"query": "research", "include_directories": True}, _ctx(settings))
    assert result.ok
    assert result.data["matches"][0]["type"] == "directory"


def test_search_outside_workspace_is_denied(tmp_path: Path, settings) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    result = search_files({"query": "x", "root": str(outside)}, _ctx(settings))
    assert not result.ok
    assert "outside the approved workspaces" in (result.error or "")


def test_search_respects_limit(workspace: Path, settings) -> None:
    for index in range(10):
        (workspace / f"match-{index}.txt").write_text("x")
    result = search_files({"query": "match", "max_results": 3}, _ctx(settings))
    assert result.ok
    assert len(result.data["matches"]) == 3
    assert result.data["truncated"] is True


def test_list_directory(workspace: Path, settings) -> None:
    (workspace / "a.txt").write_text("a")
    (workspace / "sub").mkdir()
    result = list_directory({"path": str(workspace)}, _ctx(settings))
    assert result.ok
    names = {entry["name"] for entry in result.data["entries"]}
    assert names == {"a.txt", "sub"}


def test_hidden_entries_are_skipped_by_default(workspace: Path, settings) -> None:
    (workspace / ".secret").write_text("x")
    assert list_directory({"path": str(workspace)}, _ctx(settings)).data["entries"] == []
    included = list_directory({"path": str(workspace), "include_hidden": True}, _ctx(settings))
    assert included.data["entries"][0]["name"] == ".secret"


def test_read_text_file(workspace: Path, settings) -> None:
    target = workspace / "notes.md"
    target.write_text("line one\nline two")
    result = read_text_file({"path": str(target)}, _ctx(settings))
    assert result.ok
    assert "line two" in result.data["content"]
    assert result.data["truncated"] is False


def test_read_truncates_large_files(workspace: Path, settings) -> None:
    target = workspace / "big.txt"
    target.write_text("a" * 500)
    result = read_text_file({"path": str(target), "max_bytes": 100}, _ctx(settings))
    assert result.ok
    assert result.data["truncated"] is True
    assert len(result.data["content"]) == 100


def test_read_outside_workspace_is_denied(tmp_path: Path, settings) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    result = read_text_file({"path": str(outside)}, _ctx(settings))
    assert not result.ok
    assert "outside the approved workspaces" in (result.error or "")


def test_read_refuses_unsupported_extension(workspace: Path, settings) -> None:
    target = workspace / "image.exe"
    target.write_bytes(b"MZ")
    result = read_text_file({"path": str(target)}, _ctx(settings))
    assert not result.ok
    assert "Unsupported file type" in (result.error or "")


def test_read_refuses_binary_content(workspace: Path, settings) -> None:
    target = workspace / "binary.txt"
    target.write_bytes(b"abc\x00def")
    result = read_text_file({"path": str(target)}, _ctx(settings))
    assert not result.ok
    assert "binary" in (result.error or "").lower()


def test_read_directory_is_rejected(workspace: Path, settings) -> None:
    result = read_text_file({"path": str(workspace)}, _ctx(settings))
    assert not result.ok
