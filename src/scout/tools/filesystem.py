"""Read-only filesystem tools, restricted to approved workspaces."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout.security import PathAccessError, ensure_existing_directory, ensure_within_workspaces
from scout.tools.base import PermissionLevel, Tool, ToolContext, ToolRegistry, ToolResult

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        "site-packages",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "AppData",
        "Windows",
        "$Recycle.Bin",
        "System Volume Information",
    }
)

TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".rst",
        ".py",
        ".pyi",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".tsv",
        ".log",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".java",
        ".go",
        ".rs",
        ".sh",
        ".bash",
        ".ps1",
        ".bat",
        ".cmd",
        ".sql",
        ".xml",
        ".env",
        ".gitignore",
        ".dockerfile",
        ".gradle",
        ".m",
        ".r",
        ".jl",
    }
)

MAX_READ_BYTES = 1_000_000


def _default_root(ctx: ToolContext) -> Path:
    if not ctx.workspaces:
        raise PathAccessError("No workspaces are configured.")
    return ctx.workspaces[0]


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _matches(name: str, query: str, case_sensitive: bool) -> bool:
    haystack = name if case_sensitive else name.lower()
    needle = query if case_sensitive else query.lower()
    return needle in haystack


def search_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = args["query"]
    case_sensitive = bool(args.get("case_sensitive", False))
    max_results = int(args.get("max_results", 50))
    max_depth = int(args.get("max_depth", 8))
    include_dirs = bool(args.get("include_directories", True))

    try:
        root = ensure_existing_directory(
            args.get("root") or _default_root(ctx), list(ctx.workspaces)
        )
    except (PathAccessError, FileNotFoundError, NotADirectoryError) as exc:
        return ToolResult(ok=False, error=str(exc))

    matches: list[dict[str, Any]] = []
    scanned = 0
    root_depth = len(root.parts)

    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            if depth >= max_depth:
                dirs[:] = []
            else:
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            scanned += len(files)
            candidates = list(files)
            if include_dirs:
                candidates.extend(dirs)
            for name in candidates:
                if _matches(name, query, case_sensitive):
                    full = current_path / name
                    try:
                        stat = full.stat()
                    except OSError:
                        continue
                    matches.append(
                        {
                            "path": str(full),
                            "name": name,
                            "type": "directory" if full.is_dir() else "file",
                            "size_bytes": stat.st_size,
                            "modified_utc": _iso(stat.st_mtime),
                        }
                    )
                    if len(matches) >= max_results:
                        return ToolResult(
                            ok=True,
                            data={
                                "root": str(root),
                                "matches": matches,
                                "truncated": True,
                                "scanned_files": scanned,
                            },
                        )
    except PermissionError as exc:
        return ToolResult(ok=False, error=f"Permission denied while searching: {exc}")
    except OSError as exc:
        return ToolResult(ok=False, error=f"Search failed: {exc}")

    return ToolResult(
        ok=True,
        data={
            "root": str(root),
            "matches": matches,
            "truncated": False,
            "scanned_files": scanned,
        },
    )


def list_directory(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    include_hidden = bool(args.get("include_hidden", False))
    limit = int(args.get("limit", 200))
    try:
        path = ensure_existing_directory(
            args.get("path") or _default_root(ctx), list(ctx.workspaces)
        )
        entries: list[dict[str, Any]] = []
        truncated = False
        children = sorted(path.iterdir(), key=lambda child: (child.is_file(), child.name.lower()))
        for child in children:
            if not include_hidden and child.name.startswith("."):
                continue
            if len(entries) >= limit:
                truncated = True
                break
            try:
                stat = child.stat()
                entry_type = "directory" if child.is_dir() else "file"
            except OSError:
                continue
            entries.append(
                {
                    "name": child.name,
                    "type": entry_type,
                    "size_bytes": stat.st_size if entry_type == "file" else None,
                    "modified_utc": _iso(stat.st_mtime),
                }
            )
    except (PathAccessError, FileNotFoundError, NotADirectoryError) as exc:
        return ToolResult(ok=False, error=str(exc))
    except OSError as exc:
        return ToolResult(ok=False, error=f"Could not list directory: {exc}")

    return ToolResult(
        ok=True,
        data={"path": str(path), "entries": entries, "truncated": truncated},
    )


def read_text_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    max_bytes = int(args.get("max_bytes", 200_000))
    max_bytes = min(max(max_bytes, 1), MAX_READ_BYTES)
    try:
        path = ensure_within_workspaces(args["path"], list(ctx.workspaces))
    except PathAccessError as exc:
        return ToolResult(ok=False, error=str(exc))

    if not path.exists():
        return ToolResult(ok=False, error=f"File does not exist: {path}")
    if not path.is_file():
        return ToolResult(ok=False, error=f"Not a file: {path}")
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return ToolResult(
            ok=False,
            error=(
                f"Unsupported file type '{path.suffix or '(none)'}'. "
                f"Supported: {', '.join(sorted(TEXT_SUFFIXES))}"
            ),
        )
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        return ToolResult(ok=False, error=f"Could not read file: {exc}")

    if b"\x00" in raw[:8192]:
        return ToolResult(ok=False, error="File appears to be binary, refusing to read it")

    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return ToolResult(
        ok=True,
        data={
            "path": str(path),
            "size_bytes": size,
            "truncated": truncated,
            "content": text,
        },
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="search_files",
            description=(
                "Search for files or folders by name inside approved workspaces. "
                "Use this to locate projects, documents or folders."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Case-insensitive substring to match in file/folder names.",
                        "minLength": 1,
                        "maxLength": 200,
                    },
                    "root": {
                        "type": "string",
                        "description": "Directory to search in. Defaults to the first workspace.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum matches to return (default 50, max 200).",
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum directory depth (default 8, max 20).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_directories": {
                        "type": "boolean",
                        "description": "Also match directory names (default true).",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Match case-sensitively (default false).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=search_files,
            tags=("filesystem",),
        )
    )
    registry.register(
        Tool(
            name="list_directory",
            description="List the contents of a directory inside an approved workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list. Defaults to the first workspace.",
                    },
                    "include_hidden": {
                        "type": "boolean",
                        "description": "Include dot-files and dot-directories (default false).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum entries to return (default 200, max 500).",
                        "minimum": 1,
                        "maximum": 500,
                    },
                },
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=list_directory,
            tags=("filesystem",),
        )
    )
    registry.register(
        Tool(
            name="read_text_file",
            description=(
                "Read a text or source-code file inside an approved workspace. "
                "Refuses binary files and unsupported extensions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path or path relative to the working directory.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to read (default 200000, max 1000000).",
                        "minimum": 1,
                        "maximum": MAX_READ_BYTES,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=read_text_file,
            tags=("filesystem",),
        )
    )
